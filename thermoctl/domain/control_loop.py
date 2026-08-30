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
GRUND_CODE_HEIZEN = "heizen"
GRUND_CODE_AUS = "aus"
REASON_CODE_UNCHANGED = "unveraendert"
REASON_CODE_BLOCKED_MINIMUM_DURATION = "gesperrt_mindestdauer"
REASON_CODE_WINDOW_OPEN = "fenster_offen"
REASON_CODE_FROST_SENSOR_FAILURE = "frostschutz_sensorausfall"
REASON_CODE_NO_SOURCE = "keine_quelle"


@dataclass(frozen=True)
class Lage:
    """Everything the decision sees — unchanged from section 6 of the specification."""

    ist_c: Decimal | None
    soll_c: Decimal
    soll_grund: str
    # The plant's frost-protection setpoint. Kept separate from `soll_c` because it
    # still applies even when `soll_c` currently says something completely different:
    # on a failed sensor, control falls back to it instead of relying on a setpoint it
    # can no longer verify.
    frost_c: Decimal
    operating_mode: str  # auto | manual | off
    heizt_gerade: bool
    seit_s: int | None  # how long the current state has already held
    window_open: bool
    window_closed_for_s: int | None
    sensor_status: str  # ok | veraltet | keine_quelle
    parameter: ControlParameters


@dataclass(frozen=True)
class Entscheidung:
    heizen: bool
    grund_code: str
    grund: str


def entscheiden(lage: Lage) -> Entscheidung:
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
    if lage.sensor_status == "keine_quelle" or lage.ist_c is None:
        return Entscheidung(
            heizen=False,
            grund_code=REASON_CODE_NO_SOURCE,
            grund=(
                "Keine verwertbare Messung fuer die Zone — es gibt nichts, woran zu regeln "
                "waere. Ventil bleibt zu."
            ),
        )

    # The sensor's calibration is a property of the measurement, not of the rule —
    # which is why it is applied to the current value here, and only here, before any
    # further rule.
    ist_c = lage.ist_c + lage.parameter.temperature_offset_k

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
    sensor_ausgefallen = lage.sensor_status == "veraltet"
    soll_c = lage.frost_c if sensor_ausgefallen else lage.soll_c
    soll_grund = (
        f"Sensorwert veraltet — Frostschutz {lage.frost_c} °C statt {lage.soll_c} °C"
        if sensor_ausgefallen
        else lage.soll_grund
    )

    # Rule 3 — window open: off, regardless of temperature.
    if lage.window_open:
        return Entscheidung(
            heizen=False,
            grund_code=REASON_CODE_WINDOW_OPEN,
            grund=f"Fenster offen — Ist {ist_c} °C, Soll {soll_c} °C ({soll_grund}).",
        )

    # Rule 4 — resume delay: the window is closed, but the room is still cooling down
    # from it. 'None' for fenster_zu_seit_s means "no pending resume delay" (the
    # window has never been open since recording began) — then there is nothing to
    # wait out.
    verzoegerung = lage.parameter.window_resume_delay_seconds
    if (
        lage.window_closed_for_s is not None
        and lage.window_closed_for_s < verzoegerung
    ):
        return Entscheidung(
            heizen=False,
            grund_code=GRUND_CODE_AUS,
            grund=(
                f"Fenster seit {lage.window_closed_for_s}s zu, Wiederanlauf erst nach "
                f"{verzoegerung}s — Raum kuehlt noch nach."
            ),
        )

    # Rule 5 — minimum switch duration protects the valve from short-cycling. 'None'
    # for seit_s means "duration of the current state unknown" — typically the first
    # cycle after a restart, with no history. Imposing a lock against a duration we do
    # not know would itself be arbitrary; so the lock only applies when seit_s is known
    # AND too short. A freshly started service may therefore decide by hysteresis right
    # away, instead of waiting out a deadline that never started running.
    minimum_duration = (
        lage.parameter.min_on_seconds if lage.heizt_gerade else lage.parameter.min_off_seconds
    )
    if lage.seit_s is not None and lage.seit_s < minimum_duration:
        state = "Heizen" if lage.heizt_gerade else "Aus"
        return Entscheidung(
            heizen=lage.heizt_gerade,
            grund_code=REASON_CODE_BLOCKED_MINIMUM_DURATION,
            grund=(
                f"Zustand '{state}' erst seit {lage.seit_s}s, Mindestdauer {minimum_duration}s "
                "— Ventil bleibt unangetastet."
            ),
        )

    # Rule 6 — hysteresis. The legacy system does not have it
    # (`if ist < soll: an, sonst aus`) and switches at the setpoint on every cycle;
    # `h` is exactly the band that prevents that.
    h = lage.parameter.hysteresis_k
    if not lage.heizt_gerade and ist_c < soll_c - h:
        return Entscheidung(
            heizen=True,
            grund_code=(
                REASON_CODE_FROST_SENSOR_FAILURE if sensor_ausgefallen
                else GRUND_CODE_HEIZEN
            ),
            grund=(
                f"Ist {ist_c} °C unter Soll {soll_c} °C minus Hysterese {h}K "
                f"({soll_grund})."
            ),
        )
    if lage.heizt_gerade and ist_c > soll_c + h:
        return Entscheidung(
            heizen=False,
            grund_code=GRUND_CODE_AUS,
            grund=(
                f"Ist {ist_c} °C ueber Soll {soll_c} °C plus Hysterese {h}K "
                f"({soll_grund})."
            ),
        )
    return Entscheidung(
        heizen=lage.heizt_gerade,
        grund_code=(
            REASON_CODE_FROST_SENSOR_FAILURE if sensor_ausgefallen
            else REASON_CODE_UNCHANGED
        ),
        grund=(
            f"Ist {ist_c} °C innerhalb der Hysterese um Soll {soll_c} °C ± {h}K "
            f"({soll_grund}) — Zustand bleibt."
        ),
    )
