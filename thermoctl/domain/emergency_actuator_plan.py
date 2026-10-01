"""Vorrangtabelle (Plan 1.4) -> Aktorplan je Zuordnung.

Auftrag 7a of `lokal/plaene/0.11.0-notbetrieb.md`. Keeps the per-actuator send
plan out of `services/shadow_run.py` (Grundsatz 6: a rule lives once, not
inline in the orchestration that calls it). Two kinds of actuator assignment
exist in the plant (plan 1.1, entry 1 -- confirmed against the real devices in
`lokal/plaene/0.11.0-geraetevertrag.md` (d): no mixed-capability device
exists):

* `KIND_THERMOSTAT` -- a self-regulating radiator valve. Gets, once per
  episode, the "handover" the project owner decided (plan 1.1 entry 2,
  `0.11.0-geraetevertrag.md` (b)): `operating_mode="manual"` together with the
  emergency setpoint. Every later cycle of the same episode is `no_write` --
  this module never invents a second attempt, and Auftrag 7b's publisher is
  the one place that actually turns `ACTION_HANDOVER` into the write.
* `KIND_SWITCH` -- an on/off actuator (floor-circuit relay). Delegates
  straight to `domain.emergency_cycle.advance()`, which already owns the
  entire fixed-cycle/Außenkennlinie timing question (plan 3.4) -- this module
  adds nothing to that arithmetic, only the vorrangtabelle's action label.

Both functions are reine Funktionen in the same sense as
`domain.emergency_cycle`/`domain.emergency_operation`: every input arrives as
a plain argument, nothing is read from a database or a clock of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from thermoctl.domain import emergency_cycle

KIND_THERMOSTAT = "thermostat"
KIND_SWITCH = "switch"

ACTION_NORMAL = "normal"
ACTION_NO_WRITE = "no_write"
ACTION_SWITCH_ON = "switch_on"
ACTION_SWITCH_OFF = "switch_off"
ACTION_HANDOVER = "handover"

# Reason codes -- diagnosis, read by people (Grundsatz 5); stable across releases,
# like every other `sensorausfall_*` code this plan's modules define.
REASON_HANDOVER = "sensorausfall_uebergabe"
REASON_NO_WRITE_ERLEDIGT = "sensorausfall_schweigen"

# NOTE (Hauptsession-Review von 88bc87a, 2026-09-30): plan Auftrag 7a, item 3
# had originally called for this module to also provide a dedicated
# `shadow_decision.outcome_code` and to override the zone's own `would_heat`
# while in Notbetrieb. That is now explicitly overruled: `ShadowDecision` is
# exactly what the existing publisher (`services/publishing.py`) reads and
# sends the moment an installation runs scharf, regardless of Notbetrieb --
# Auftrag 7a is shadow-only by scope. There is therefore deliberately no
# `OUTCOME_CODE_NOTBETRIEB`/zone-level `Decision` here any more; every
# function below only ever produces the per-*actuator* plan
# (`services/shadow_run.py::_apply_emergency_actuators` persists it to
# `actuator_decision`/`actuator_emergency_state`, `simulated=True`, and never
# feeds it back into the zone's own decision). Rerouting the publisher to
# actually read this plan is Auftrag 7b's job, not this module's.


@dataclass(frozen=True)
class ThermostatDecision:
    """One cycle's plan for a thermostat-actuator assignment."""

    action: str  # ACTION_HANDOVER | ACTION_NO_WRITE
    reason_code: str
    reason: str
    # The new value for `ActuatorEmergencyState.(simulated_)handover_attempted_at`
    # -- `None` means "leave the persisted value unchanged" (already attempted
    # earlier this episode, or not due yet); only `ACTION_HANDOVER` ever sets it.
    handover_attempted_at: datetime | None


def plan_thermostat(
    *,
    already_attempted: bool,
    now: datetime,
    device_name: str,
    emergency_setpoint_c: Decimal,
) -> ThermostatDecision:
    """Rang 3 of plan 1.4: one handover attempt per episode, then silence.

    `already_attempted` is the caller's own answer to "does this exact
    episode already have a recorded attempt for this exact assignment" --
    tracked per assignment, not per zone, so a thermostat added to the zone
    mid-episode still gets its own single attempt rather than inheriting
    another device's. Deliberately independent of
    `emergency_operation.StageEvents.handover_due`: that signal fires only on
    the one cycle the zone first reaches `NOTBETRIEB`, but "einmal je
    scharfer Episode" (plan 1.4 Rang 3) must also cover an assignment that
    only exists, or only becomes eligible, on a later cycle of the same
    still-open episode -- the per-assignment latch, not the per-episode
    entry event, is what actually enforces "genau einmal".
    """
    if already_attempted:
        return ThermostatDecision(
            ACTION_NO_WRITE,
            REASON_NO_WRITE_ERLEDIGT,
            f"{device_name}: bereits übergeben (Notsollwert, operating_mode=manual) — "
            "keine weiteren Schreibbefehle für diese Störung.",
            None,
        )
    return ThermostatDecision(
        ACTION_HANDOVER,
        REASON_HANDOVER,
        f"{device_name}: Notbetrieb-Übergabe — operating_mode=manual, Notsollwert "
        f"{emergency_setpoint_c} °C (einmalig für diese Störung).",
        now,
    )


@dataclass(frozen=True)
class SwitchDecision:
    """One cycle's plan for a switch-actuator assignment."""

    action: str  # ACTION_SWITCH_ON | ACTION_SWITCH_OFF
    reason_code: str
    reason: str
    cycle: emergency_cycle.CycleOutput


def plan_switch(
    prior_state: emergency_cycle.CycleState | None,
    cycle_input: emergency_cycle.CycleInput,
) -> SwitchDecision:
    """Rang 4 of plan 1.4: the taktung `domain.emergency_cycle` already computes,
    labelled with the vorrangtabelle's own action names. Runs unconditionally
    (Entscheidung 6: "Takt läuft auch im Modus 'Aus' und bei offenem Fenster")
    -- the caller never gates this call on mode/window, only on the zone's
    emergency stage.
    """
    output = emergency_cycle.advance(prior_state, cycle_input)
    action = ACTION_SWITCH_ON if output.decision.heating_requested else ACTION_SWITCH_OFF
    return SwitchDecision(action, output.decision.reason_code, output.decision.reason, output)


__all__ = [
    "ACTION_HANDOVER",
    "ACTION_NORMAL",
    "ACTION_NO_WRITE",
    "ACTION_SWITCH_OFF",
    "ACTION_SWITCH_ON",
    "KIND_SWITCH",
    "KIND_THERMOSTAT",
    "REASON_HANDOVER",
    "REASON_NO_WRITE_ERLEDIGT",
    "SwitchDecision",
    "ThermostatDecision",
    "plan_switch",
    "plan_thermostat",
]
