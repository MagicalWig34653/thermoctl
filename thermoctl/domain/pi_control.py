"""Pure PI and window functions for the optional per-zone PI controller (Beta).

Specification: `docs/superpowers/specs/2026-09-02-pi-regelung-spezifikation.md`,
sections 1 to 4. This module builds **step 3** of the build order in section 11:
reine PI- und Fensterfunktionen. Nothing here is wired to
`thermoctl.domain.control_loop.decide()` yet -- that is step 4, a separate task. The
existing 2.376-line state-table test therefore stays the exhaustive specification of
the precedence chain for `pi_enabled=False`, unaffected by this module.

Purity (section 1 of the spec, and CLAUDE.md's ban on hidden state): every function
here takes its previous state and every input value as an argument, and returns the
new state and its result as a value. No clock, no database, no globals, no mutation of
the arguments (every dataclass is frozen). The caller -- eventually
`services/shadow_run.py` -- owns the actual `ControllerState` row and decides when to
call which function below:

- A cycle where PI's own rules govern (nothing overrides it): `pi_cycle()`.
- A cycle where an *earlier* precedence rule already decided the outcome and PI must
  not accumulate anything for it (window open, frost protection, sensor failure, a
  running valve-protection cycle, or a corrupted/missing PI state, or the moment a
  dry run turns armed): `reset_pi_state()`, with the reason code that names the rule.
  A caller does **not** need a separate function for "override begins/ends" or
  "boost begins/ends" -- see the module docstring of `pi_cycle()` for why those are
  already covered by the ordinary setpoint-context reset in `pi_cycle()` itself.

Section 3's schaltspiel calculation is why `pi_min_on_seconds` / `pi_min_off_seconds`
are *soft* minimums here, unlike `min_on_seconds` / `min_off_seconds` in
`control_loop.py`, which stay hard. See `window_modulate()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

# --- Fixed by the spec (section 3): the window length is deliberately not a third
# tuning parameter, and the remainder's bound matches it exactly. ---
WINDOW_SECONDS = 900
REMAINDER_LIMIT_S = Decimal(900)

# The activation ceiling from section 3: "PI darf nur aktiviert werden, wenn der
# Regelzyklus höchstens 60 Sekunden ... ist."
MAX_CONTROL_CYCLE_SECONDS = 60

# --- Reset reasons -- one per row of section 4's table that resets PI state, plus
# the two housekeeping cases from section 2 (context change, time gap) and the
# arming/invalid-state case from section 4's closing paragraph. Whoever persists
# these values keeps them stable: they are diagnosis, read by people. ---
RESET_REASON_WINDOW_OPEN = "fenster_offen"
RESET_REASON_FROST = "frostschutz"
RESET_REASON_SENSOR_FAILURE = "sensorausfall"
RESET_REASON_VALVE_PROTECTION = "ventilschutz"
RESET_REASON_CONTEXT_CHANGE = "sollwertkontext_wechsel"
RESET_REASON_TIME_GAP = "zeitluecke"
RESET_REASON_ARMING = "scharfschaltung"
RESET_REASON_INVALID_STATE = "ungueltiger_zustand"

# --- What happened to the integrator this cycle (section 4's "Integrator" column). ---
INTEGRATOR_CONTINUE = "weiter"
INTEGRATOR_HOLD = "angehalten"
INTEGRATOR_RESET = "zurueckgesetzt"

# --- Why the modulator's on/off output is what it is this cycle. ---
MODULATOR_REASON_REGULAR = "regulaer"
MODULATOR_REASON_HELD = "gehalten_mindestdauer"
MODULATOR_REASON_TASTGRAD_VORRANG = "pi_tastgrad_vorrang"


def _clamp01(value: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(1), value))


def _clamp_remainder(value: Decimal) -> Decimal:
    return max(-REMAINDER_LIMIT_S, min(REMAINDER_LIMIT_S, value))


def window_start_for(now: datetime) -> datetime:
    """Floors `now` to the enclosing 15-minute UTC boundary (section 3).

    `now` must be timezone-aware. The spec fixes the window to UTC quarter-hours
    deliberately -- not to the zone's local time -- so a summer-time change never
    moves the boundary.
    """
    if now.tzinfo is None:
        raise ValueError("now muss zeitzonenbewusst sein.")
    minute = (now.minute // 15) * 15
    return now.replace(minute=minute, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# Section 2 -- the PI arithmetic itself.
# --------------------------------------------------------------------------- #


def pi_dt(
    *,
    previous_evaluated_at: datetime | None,
    now: datetime,
    expected_cycle_seconds: int,
) -> Decimal | None:
    """The elapsed time to integrate over, or `None` if it must not be integrated.

    Section 2, third paragraph: `dt <= 0`, a gap of more than two expected cycles, or
    no previous evaluation at all (missing state) all mean the same thing -- a service
    outage must not retroactively become an hours-long control deviation. The caller
    resets the PI state with `RESET_REASON_TIME_GAP` when this returns `None`.
    """
    if previous_evaluated_at is None:
        return None
    dt = Decimal(str((now - previous_evaluated_at).total_seconds()))
    if dt <= 0:
        return None
    if dt > 2 * expected_cycle_seconds:
        return None
    return dt


def pi_arithmetic(
    *,
    integral: Decimal,
    error_k: Decimal,
    dt_seconds: Decimal,
    gain_per_k: Decimal,
    integral_time_minutes: Decimal,
) -> tuple[Decimal, Decimal]:
    """One PI step: `(u, neuer Integralanteil)` (section 2).

    `dt_seconds` must already be a validated, positive duration -- callers get that
    from `pi_dt()`. Conditional integration is the first anti-windup guard: once `u`
    is exactly saturated (0 or 1) *and* the error still pushes further into the same
    saturation, the integral is frozen rather than driven further past the bound it
    is already clamped to; in the opposite direction it is free to unwind.
    """
    u_before = _clamp01(gain_per_k * error_k + integral)
    stuck_high = u_before == 1 and error_k > 0
    stuck_low = u_before == 0 and error_k < 0
    if stuck_high or stuck_low:
        new_integral = integral
    else:
        ti_seconds = integral_time_minutes * 60
        increment = (gain_per_k / ti_seconds) * error_k * dt_seconds
        new_integral = _clamp01(integral + increment)
    u = _clamp01(gain_per_k * error_k + new_integral)
    return u, new_integral


# --------------------------------------------------------------------------- #
# Section 3 -- the window modulator: duty cycle to on/off decision.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModulatorState:
    """The window modulator's own persisted state (part of section 5's field list)."""

    on: bool
    held_for_s: int | None
    remainder_s: Decimal
    window_start: datetime | None
    frozen_duty: Decimal | None


NEUTRAL_MODULATOR_STATE = ModulatorState(
    on=False,
    held_for_s=None,
    remainder_s=Decimal(0),
    window_start=None,
    frozen_duty=None,
)


@dataclass(frozen=True)
class ModulatorResult:
    on: bool
    state: ModulatorState
    switched: bool
    reason_code: str
    integrator_action: str


def _held_after(held_for_s: int | None, dt_seconds: Decimal) -> int:
    increment = int(dt_seconds)
    return increment if held_for_s is None else held_for_s + increment


def window_modulate(
    state: ModulatorState,
    *,
    now: datetime,
    u_raw: Decimal,
    dt_seconds: Decimal,
    pi_min_on_seconds: int,
    pi_min_off_seconds: int,
) -> ModulatorResult:
    """One regular modulator cycle (section 3).

    The duty cycle is frozen at the start of each 15-minute UTC window and held for
    its whole length; only the *distribution* of on/off pulses within the window
    reacts every cycle, via the signed remainder. The PI minimum durations
    (`pi_min_on_seconds` / `pi_min_off_seconds`) hold the current state by default --
    "grundsätzlich bis zum passenden PI-Wert" -- but they are a soft minimum, not a
    hard one: two situations override the hold and switch immediately, tagged
    `pi_tastgrad_vorrang`:

    1. The frozen duty is exactly 0 while the actuator is on, or exactly 1 while it
       is off -- the actuator must not stay in a state the duty forbids entirely.
    2. Holding the current state this cycle would push the remainder past its
       [-900, 900] bound -- the bound exists so a blocked minimum duration cannot
       accumulate an unlimited heating-time debt (section 3, "Verworfen und schlecht
       lösbar").

    Once the minimum duration is satisfied, the remainder's sign decides: positive
    favours on, non-positive favours off (the spec's explicit tie-break, "bei
    gleichem Betrag gewinnt Aus"). That is a regular switch, not a `pi_tastgrad_vorrang`
    one -- the minimum duration was never in the way.
    """
    if not Decimal(0) <= u_raw <= Decimal(1):
        raise ValueError("u_raw muss zwischen 0 und 1 liegen.")
    if dt_seconds <= 0:
        raise ValueError("dt_seconds muss positiv sein.")

    boundary = window_start_for(now)
    if state.window_start != boundary or state.frozen_duty is None:
        working = replace(state, window_start=boundary, frozen_duty=u_raw)
    else:
        working = state
    duty = working.frozen_duty
    assert duty is not None

    required_min = pi_min_on_seconds if working.on else pi_min_off_seconds
    below_min = working.held_for_s is not None and working.held_for_s < required_min

    def settle(on: bool, *, reason: str) -> ModulatorResult:
        switched = on != working.on
        on_seconds = dt_seconds if on else Decimal(0)
        remainder = _clamp_remainder(working.remainder_s + duty * dt_seconds - on_seconds)
        held = int(dt_seconds) if switched else _held_after(working.held_for_s, dt_seconds)
        new_state = ModulatorState(
            on=on,
            held_for_s=held,
            remainder_s=remainder,
            window_start=working.window_start,
            frozen_duty=working.frozen_duty,
        )
        integrator_action = (
            INTEGRATOR_HOLD if (not switched and reason == MODULATOR_REASON_HELD)
            else INTEGRATOR_CONTINUE
        )
        return ModulatorResult(
            on=on,
            state=new_state,
            switched=switched,
            reason_code=reason,
            integrator_action=integrator_action,
        )

    # u=0 and u=1 are absolute, not just a one-off nudge when the state first
    # contradicts them (section 3: "u = 0 bleibt aus und u = 1 bleibt ein"). Without
    # this the ordinary tie-break below ("bei gleichem Betrag gewinnt Aus") would flip
    # a u=1 zone back off the very cycle its remainder first settles at exactly 0.
    if duty == 1 or duty == 0:
        target = duty == 1
        if target != working.on:
            reason = MODULATOR_REASON_TASTGRAD_VORRANG if below_min else MODULATOR_REASON_REGULAR
        else:
            reason = MODULATOR_REASON_REGULAR
        return settle(target, reason=reason)

    if below_min:
        on_seconds = dt_seconds if working.on else Decimal(0)
        projected = working.remainder_s + duty * dt_seconds - on_seconds
        if projected > REMAINDER_LIMIT_S or projected < -REMAINDER_LIMIT_S:
            return settle(not working.on, reason=MODULATOR_REASON_TASTGRAD_VORRANG)
        return settle(working.on, reason=MODULATOR_REASON_HELD)

    wants_on = working.remainder_s > 0
    return settle(wants_on, reason=MODULATOR_REASON_REGULAR)


# --------------------------------------------------------------------------- #
# Section 5 -- the combined PI state, and reset/orchestration on top of it.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PiState:
    """The zone's PI operating state -- an input and an output, never held anywhere.

    Mirrors section 5's field list (integral, last evaluated timestamp, setpoint
    context key, the modulator's own fields, last reset reason) plus
    `awaiting_boundary_until`, which implements the "decide with hysteresis until the
    next full window boundary" rule from section 4's closing paragraph without giving
    the modulator itself a notion of "not yet armed".
    """

    integral: Decimal
    last_evaluated_at: datetime | None
    setpoint_context_key: str | None
    modulator: ModulatorState
    awaiting_boundary_until: datetime | None
    last_reset_reason: str | None


NEUTRAL_PI_STATE = PiState(
    integral=Decimal(0),
    last_evaluated_at=None,
    setpoint_context_key=None,
    modulator=NEUTRAL_MODULATOR_STATE,
    awaiting_boundary_until=None,
    last_reset_reason=None,
)


def reset_pi_state(
    reason: str,
    *,
    now: datetime | None = None,
    await_next_boundary: bool = False,
) -> PiState:
    """"Zurücksetzen" from section 4: `I = 0`, remainder = 0, no adopted duty.

    Callers use this directly -- not `pi_cycle()` -- for every precedence rule whose
    own decision already governs `heating` and that must not let PI accumulate
    anything meanwhile: window open (`RESET_REASON_WINDOW_OPEN`, including the resume
    delay -- both are "window open" for this purpose, since the resume wait is not a
    setpoint change), frost protection (`RESET_REASON_FROST`), sensor failure
    (`RESET_REASON_SENSOR_FAILURE`), and a valve-protection run
    (`RESET_REASON_VALVE_PROTECTION`) -- the caller calls this every cycle the run is
    active, which *is* "während des Laufs bei null halten": the state stays neutral
    throughout, not just on the first cycle.

    `await_next_boundary=True` is for the arming transition (dry run to armed) and
    for a missing or corrupted PI state, both `RESET_REASON_ARMING` /
    `RESET_REASON_INVALID_STATE`: "Bis zur nächsten vollständigen Fenstergrenze
    entscheidet nach diesem Übergang die Hysterese." It requires `now`, so the
    returned state can compute *which* boundary is next.
    """
    awaiting = None
    if await_next_boundary:
        if now is None:
            raise ValueError("await_next_boundary braucht `now`.")
        awaiting = window_start_for(now) + timedelta(seconds=WINDOW_SECONDS)
    return PiState(
        integral=Decimal(0),
        last_evaluated_at=now,
        setpoint_context_key=None,
        modulator=NEUTRAL_MODULATOR_STATE,
        awaiting_boundary_until=awaiting,
        last_reset_reason=reason,
    )


@dataclass(frozen=True)
class PiCycleInput:
    now: datetime
    error_k: Decimal
    setpoint_context_key: str
    expected_cycle_seconds: int
    gain_per_k: Decimal
    integral_time_minutes: Decimal
    pi_min_on_seconds: int
    pi_min_off_seconds: int


@dataclass(frozen=True)
class PiCycleOutput:
    state: PiState
    pi_available: bool
    heating: bool | None
    duty_raw: Decimal | None
    reason_code: str | None
    integrator_action: str
    # Whether the modulator's on/off output flipped *this* cycle -- lets a caller
    # that persists "timestamp of the last switch" separately from the modulator's
    # own `held_for_s` (see `ModulatorState`) tell a genuine flip apart from an
    # ordinary continuing cycle, without reaching into `window_modulate()` itself.
    # `False` whenever no modulator run happened this cycle (awaiting a window
    # boundary, or a time gap/context reset that only resets state).
    switched: bool


def pi_cycle(state: PiState, cycle: PiCycleInput) -> PiCycleOutput:
    """One regular PI cycle -- everything that is *not* one of section 4's
    suppressing rules (those go through `reset_pi_state()` directly, see there).

    This single function also covers the "beim Beginn und Ende zurücksetzen, danach
    weiterlaufen" rows for Übersteuerung and Boost, and the ordinary setpoint-context
    reset from section 2, without any separate override/boost handling: section 2
    already defines both as "jede Änderung des wirksamen Sollwertkontexts". The
    caller forms `cycle.setpoint_context_key` from the origin and identity of
    whichever setpoint is effective this cycle (override, boost, schedule slot,
    operating mode, frost protection); whenever it differs from the previous cycle's
    key, this function resets before doing anything else -- covering an override's or
    boost's start *and* end in the same mechanism, with no separate code path to keep
    in sync with section 4's table.
    """
    if state.awaiting_boundary_until is not None and cycle.now < state.awaiting_boundary_until:
        return PiCycleOutput(
            state=replace(state, last_evaluated_at=cycle.now),
            pi_available=False,
            heating=None,
            duty_raw=None,
            reason_code=state.last_reset_reason,
            integrator_action=INTEGRATOR_HOLD,
            switched=False,
        )

    base = state
    context_changed = (
        base.setpoint_context_key is not None
        and base.setpoint_context_key != cycle.setpoint_context_key
    )
    if context_changed:
        base = reset_pi_state(RESET_REASON_CONTEXT_CHANGE, now=base.last_evaluated_at)
    base = replace(base, setpoint_context_key=cycle.setpoint_context_key)

    dt = pi_dt(
        previous_evaluated_at=base.last_evaluated_at,
        now=cycle.now,
        expected_cycle_seconds=cycle.expected_cycle_seconds,
    )
    if dt is None:
        new_state = replace(
            reset_pi_state(RESET_REASON_TIME_GAP, now=cycle.now),
            setpoint_context_key=cycle.setpoint_context_key,
        )
        return PiCycleOutput(
            state=new_state,
            pi_available=False,
            heating=None,
            duty_raw=None,
            reason_code=RESET_REASON_TIME_GAP,
            integrator_action=INTEGRATOR_RESET,
            switched=False,
        )

    u, new_integral = pi_arithmetic(
        integral=base.integral,
        error_k=cycle.error_k,
        dt_seconds=dt,
        gain_per_k=cycle.gain_per_k,
        integral_time_minutes=cycle.integral_time_minutes,
    )
    modulator_result = window_modulate(
        base.modulator,
        now=cycle.now,
        u_raw=u,
        dt_seconds=dt,
        pi_min_on_seconds=cycle.pi_min_on_seconds,
        pi_min_off_seconds=cycle.pi_min_off_seconds,
    )
    integral_for_state = (
        base.integral
        if modulator_result.integrator_action == INTEGRATOR_HOLD
        else new_integral
    )
    new_state = PiState(
        integral=integral_for_state,
        last_evaluated_at=cycle.now,
        setpoint_context_key=cycle.setpoint_context_key,
        modulator=modulator_result.state,
        awaiting_boundary_until=None,
        last_reset_reason=(
            RESET_REASON_CONTEXT_CHANGE if context_changed else base.last_reset_reason
        ),
    )
    return PiCycleOutput(
        state=new_state,
        pi_available=True,
        heating=modulator_result.on,
        duty_raw=u,
        reason_code=modulator_result.reason_code,
        integrator_action=modulator_result.integrator_action,
        switched=modulator_result.switched,
    )


# --------------------------------------------------------------------------- #
# Section 3 / "Feststehender Zuschnitt" -- eligibility.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ActuatorProfile:
    """What `pi_eligible()` needs to know about one of a zone's assigned actuators.

    Deliberately not a database row -- the caller builds this from
    `domain.switch_commands.switch_commands()` /
    `domain.switch_commands.thermostat_commands()` plus the `self_regulating` flag on
    the zone-device assignment, keeping this module free of any session or model
    import (see `tests/test_architecture.py`).
    """

    self_regulating: bool
    has_switch_capability: bool
    has_thermostat_capability: bool


@dataclass(frozen=True)
class PiEligibility:
    eligible: bool
    reason: str


def pi_eligible(
    actuators: Sequence[ActuatorProfile],
    *,
    control_cycle_seconds: int,
    pi_min_on_seconds: int,
    pi_min_off_seconds: int,
) -> PiEligibility:
    """Whether a zone may use PI at all ("Feststehender Zuschnitt" and section 3).

    Strict on purpose, and zone-wide rather than per-device: a zone with even one
    self-regulating valve or one device carrying the `thermostat` capability -- not
    just one actually driven as a thermostat today -- is excluded outright, because a
    mixed zone would still let the same PI result reach a thermostat valve in
    `publishing.py`. A zone with no ordinary switch actuator at all is excluded the
    same way, not silently.

    The control-cycle bound is section 3's activation condition: PI only makes sense
    when the cycle can resolve to it own minimum durations without a third parameter.
    """
    if not actuators:
        return PiEligibility(False, "Kein gewöhnlicher Schaltaktor zugeordnet.")
    for actuator in actuators:
        if actuator.self_regulating:
            return PiEligibility(
                False, "Ein selbstregelndes Ventil ist der Zone zugeordnet."
            )
        if actuator.has_thermostat_capability:
            return PiEligibility(
                False, "Ein Gerät mit der Fähigkeit 'thermostat' ist der Zone zugeordnet."
            )
        if not actuator.has_switch_capability:
            return PiEligibility(
                False, "Ein Aktor ohne die Fähigkeit 'switch' ist der Zone zugeordnet."
            )
    if control_cycle_seconds <= 0:
        return PiEligibility(False, "Der Regelzyklus ist ungültig.")
    if control_cycle_seconds > MAX_CONTROL_CYCLE_SECONDS:
        return PiEligibility(
            False,
            f"Der Regelzyklus darf höchstens {MAX_CONTROL_CYCLE_SECONDS}s betragen.",
        )
    if control_cycle_seconds > pi_min_on_seconds or control_cycle_seconds > pi_min_off_seconds:
        return PiEligibility(
            False, "Der Regelzyklus ist länger als eine der PI-Mindestdauern."
        )
    return PiEligibility(True, "Die Zone erfüllt die Voraussetzungen für PI.")
