"""Zone-level emergency-operation state machine and recovery counting.

Auftrag 5b of `lokal/plaene/0.11.0-notbetrieb.md`, backed by plan section 1.2
(Zustandsautomat), 1.3 (Ersatzquelle/PI) and 1.4 (Vorrangtabelle Rang 2/3/9).
**Reine Funktion**, exactly like `domain/emergency_cycle.py` and
`domain/temperature_source_health.py`: no clock of its own (`now` is always the
caller's), no database access, every input and every persisted bit of state
arrives as a plain, frozen dataclass. `advance()` is the one entry point --
`state=None` means "no zone sensor-failure state persisted yet", exactly the
same convention `emergency_cycle.advance()` uses.

## The four stages

```
NORMAL
  |  Wandfühler wird unbrauchbar
  v
ERSATZQUELLE  --(Wandfühler >= 2 neue Messwerte, >= recovery_seconds)-->  NORMAL
  |  auch die Ersatzquelle liefert keine aktuelle Messung
  v
NOTBETRIEB
  |  Wandfühler ODER Ersatzquelle wieder brauchbar (Wandfühler hat Vorrang)
  v
RUECKKEHRPRUEFUNG  --(die geprüfte Quelle fällt erneut aus)-->  NOTBETRIEB (gleiche Episode)
  |  >= 2 neue Messwerte dieser einen Quelle, >= recovery_seconds durchgehend
  v
NORMAL              (Quelle war der Wandfühler)
  oder ERSATZQUELLE  (Quelle war die Ersatzquelle -- Rückkehr zum Wandfühler
                       startet danach ihre eigene, unabhängige Prüfung)
```

This module never invents a wait beyond what its two inputs (`wall_probe`,
`replacement`) already say is usable *this* cycle -- "nie gemessen seit Start"
(both readings unusable from the very first call) reaches `NOTBETRIEB`
immediately, in the same call, because `_wall_probe_usable`/candidate
usability (computed upstream by `domain/temperature_source_health.py`) already
encodes the sensor timeout; this module adds no second one (plan 5b: "ohne
Zusatzwartezeit").

## Episodes and the two once-per-episode signals

An episode begins on the *first* transition away from `NORMAL` (whichever
stage it lands in) and ends only when the zone reaches `NORMAL` again --
`ERSATZQUELLE -> NOTBETRIEB` and a fallback `RUECKKEHRPRUEFUNG -> NOTBETRIEB`
both continue the *same* episode (plan 5b: "Rückfall während
Rückkehrprüfung setzt dieselbe Episode fort"); only the caller (a later task)
turns `episode_started`/`episode_ended` into an actual
`sensor_failure_episode` row and its id. `trigger_kind` mirrors the model's own
two values -- `"wandfuehler"` when the wall probe alone failed and the
replacement was still there to catch it, `"alle_quellen"` when neither source
delivered a current reading at the moment the episode opened.

`episode_ended` can also happen with `reason_code=REASON_DEAKTIVIERT` (`enabled`
flipped to `False` mid-episode, see below) -- this is **not** an Entwarnung
(plan 8's "eine Störungsmeldung je Episode ... und Entwarnung"): the fault was
never resolved, the operator simply told this module to stop tracking it. A
later task (Auftrag 7/8) must not send the "all clear" notification text for
this particular `episode_ended`, only close the episode record.

`handover_due` is the "Thermostat-Übergabe fällig" signal from plan 1.4 Rang 3:
it fires exactly once per episode, on the cycle `NOTBETRIEB` is *first*
reached -- never again for the rest of that episode, including every later
fallback out of `RUECKKEHRPRUEFUNG` back into `NOTBETRIEB` and including a
restart across process cycles (the flag is part of the persisted state, not
recomputed from "are we in NOTBETRIEB right now").

`pi_neutralized` (Rang 9) needs no state at all -- it is simply "the new stage
is not NORMAL".

## Recovery counting

Both recovery checks (`ERSATZQUELLE -> NORMAL` and the two
`RUECKKEHRPRUEFUNG` exits) share one rule, applied against exactly one
tracked source: at least `recovery_samples` *distinct* `measured_at` values
(the same timestamp seen again across polling cycles, because the source
simply has not produced a new value yet, never counts twice) spanning at
least `recovery_seconds` of continuous usability since the moment that source
was first seen usable in this attempt. Both conditions gate the same instant
in time -- there is no separate "and now wait 60s on top of the count" step.
A single unusable cycle for the tracked source resets the whole attempt (in
`ERSATZQUELLE` it stays `ERSATZQUELLE` with the count zeroed; in
`RUECKKEHRPRUEFUNG` it falls all the way back to `NOTBETRIEB`, per the
diagram above). In `RUECKKEHRPRUEFUNG`, if the tracked source is the
replacement and *which device* answers as "the replacement" changes between
cycles (`temperature_source_health`'s own cold-candidate choice can flip),
that also restarts the count from zero against the new device -- a partial
count against one thermostat must not silently complete against a different
one (plan 5b: "Quellenwechsel startet Rückkehrprüfung neu").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

STAGE_NORMAL = "normal"
STAGE_ERSATZQUELLE = "ersatzquelle"
STAGE_NOTBETRIEB = "notbetrieb"
STAGE_RUECKKEHRPRUEFUNG = "rueckkehrpruefung"

KIND_WANDFUEHLER = "wandfuehler"
KIND_ERSATZQUELLE = "ersatzquelle"

TRIGGER_WANDFUEHLER = "wandfuehler"
TRIGGER_ALLE_QUELLEN = "alle_quellen"

# Reason codes -- diagnosis, read by people (Grundsatz 5); a caller persisting these
# (e.g. onto `actuator_decision.reason_code`) keeps them stable across releases.
REASON_NORMAL = "sensorausfall_normal"
REASON_ERSATZQUELLE_EINTRITT = "sensorausfall_ersatzquelle_eintritt"
REASON_ERSATZQUELLE_HAELT = "sensorausfall_ersatzquelle_haelt"
REASON_NOTBETRIEB_EINTRITT = "sensorausfall_notbetrieb_eintritt"
REASON_NOTBETRIEB_HAELT = "sensorausfall_notbetrieb_haelt"
REASON_RUECKKEHR_STARTET = "sensorausfall_rueckkehr_startet"
REASON_RUECKKEHR_LAEUFT = "sensorausfall_rueckkehr_laeuft"
REASON_RUECKKEHR_NEUSTART = "sensorausfall_rueckkehr_quellenwechsel"
REASON_RUECKFALL = "sensorausfall_rueckkehr_rueckfall"
REASON_RUECKKEHR_ABGESCHLOSSEN = "sensorausfall_rueckkehr_abgeschlossen"
REASON_DEAKTIVIERT = "sensorausfall_deaktiviert"


@dataclass(frozen=True)
class SourceReading:
    """One candidate source's usability this cycle, exactly as the caller already
    judged it (`domain/temperature_source_health.py` for both the wall probe and
    the chosen replacement candidate) -- this module recomputes none of that.

    `device_id` identifies *which* device stands behind a usable replacement
    reading; it is always `None` for the wall probe itself (a zone has exactly
    one) and also `None` for a replacement reading that is not usable at all
    (there is no candidate to name)."""

    usable: bool
    measured_at: datetime | None
    device_id: int | None = None
    device_name: str | None = None


@dataclass(frozen=True)
class StageInput:
    """Everything one `advance()` call needs beyond the previous state."""

    now: datetime
    enabled: bool
    wall_probe: SourceReading
    replacement: SourceReading
    recovery_seconds: int
    recovery_samples: int


@dataclass(frozen=True)
class ZoneEmergencyState:
    """The persisted runtime state of one zone -- mirrors
    `db.models.sensor_failure.ZoneSensorFailureState`'s columns (minus `zone_id`,
    which the caller already knows, and `episode_id`, whose actual integer only
    the persistence layer can assign; `episode_open` stands in for what that
    id's presence/history already implies -- non-`None` means open).
    `handover_due_signalled` **is** a real column there (Kreuzreview finding on
    `509997b`: without one, the once-per-episode latch would live only in this
    process's memory and either re-fire or silently vanish across a restart
    mid-episode) -- every other field here has a same-named, same-meaning
    counterpart."""

    stage: str = STAGE_NORMAL
    tracked_kind: str | None = None
    tracked_device_id: int | None = None
    source_measured_at: datetime | None = None
    recovery_started_at: datetime | None = None
    last_counted_measurement_at: datetime | None = None
    recovery_sample_count: int = 0
    episode_open: bool = False
    handover_due_signalled: bool = False


@dataclass(frozen=True)
class StageEvents:
    """What happened this cycle, for the caller to turn into persistence,
    notifications and the actuator plan (Auftrag 7/8) -- never raised as an
    exception, always a value."""

    episode_started: bool
    episode_ended: bool
    trigger_kind: str | None
    pi_neutralized: bool
    handover_due: bool
    reason_code: str
    reason: str


@dataclass(frozen=True)
class StageOutput:
    state: ZoneEmergencyState
    events: StageEvents


def _no_op(reason_code: str, reason: str, *, pi_neutralized: bool = False) -> StageEvents:
    return StageEvents(
        episode_started=False,
        episode_ended=False,
        trigger_kind=None,
        pi_neutralized=pi_neutralized,
        handover_due=False,
        reason_code=reason_code,
        reason=reason,
    )


def _name(reading: SourceReading, kind: str) -> str:
    if kind == KIND_WANDFUEHLER:
        return "Wandfühler"
    return reading.device_name or "Ersatzquelle"


def _progress_reason(count: int, samples: int, elapsed_s: int, seconds: int, name: str) -> str:
    return (
        f"Rückkehrprüfung gegen {name}: {count}/{samples} neue Messwerte, "
        f"{elapsed_s}/{seconds} s durchgehend brauchbar."
    )


def _reading_for(kind: str, inp: StageInput) -> SourceReading:
    return inp.wall_probe if kind == KIND_WANDFUEHLER else inp.replacement


def _device_id_for(kind: str, inp: StageInput) -> int | None:
    return None if kind == KIND_WANDFUEHLER else inp.replacement.device_id


def _fresh_recovery(
    stage: str,
    kind: str,
    device_id: int | None,
    reading: SourceReading,
    *,
    episode_open: bool,
    handover_due_signalled: bool,
    reason_code: str,
    reason: str,
    episode_started: bool = False,
    trigger_kind: str | None = None,
    handover_due: bool = False,
) -> StageOutput:
    count = 1 if reading.measured_at is not None else 0
    state = ZoneEmergencyState(
        stage=stage,
        tracked_kind=kind,
        tracked_device_id=device_id,
        source_measured_at=reading.measured_at,
        recovery_started_at=None,  # set to `now` by the caller loop below
        last_counted_measurement_at=reading.measured_at,
        recovery_sample_count=count,
        episode_open=episode_open,
        handover_due_signalled=handover_due_signalled,
    )
    events = StageEvents(
        episode_started=episode_started,
        episode_ended=False,
        trigger_kind=trigger_kind,
        pi_neutralized=True,
        handover_due=handover_due,
        reason_code=reason_code,
        reason=reason,
    )
    return StageOutput(state, events)


def _enter_from_normal(inp: StageInput) -> StageOutput:
    wall_ok = inp.wall_probe.usable
    repl_ok = inp.replacement.usable
    if wall_ok:
        events = _no_op(REASON_NORMAL, "Normalbetrieb: Wandfühler betriebsbereit.")
        return StageOutput(ZoneEmergencyState(), events)

    if repl_ok:
        # Entry into ERSATZQUELLE does not itself start a recovery attempt
        # against the wall probe -- that only begins once the wall probe is
        # usable again (handled by `_ersatzquelle`'s own branch below).
        state = ZoneEmergencyState(
            stage=STAGE_ERSATZQUELLE,
            episode_open=True,
            handover_due_signalled=False,
        )
        events = StageEvents(
            episode_started=True,
            episode_ended=False,
            trigger_kind=TRIGGER_WANDFUEHLER,
            pi_neutralized=True,
            handover_due=False,
            reason_code=REASON_ERSATZQUELLE_EINTRITT,
            reason=(
                f"Wandfühler ausgefallen — Ersatzquelle "
                f"{_name(inp.replacement, KIND_ERSATZQUELLE)} übernimmt, PI ausgesetzt."
            ),
        )
        return StageOutput(state, events)

    state = ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB,
        episode_open=True,
        handover_due_signalled=True,
    )
    events = StageEvents(
        episode_started=True,
        episode_ended=False,
        trigger_kind=TRIGGER_ALLE_QUELLEN,
        pi_neutralized=True,
        handover_due=True,
        reason_code=REASON_NOTBETRIEB_EINTRITT,
        reason=(
            "Wandfühler und Ersatzquelle unbrauchbar — Notbetrieb, Thermostat-Übergabe fällig."
        ),
    )
    return StageOutput(state, events)


def _ersatzquelle(cur: ZoneEmergencyState, inp: StageInput) -> StageOutput:
    if inp.wall_probe.usable:
        return _progress_or_finish(
            cur,
            inp,
            kind=KIND_WANDFUEHLER,
            device_id=None,
            reading=inp.wall_probe,
            complete_stage=STAGE_NORMAL,
            complete_episode_ends=True,
        )

    if inp.replacement.usable:
        state = ZoneEmergencyState(
            stage=STAGE_ERSATZQUELLE,
            episode_open=True,
            handover_due_signalled=cur.handover_due_signalled,
        )
        events = _no_op(
            REASON_ERSATZQUELLE_HAELT,
            f"Ersatzquelle {_name(inp.replacement, KIND_ERSATZQUELLE)} weiterhin aktiv, "
            "Wandfühler nicht brauchbar.",
            pi_neutralized=True,
        )
        return StageOutput(state, events)

    state = ZoneEmergencyState(
        stage=STAGE_NOTBETRIEB,
        episode_open=True,
        handover_due_signalled=True,
    )
    first_time = not cur.handover_due_signalled
    events = StageEvents(
        episode_started=False,
        episode_ended=False,
        trigger_kind=None,
        pi_neutralized=True,
        handover_due=first_time,
        reason_code=REASON_NOTBETRIEB_EINTRITT,
        reason=(
            "Auch die Ersatzquelle ist unbrauchbar geworden — Notbetrieb"
            + (", Thermostat-Übergabe fällig." if first_time else " (gleiche Störung).")
        ),
    )
    return StageOutput(state, events)


def _notbetrieb(cur: ZoneEmergencyState, inp: StageInput) -> StageOutput:
    if inp.wall_probe.usable:
        kind, reading, device_id = KIND_WANDFUEHLER, inp.wall_probe, None
    elif inp.replacement.usable:
        kind, reading, device_id = KIND_ERSATZQUELLE, inp.replacement, inp.replacement.device_id
    else:
        state = replace(cur, stage=STAGE_NOTBETRIEB)
        events = _no_op(
            REASON_NOTBETRIEB_HAELT,
            "Notbetrieb hält an — keine Quelle liefert eine Messung.",
            pi_neutralized=True,
        )
        return StageOutput(state, events)

    out = _fresh_recovery(
        STAGE_RUECKKEHRPRUEFUNG,
        kind,
        device_id,
        reading,
        episode_open=True,
        handover_due_signalled=True,
        reason_code=REASON_RUECKKEHR_STARTET,
        reason=f"Rückkehrprüfung gegen {_name(reading, kind)} gestartet.",
    )
    state = replace(out.state, recovery_started_at=inp.now)
    return StageOutput(state, out.events)


def _rueckkehrpruefung(cur: ZoneEmergencyState, inp: StageInput) -> StageOutput:
    kind = cur.tracked_kind or KIND_WANDFUEHLER
    reading = _reading_for(kind, inp)
    device_id_now = _device_id_for(kind, inp)

    if not reading.usable:
        state = ZoneEmergencyState(
            stage=STAGE_NOTBETRIEB,
            episode_open=True,
            handover_due_signalled=True,  # already signalled earlier this episode
        )
        events = _no_op(
            REASON_RUECKFALL,
            f"Rückkehrprüfung gegen {_name(reading, kind)}: Quelle erneut ausgefallen, "
            "zurück in Notbetrieb (gleiche Störung).",
            pi_neutralized=True,
        )
        return StageOutput(state, events)

    if kind == KIND_ERSATZQUELLE and device_id_now != cur.tracked_device_id:
        out = _fresh_recovery(
            STAGE_RUECKKEHRPRUEFUNG,
            kind,
            device_id_now,
            reading,
            episode_open=True,
            handover_due_signalled=True,
            reason_code=REASON_RUECKKEHR_NEUSTART,
            reason=(
                f"Rückkehrprüfung neu gestartet: Ersatzquelle gewechselt zu "
                f"{_name(reading, kind)}."
            ),
        )
        state = replace(out.state, recovery_started_at=inp.now)
        return StageOutput(state, out.events)

    complete_stage = STAGE_NORMAL if kind == KIND_WANDFUEHLER else STAGE_ERSATZQUELLE
    return _progress_or_finish(
        cur,
        inp,
        kind=kind,
        device_id=device_id_now,
        reading=reading,
        complete_stage=complete_stage,
        complete_episode_ends=(kind == KIND_WANDFUEHLER),
    )


def _progress_or_finish(
    cur: ZoneEmergencyState,
    inp: StageInput,
    *,
    kind: str,
    device_id: int | None,
    reading: SourceReading,
    complete_stage: str,
    complete_episode_ends: bool,
) -> StageOutput:
    """Shared recovery-counting step for `ERSATZQUELLE -> NORMAL` and both
    `RUECKKEHRPRUEFUNG` exits -- `reading.usable` is always `True` here, the
    caller has already dealt with the unusable case."""
    started_at = cur.recovery_started_at if cur.recovery_started_at is not None else inp.now
    last_counted = cur.last_counted_measurement_at
    count = cur.recovery_sample_count if cur.recovery_started_at is not None else 0

    if reading.measured_at is not None and reading.measured_at != last_counted:
        count += 1
        last_counted = reading.measured_at

    elapsed_s = int((inp.now - started_at).total_seconds())
    name = _name(reading, kind)

    if count >= inp.recovery_samples and elapsed_s >= inp.recovery_seconds:
        if complete_episode_ends:
            state = ZoneEmergencyState()
        else:
            state = ZoneEmergencyState(
                stage=complete_stage,
                episode_open=True,
                handover_due_signalled=cur.handover_due_signalled,
            )
        events = StageEvents(
            episode_started=False,
            episode_ended=complete_episode_ends,
            trigger_kind=None,
            pi_neutralized=(complete_stage != STAGE_NORMAL),
            handover_due=False,
            reason_code=REASON_RUECKKEHR_ABGESCHLOSSEN,
            reason=(
                f"Rückkehrprüfung gegen {name} abgeschlossen ({count} Messwerte, "
                f"{elapsed_s} s) — "
                + (
                    "Normalbetrieb wieder aufgenommen."
                    if complete_stage == STAGE_NORMAL
                    else f"Ersatzquelle {name} aktiv, PI weiter ausgesetzt."
                )
            ),
        )
        return StageOutput(state, events)

    state = ZoneEmergencyState(
        stage=cur.stage,
        tracked_kind=kind,
        tracked_device_id=device_id,
        source_measured_at=reading.measured_at,
        recovery_started_at=started_at,
        last_counted_measurement_at=last_counted,
        recovery_sample_count=count,
        episode_open=cur.episode_open,
        handover_due_signalled=cur.handover_due_signalled,
    )
    events = StageEvents(
        episode_started=False,
        episode_ended=False,
        trigger_kind=None,
        pi_neutralized=True,
        handover_due=False,
        reason_code=REASON_RUECKKEHR_LAEUFT,
        reason=_progress_reason(count, inp.recovery_samples, elapsed_s, inp.recovery_seconds, name),
    )
    return StageOutput(state, events)


def advance(state: ZoneEmergencyState | None, inp: StageInput) -> StageOutput:
    """One control-cycle step of the zone's emergency-operation state machine.

    `state=None` is "no persisted runtime state yet for this zone" -- treated
    exactly like a fresh `ZoneEmergencyState()` (stage `NORMAL`, no open
    episode). `enabled=False` always yields plain `NORMAL` behaviour (plan 5b:
    "immer normal-Verhalten wie heute"): any episode that happened to be open
    is closed on the spot (so it does not linger forever once the operator
    turns the feature off mid-fault), and every counter resets. This is a
    reine Funktion's own defensible reading of an underspecified edge case,
    not a blocker -- disabling is defined everywhere else as "as if none of
    this existed", and an emergency-operation state machine that still thinks
    an episode is open after being told to behave normally would contradict
    that.
    """
    cur = state if state is not None else ZoneEmergencyState()

    if not inp.enabled:
        was_open = cur.episode_open
        events = StageEvents(
            episode_started=False,
            episode_ended=was_open,
            trigger_kind=None,
            pi_neutralized=False,
            handover_due=False,
            reason_code=REASON_DEAKTIVIERT,
            reason="Notbetrieb deaktiviert — normale Regelung angenommen.",
        )
        return StageOutput(ZoneEmergencyState(), events)

    if cur.stage == STAGE_NORMAL:
        return _enter_from_normal(inp)
    if cur.stage == STAGE_ERSATZQUELLE:
        return _ersatzquelle(cur, inp)
    if cur.stage == STAGE_NOTBETRIEB:
        return _notbetrieb(cur, inp)
    if cur.stage == STAGE_RUECKKEHRPRUEFUNG:
        return _rueckkehrpruefung(cur, inp)

    # pragma: no cover -- durch die vier Konstanten oben ausgeschlossen, nicht erreichbar
    raise ValueError(f"Unbekannte Notbetriebsstufe: {cur.stage}")  # pragma: no cover


__all__ = [
    "KIND_ERSATZQUELLE",
    "KIND_WANDFUEHLER",
    "REASON_DEAKTIVIERT",
    "REASON_ERSATZQUELLE_EINTRITT",
    "REASON_ERSATZQUELLE_HAELT",
    "REASON_NORMAL",
    "REASON_NOTBETRIEB_EINTRITT",
    "REASON_NOTBETRIEB_HAELT",
    "REASON_RUECKFALL",
    "REASON_RUECKKEHR_ABGESCHLOSSEN",
    "REASON_RUECKKEHR_LAEUFT",
    "REASON_RUECKKEHR_NEUSTART",
    "REASON_RUECKKEHR_STARTET",
    "STAGE_ERSATZQUELLE",
    "STAGE_NORMAL",
    "STAGE_NOTBETRIEB",
    "STAGE_RUECKKEHRPRUEFUNG",
    "TRIGGER_ALLE_QUELLEN",
    "TRIGGER_WANDFUEHLER",
    "SourceReading",
    "StageEvents",
    "StageInput",
    "StageOutput",
    "ZoneEmergencyState",
    "advance",
]
