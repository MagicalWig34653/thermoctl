"""The control decision: a situation becomes a decision, together with its reasoning.

Pure in the sense of CLAUDE.md and section 3 of the specification: no database, no
network, no clock. Everything the decision needs sits in `Lage`. That is exactly why it
is exhaustively testable (`tests/test_regelung.py`), and exactly why sub-project 4 can
arm it unchanged.

In this phase nothing is actually switched (dry run, section 1 of the specification) —
the result only ends up in the shadow log.
"""

from dataclasses import dataclass
from decimal import Decimal

from thermoctl.domain.zone_settings import ControlParameters

# These codes correspond literally to the shadow_decision.outcome_code column from
# section 4 of the specification. Anyone needing a new code here must add it there first.
REASON_CODE_HEATING = "heizen"
REASON_CODE_OFF = "aus"
REASON_CODE_UNCHANGED = "unveraendert"
REASON_CODE_BLOCKED_MINIMUM_DURATION = "gesperrt_mindestdauer"
REASON_CODE_WINDOW_OPEN = "fenster_offen"
REASON_CODE_FROST_SENSOR_FAILURE = "frostschutz_sensorausfall"
REASON_CODE_NO_SOURCE = "keine_quelle"


@dataclass(frozen=True)
class Situation:
    """Everything the decision sees — unchanged from section 6 of the specification."""

    measured_c: Decimal | None
    setpoint_c: Decimal
    setpoint_reason: str
    # The plant's frost-protection setpoint. Kept separate from `soll_c` because it
    # still applies even when `soll_c` currently says something completely different:
    # on a failed sensor, control falls back to it instead of relying on a setpoint it
    # can no longer verify.
    frost_c: Decimal
    operating_mode: str  # auto | manual | off
    heating_now: bool
    held_for_s: int | None  # how long the current state has already held
    window_open: bool
    window_closed_for_s: int | None
    sensor_status: str  # ok | veraltet | keine_quelle
    parameter: ControlParameters


@dataclass(frozen=True)
class Decision:
    heating: bool
    reason_code: str
    reason: str


