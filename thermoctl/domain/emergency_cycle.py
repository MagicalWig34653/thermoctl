"""Fixed-cycle and outdoor-curve phase timing for sensor-failure emergency operation.

Auftrag 6 of `lokal/plaene/0.11.0-notbetrieb.md`, backed by section 3.4 of
`lokal/konzepte/sensorausfall-notbetrieb.md`. This module answers exactly one
question, per on/off actuator, every control cycle: *given the time and the outdoor
reading, should this actuator be on or off right now, and until when does that
decision hold?* Nothing else -- no window/mode/override precedence (Auftrag 7's
`emergency_actuator_plan.py` builds the actual send plan from the vorrangtabelle in
plan section 1.4), no database, no models. A later task fills `CycleProfile` and
`OutdoorSample` from the DB rows another task is building in parallel
(`thermoctl/db/models/sensor_failure.py`), so this module only imports the standard
library.

Purity, exactly like `domain/pi_control.py`: every function takes its previous state
and every input as an argument and returns the new state and the decision as a value.
No clock, no globals, no mutation -- every dataclass is frozen. `advance()` is the one
entry point; `state=None` means "no emergency cycle running yet for this actuator"
and covers both the very first entry into emergency operation and, internally, a
"disturbed" forced restart (see below).

**A "pair"** is one Aus-phase followed by one Ein-phase (concept 3.4: "Erster
Eintritt beginnt mit Aus-Phase"). The pair's outdoor-derived values (which source,
which on/off duration) are pinned once, at the moment the Aus-phase that starts the
pair is (re-)resolved, and hold unchanged through both legs of that pair -- so a
source flipping between Kennlinie and Festtakt mid-phase, or an outdoor value that
moves, never aborts a running phase; it only takes effect at the *next* pair
boundary. This is deliberately the only place `advance()` re-evaluates the outdoor
sample and profile; everywhere else it just compares `now` against the already-fixed
`phase_deadline`.

**Warm-Aus-Hysterese:** the curve's own "echter Aus-Punkt" is the lowest outdoor
value at which its interpolation would give `on_seconds == 0` (unique per the
validation another module -- `sensor_failure_policy.py`, Auftrag 4 -- enforces; this
module does not itself validate the profile). Once that point is reached, `on` stays
pinned at 0 until the outdoor value drops back below `threshold -
warm_restart_hysteresis_k` -- read from the profile, never invented here -- so a
value oscillating right around the warm point does not flap between requesting Ein
and Aus every pair. The lock state (`CycleState.warm_locked`) is itself part of the
persisted state precisely so this band survives across calls.

**Disturbance handling (Neustart/Zeitrücksprung/lange Pause):** `advance()` treats a
loaded state as untrustworthy -- and restarts exactly like a fresh entry, Aus, full
profile Aus-Dauer, no credit for anything -- in two cases: the clock moved backwards
past the recorded phase start, or the gap since the phase's own deadline exceeds the
caller-supplied `CycleInput.stale_state_seconds`. Concept 3.4: "Bei Zeitrücksprung,
unplausibler Persistenz oder langer Prozesspause mit unklarem Gerät: Aus anfordern,
Störung kennzeichnen, volle ... Aus-Zeit ... abwarten." A state that is merely late
(deadline passed, but not by more than the stale threshold) is the ordinary "kein
Nachholen" case: the next phase starts fresh from the real `now`, neither backdated
to the missed deadline nor shortened -- see `test_late_cycle_does_not_extend_or_catch_up`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

PHASE_ON = "ein"
PHASE_OFF = "aus"

SOURCE_FIXED = "festtakt"
SOURCE_CURVE = "kennlinie"

# Reason codes -- diagnosis, read by people (Grundsatz 5); a caller persisting these
# keeps them stable.
REASON_ENTRY = "sensorausfall_takt_eintritt"
REASON_CONTINUE = "sensorausfall_takt_laeuft"
REASON_TRANSITION = "sensorausfall_takt_wechsel"
REASON_WARM_LOCK = "sensorausfall_takt_warmsperre"
REASON_DISTURBED = "sensorausfall_takt_gestoert"


@dataclass(frozen=True)
class CurvePoint:
    """One outdoor-curve stützpunkt: at `outdoor_c`, run `on_seconds` on, then
    `off_seconds` off. `on_seconds == 0` marks a genuine Aus-Punkt (concept 3.4:
    "Punkt bei 15 °C mit Ein=0 ist echtes Aus, niemals durch eine Mindest-Ein-Zeit
    zu Ein machen") -- `advance()` honours that by never promoting a `0` result."""

    outdoor_c: Decimal
    on_seconds: int
    off_seconds: int


@dataclass(frozen=True)
class CycleProfile:
    """A sensor-failure cycle profile -- anlagenweit or per-zone override, populated
    by a later task from `sensor_failure_profile`/`sensor_failure_curve_point`.

    `curve_points` empty means "no Kennlinie configured": every pair uses the fixed
    values unconditionally, regardless of outdoor usability. Order does not matter --
    `advance()` sorts by `outdoor_c` itself -- and validation (positive off-duration,
    at most one Aus-Punkt, non-increasing duty cycle with rising temperature, 1-120
    minute range) is deliberately not this module's job; it belongs to
    `sensor_failure_policy.py` (Auftrag 4), which owns *all* configuration
    correctness, not just this one profile's callers.
    """

    fixed_on_seconds: int
    fixed_off_seconds: int
    warm_restart_hysteresis_k: Decimal
    curve_points: tuple[CurvePoint, ...] = ()


@dataclass(frozen=True)
class OutdoorSample:
    """The outdoor reading as the caller already judged it -- `usable` is *not*
    recomputed here from `measured_at`. A later task derives it exactly the way
    `domain.fault.sensor_state`/`domain.outdoor.outdoor_reading` already judge
    every other temperature source (status `ok` and a numeric value), so this module
    does not duplicate that timeout policy or invent its own."""

    value_c: Decimal | None
    measured_at: datetime | None
    usable: bool


@dataclass(frozen=True)
class PriorPhaseHint:
    """What the caller already knows about the actuator's real state at the moment
    emergency cycling begins -- optional, because a caller (Auftrag 7) may not
    always have a trustworthy answer (Neustart, unklares Gerät). `on=True` credits
    the actuator's already-elapsed time against the zone's minimum-on duration
    (concept 3.4: "Ein bereits eingeschaltetes Relais darf zunächst seine
    verbleibende Mindest-Ein-Dauer erfüllen"); `on=False` credits it against the
    freshly resolved Aus-Dauer of the entry pair. Passing `None` to `CycleInput.prior`
    means "state unknown" -- concept 3.4: "bei unbekanntem Zustand Aus senden und
    volle Aus-Dauer warten"."""

    on: bool
    elapsed_seconds: int


@dataclass(frozen=True)
class CycleState:
    """The persisted state of one actuator's emergency cycle -- one row per
    zone/actuator assignment for a later task to store in
    `actuator_emergency_state`."""

    phase: str  # PHASE_ON | PHASE_OFF
    phase_started_at: datetime
    phase_deadline: datetime
    source: str  # SOURCE_FIXED | SOURCE_CURVE -- pinned for the whole current pair
    on_seconds: int  # this pair's Ein-Dauer, after minimum-duration clamping
    off_seconds: int  # this pair's Aus-Dauer, after minimum-duration clamping
    warm_locked: bool = False


@dataclass(frozen=True)
class CycleInput:
    """Everything one `advance()` call needs beyond the previous state."""

    now: datetime
    profile: CycleProfile
    outdoor: OutdoorSample
    min_on_seconds: int
    min_off_seconds: int
    # How long a control loop can go silent past a phase's own deadline before its
    # persisted state can no longer be trusted (concept 3.4's "lange Prozesspause
    # mit unklarem Gerät"). Deliberately not a constant here (Grundsatz 1) -- it
    # comes from the caller's own configuration (e.g. a multiple of the plant's
    # `shadow_interval_seconds`).
    stale_state_seconds: int
    # Only consulted on first entry (`state=None`) -- ignored on every later call,
    # and ignored entirely on a disturbed restart, which never credits anything.
    prior: PriorPhaseHint | None = None


@dataclass(frozen=True)
class CycleDecision:
    heating_requested: bool
    reason_code: str
    reason: str


@dataclass(frozen=True)
class CycleOutput:
    state: CycleState
    decision: CycleDecision


def _round_seconds(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _interpolate(points: tuple[CurvePoint, ...], outdoor_c: Decimal) -> tuple[int, int]:
    """Linear interpolation in `Decimal`, clamped at the edges, rounded to whole
    seconds with `ROUND_HALF_UP`. Never extrapolates (concept 3.4: "keine
    Extrapolation")."""
    ordered = sorted(points, key=lambda p: p.outdoor_c)
    # Mutation note (manual single-mutant pass, Auftrag 6): swapping either `<=`
    # below for `<` (or the matching `>=` for `>`) survives the test suite -- it is
    # an equivalent mutant, not a gap. At an exact endpoint the value is also the
    # first/last bracket's own edge, so the loop below reaches the identical
    # bracket and its fraction lands on exactly 0 or 1, reproducing the same
    # on/off pair either way. Confirmed by hand, not left unchecked.
    if outdoor_c <= ordered[0].outdoor_c:
        return ordered[0].on_seconds, ordered[0].off_seconds
    if outdoor_c >= ordered[-1].outdoor_c:
        return ordered[-1].on_seconds, ordered[-1].off_seconds
    # Mutation note: the bracket's lower `<=` mutated to `<` also survives, for the
    # same reason -- an outdoor value exactly equal to an interior stützpunkt is
    # already returned by the *previous* bracket's inclusive upper edge, in
    # iteration order, before this comparison is ever evaluated with that value as
    # its own lower edge (and the very first bracket's lower edge is unreachable
    # here at all, caught by the clamp above). Equivalent mutant, confirmed by hand.
    for lower, upper in zip(ordered, ordered[1:], strict=False):
        if lower.outdoor_c <= outdoor_c <= upper.outdoor_c:
            span = upper.outdoor_c - lower.outdoor_c
            fraction = (outdoor_c - lower.outdoor_c) / span
            on = Decimal(lower.on_seconds) + fraction * Decimal(upper.on_seconds - lower.on_seconds)
            off = Decimal(lower.off_seconds) + fraction * Decimal(
                upper.off_seconds - lower.off_seconds
            )
            return _round_seconds(on), _round_seconds(off)
    raise AssertionError(  # pragma: no cover -- outdoor_c is within [first, last] here
        "outdoor_c innerhalb der Randwerte, aber kein Intervall gefunden"
    )


def _warm_threshold(points: tuple[CurvePoint, ...]) -> Decimal | None:
    """The lowest outdoor value at which the curve gives a genuine Aus-Punkt
    (`on_seconds == 0`), or `None` if the curve never reaches one."""
    zero_points = [p.outdoor_c for p in points if p.on_seconds == 0]
    return min(zero_points) if zero_points else None


def _resolve_pair(
    profile: CycleProfile,
    outdoor: OutdoorSample,
    warm_locked_prev: bool,
    min_on_seconds: int,
    min_off_seconds: int,
) -> tuple[int, int, str, bool]:
    """Determine one pair's (on_seconds, off_seconds, source, warm_locked) -- called
    exactly once per pair, at the pair's own boundary (see module docstring)."""
    if not profile.curve_points or not outdoor.usable or outdoor.value_c is None:
        # No Kennlinie configured, or the outdoor source is unusable: Festtakt,
        # unconditionally. No warm lock applies while on Festtakt. The profile's
        # fixed durations are expected to already respect the zone's minimums
        # (that is `sensor_failure_policy.py`'s validation job, Auftrag 4) -- this
        # clamp is defense in depth, not the primary enforcement.
        fixed_on = max(profile.fixed_on_seconds, min_on_seconds)
        fixed_off = max(profile.fixed_off_seconds, min_off_seconds)
        return fixed_on, fixed_off, SOURCE_FIXED, False

    threshold = _warm_threshold(profile.curve_points)
    on_i, off_i = _interpolate(profile.curve_points, outdoor.value_c)
    is_warm_now = threshold is not None and outdoor.value_c >= threshold

    if warm_locked_prev:
        exits_band = threshold is not None and outdoor.value_c < (
            threshold - profile.warm_restart_hysteresis_k
        )
        locked = not exits_band
    else:
        locked = is_warm_now

    on_final = 0 if locked else on_i
    if on_final > 0:
        on_final = max(on_final, min_on_seconds)
    off_final = max(off_i, min_off_seconds)
    return on_final, off_final, SOURCE_CURVE, locked


def _explain(
    *,
    phase: str,
    on_seconds: int,
    off_seconds: int,
    source: str,
    outdoor: OutdoorSample,
    lead_in: str,
) -> str:
    source_text = "Außenkennlinie" if source == SOURCE_CURVE else "Festtakt"
    if outdoor.value_c is not None:
        brauchbarkeit = "brauchbar" if outdoor.usable else "unbrauchbar"
        outdoor_text = f"Außentemperatur {outdoor.value_c} °C ({brauchbarkeit})"
    else:
        outdoor_text = "keine Außentemperaturmessung"
    duration = on_seconds if phase == PHASE_ON else off_seconds
    phase_text = "Ein" if phase == PHASE_ON else "Aus"
    return (
        f"{lead_in} {phase_text}-Phase für {duration} s, Taktquelle {source_text}, "
        f"{outdoor_text}. Takt: {on_seconds} s Ein / {off_seconds} s Aus."
    )


def _is_disturbed(state: CycleState, now: datetime, stale_state_seconds: int) -> bool:
    if now < state.phase_started_at:
        return True
    overdue_s = (now - state.phase_deadline).total_seconds()
    return overdue_s > stale_state_seconds


def _enter(cycle: CycleInput, *, disturbed: bool) -> CycleOutput:
    on_s, off_s, source, warm = _resolve_pair(
        cycle.profile, cycle.outdoor, False, cycle.min_on_seconds, cycle.min_off_seconds
    )
    prior = None if disturbed else cycle.prior
    reason_code = REASON_DISTURBED if disturbed else REASON_ENTRY
    lead_in = (
        "Störung erkannt, Notbetriebstakt neu gestartet:"
        if disturbed
        else "Notbetriebstakt startet:"
    )

    if prior is not None and prior.on:
        remaining = max(0, cycle.min_on_seconds - prior.elapsed_seconds)
        started = cycle.now - timedelta(seconds=prior.elapsed_seconds)
        deadline = started + timedelta(seconds=cycle.min_on_seconds)
        state = CycleState(
            phase=PHASE_ON,
            phase_started_at=started,
            phase_deadline=deadline,
            source=source,
            on_seconds=cycle.min_on_seconds,
            off_seconds=off_s,
            warm_locked=warm,
        )
        reason = (
            f"Notbetriebstakt übernimmt eine bereits laufende Ein-Phase: verbleibende "
            f"Mindest-Ein-Dauer {remaining} s werden gehalten, danach Aus nach Takt "
            f"({'Außenkennlinie' if source == SOURCE_CURVE else 'Festtakt'})."
        )
        return CycleOutput(state, CycleDecision(True, reason_code, reason))

    elapsed = prior.elapsed_seconds if (prior is not None and not prior.on) else 0
    started = cycle.now - timedelta(seconds=elapsed)
    deadline = started + timedelta(seconds=off_s)
    state = CycleState(
        phase=PHASE_OFF,
        phase_started_at=started,
        phase_deadline=deadline,
        source=source,
        on_seconds=on_s,
        off_seconds=off_s,
        warm_locked=warm,
    )
    reason = _explain(
        phase=PHASE_OFF,
        on_seconds=on_s,
        off_seconds=off_s,
        source=source,
        outdoor=cycle.outdoor,
        lead_in=lead_in,
    )
    return CycleOutput(state, CycleDecision(False, reason_code, reason))


def advance(state: CycleState | None, cycle: CycleInput) -> CycleOutput:
    """One control-cycle step of the emergency actuator taktung.

    `state=None` is the first entry into emergency operation for this actuator.
    Every other call passes back the `state` this function itself returned last
    time; a distrusted state (clock regression, or a gap past
    `CycleInput.stale_state_seconds`) is handled internally as a disturbed restart,
    not by the caller.
    """
    if state is None:
        return _enter(cycle, disturbed=False)

    if _is_disturbed(state, cycle.now, cycle.stale_state_seconds):
        return _enter(cycle, disturbed=True)

    if cycle.now < state.phase_deadline:
        reason = _explain(
            phase=state.phase,
            on_seconds=state.on_seconds,
            off_seconds=state.off_seconds,
            source=state.source,
            outdoor=cycle.outdoor,
            lead_in="Notbetriebstakt läuft weiter, aktuelle",
        )
        return CycleOutput(state, CycleDecision(state.phase == PHASE_ON, REASON_CONTINUE, reason))

    # Phase deadline reached (or passed -- no catch-up, see module docstring).
    if state.phase == PHASE_ON:
        new_state = CycleState(
            phase=PHASE_OFF,
            phase_started_at=cycle.now,
            phase_deadline=cycle.now + timedelta(seconds=state.off_seconds),
            source=state.source,
            on_seconds=state.on_seconds,
            off_seconds=state.off_seconds,
            warm_locked=state.warm_locked,
        )
        reason = _explain(
            phase=PHASE_OFF,
            on_seconds=new_state.on_seconds,
            off_seconds=new_state.off_seconds,
            source=new_state.source,
            outdoor=cycle.outdoor,
            lead_in="Notbetriebstakt wechselt auf",
        )
        return CycleOutput(new_state, CycleDecision(False, REASON_TRANSITION, reason))

    # Was PHASE_OFF -- this is a pair boundary: re-resolve source/values.
    on_s, off_s, source, warm = _resolve_pair(
        cycle.profile, cycle.outdoor, state.warm_locked, cycle.min_on_seconds, cycle.min_off_seconds
    )
    new_phase = PHASE_OFF if on_s == 0 else PHASE_ON
    duration = off_s if new_phase == PHASE_OFF else on_s
    new_state = CycleState(
        phase=new_phase,
        phase_started_at=cycle.now,
        phase_deadline=cycle.now + timedelta(seconds=duration),
        source=source,
        on_seconds=on_s,
        off_seconds=off_s,
        warm_locked=warm,
    )
    reason_code = REASON_WARM_LOCK if on_s == 0 else REASON_TRANSITION
    lead_in = "Warm-Aus-Sperre hält" if on_s == 0 else "Notbetriebstakt wechselt auf"
    reason = _explain(
        phase=new_phase,
        on_seconds=on_s,
        off_seconds=off_s,
        source=source,
        outdoor=cycle.outdoor,
        lead_in=lead_in,
    )
    return CycleOutput(new_state, CycleDecision(new_phase == PHASE_ON, reason_code, reason))
