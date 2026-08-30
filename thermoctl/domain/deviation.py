"""Comparison of a shadow-run decision of our own against the legacy system's state at the
same moment.

Pure like the other modules under `domain/`: no database, no network, no clock. Bringing
together `ShadowDecision` and `AltsystemBeobachtung` — both for the same point in time,
from whichever source — is the caller's job.

A deviation is not an error, it is an observation: the legacy system has no hysteresis
(inventory document, pitfall 2) and switches at the setpoint on every cycle, so deviations
are to be expected during ongoing shadow-run comparison. The wording therefore stays
neutral, without judging which side is "correct".
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Classification(StrEnum):
    UEBEREINSTIMMUNG = "uebereinstimmung"
    ABWEICHUNG = "abweichung"
    KEIN_VERGLEICH = "kein_vergleich"


@dataclass(frozen=True)
class Comparison:
    einordnung: Classification
    text: str


def _temperature_text(value: Decimal | None) -> str:
    if value is None:
        return "unbekannt"
    return f"{value:.1f}".replace(".", ",")


def compare(
    *,
    would_heat: bool,
    measured_c: Decimal | None,
    setpoint_c: Decimal | None,
    legacy_system_heating: bool | None,
) -> Comparison:
    """Compares our own decision with the legacy system at the same point in time.

    `would_heat` is `ShadowDecision.would_heat` (our own decision, not acted on). `ist_c`/
    `soll_c` are the values this decision saw — they appear only in the plain-text
    message, they do not feed into the classification. `altsystem_heizt` is derived
    from `thermostatActualState` (`heat` -> True, `off` -> False); `None` means that no
    legacy-system value is available at the comparison time — in that case no comparison
    is possible at all, which is explicitly not a deviation.
    """
    if legacy_system_heating is None:
        return Comparison(
            Classification.KEIN_VERGLEICH,
            "Zum Vergleichszeitpunkt liegt kein Altsystem-Wert vor.",
        )

    if would_heat == legacy_system_heating:
        text = (
            "thermoctl und das Altsystem heizen beide."
            if would_heat
            else "thermoctl und das Altsystem heizen beide nicht."
        )
        return Comparison(Classification.UEBEREINSTIMMUNG, text)

    ist_text = _temperature_text(measured_c)
    setpoint_text = _temperature_text(setpoint_c)
    if would_heat:
        text = (
            "thermoctl haette geheizt, das Altsystem heizte nicht — "
            f"Ist {ist_text} °C, Soll {setpoint_text} °C."
        )
    else:
        text = (
            "thermoctl haette nicht geheizt, das Altsystem heizte — "
            f"Ist {ist_text} °C, Soll {setpoint_text} °C."
        )
    return Comparison(Classification.ABWEICHUNG, text)