def decide(situation: Situation) -> Decision:
    """The precedence from section 6 of the specification, rule by rule.

    The first matching rule wins and returns immediately — deliberately written as an
    early exit per rule instead of one large condition, so the precedence in the code
    looks the same as in the specification and nobody has to reconstruct it from an
    expression.
    """
    # Rule 1 — sensor failure trumps everything. Without a reliable current value,
    # "heat fully" is wrong (overheating with no feedback) — but "permanently off" is
    # just as wrong, and more dangerous: that is exactly how a pipe freezes in January.
    # So control falls back to the frost-protection setpoint and keeps controlling
    # against the last known value. The frost-protection value is low; with it the
    # plant can heat at most to an unproblematic level, but keeps the home above the
    # freezing point.
    #
    # If there is no value at all, only "off" remains — there is nothing to control
    # against.
    if situation.sensor_status == "keine_quelle" or situation.measured_c is None:
        return Decision(
            heating=False,
            reason_code=REASON_CODE_NO_SOURCE,
            reason=(
                "Keine verwertbare Messung für die Zone — es gibt nichts, woran zu regeln "
                "wäre. Ventil bleibt zu."
            ),
        )

    # The sensor's calibration is a property of the measurement, not of the rule —
    # which is why it is applied to the current value here, and only here, before any
    # further rule.
    measured_c = situation.measured_c + situation.parameter.temperature_offset_k

    # Rule 2 — operating mode 'off' means frost protection, not powered down. The
    # caller already resolves the setpoint before us (`aufgeloester_sollwert`), which
    # returns the frost-protection value for 'off'. So `lage.soll_c` is already the
    # frost-protection setpoint in the 'off' case, and the "normal rule" is exactly
    # what follows from rule 3 onward — this function therefore does not need a
    # separate branch for the operating mode, only the origin of the setpoint
    # (`soll_grund`) carries through unchanged into the reasoning.

    # The effective setpoint. On a failed sensor this is the frost-protection value
    # (rule 1), otherwise the zone's resolved setpoint. From here on the same rule
    # runs in both cases — that is the core of "off means frost protection, not
    # powered down".
    sensor_failed = situation.sensor_status == "veraltet"
    setpoint_c = situation.frost_c if sensor_failed else situation.setpoint_c
    setpoint_reason = (
        f"Sensorwert veraltet — Frostschutz {situation.frost_c} °C statt {situation.setpoint_c} °C"
        if sensor_failed
        else situation.setpoint_reason
    )

    # Rule 3 — window open: off, regardless of temperature.
    if situation.window_open:
        return Decision(
            heating=False,
            reason_code=REASON_CODE_WINDOW_OPEN,
            reason=(
                f"Fenster offen — Ist {measured_c} °C, "
                f"Soll {setpoint_c} °C ({setpoint_reason})."
            ),
        )

    # Rule 4 — resume delay: the window is closed, but the room is still cooling down
    # from it. 'None' for fenster_zu_seit_s means "no pending resume delay" (the
    # window has never been open since recording began) — then there is nothing to
    # wait out.
    delay = situation.parameter.window_resume_delay_seconds
    if (
        situation.window_closed_for_s is not None
        and situation.window_closed_for_s < delay
    ):
        return Decision(
            heating=False,
            reason_code=REASON_CODE_OFF,
            reason=(
                f"Fenster seit {situation.window_closed_for_s}s zu, Wiederanlauf erst nach "
                f"{delay}s — Raum kühlt noch nach."
            ),
        )

    # Rule 5 — minimum switch duration protects the valve from short-cycling. 'None'
    # for seit_s means "duration of the current state unknown" — typically the first
    # cycle after a restart, with no history. Imposing a lock against a duration we do
    # not know would itself be arbitrary; so the lock only applies when seit_s is known
    # AND too short. A freshly started service may therefore decide by hysteresis right
    # away, instead of waiting out a deadline that never started running.
    minimum_duration = (
        situation.parameter.min_on_seconds
        if situation.heating_now
        else situation.parameter.min_off_seconds
    )
    if situation.held_for_s is not None and situation.held_for_s < minimum_duration:
        state = "Heizen" if situation.heating_now else "Aus"
        return Decision(
            heating=situation.heating_now,
            reason_code=REASON_CODE_BLOCKED_MINIMUM_DURATION,
            reason=(
                f"Zustand '{state}' erst seit {situation.held_for_s}s, "
                f"Mindestdauer {minimum_duration}s "
                "— Ventil bleibt unangetastet."
            ),
        )

    # Rule 6 — hysteresis. The legacy system does not have it
    # (`if ist < soll: an, sonst aus`) and switches at the setpoint on every cycle;
    # `h` is exactly the band that prevents that.
    h = situation.parameter.hysteresis_k
    if not situation.heating_now and measured_c < setpoint_c - h:
        return Decision(
            heating=True,
            reason_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
                else REASON_CODE_HEATING
            ),
            reason=(
                f"Ist {measured_c} °C unter Soll {setpoint_c} °C minus Hysterese {h}K "
                f"({setpoint_reason})."
            ),
        )
    if situation.heating_now and measured_c > setpoint_c + h:
        return Decision(
            heating=False,
            reason_code=REASON_CODE_OFF,
            reason=(
                f"Ist {measured_c} °C über Soll {setpoint_c} °C plus Hysterese {h}K "
                f"({setpoint_reason})."
            ),
        )
    return Decision(
        heating=situation.heating_now,
        reason_code=(
            REASON_CODE_FROST_SENSOR_FAILURE if sensor_failed
            else REASON_CODE_UNCHANGED
        ),
        reason=(
            f"Ist {measured_c} °C innerhalb der Hysterese um Soll {setpoint_c} °C ± {h}K "
            f"({setpoint_reason}) — Zustand bleibt."
        ),
    )
