"""Vergleich einer eigenen Schattenentscheidung mit dem gleichzeitigen Altsystem-Zustand.

Rein wie die uebrigen Module unter `domain/`: keine Datenbank, kein Netz, keine Uhr. Die
Zusammenfuehrung von `ShadowDecision` und `AltsystemBeobachtung` — beide zum selben
Zeitpunkt, aus welcher Quelle auch immer — ist Aufgabe des Aufrufers.

Eine Abweichung ist keine Fehlermeldung, sondern eine Beobachtung: Das Altsystem hat keine
Hysterese (Bestandsaufnahme, Fallstrick 2) und schaltet am Sollwert in jedem Zyklus um, also
sind Abweichungen im laufenden Vergleichsbetrieb erwartbar. Die Formulierung bleibt deshalb
neutral, ohne Wertung, wer "richtig" liegt.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Einordnung(StrEnum):
    UEBEREINSTIMMUNG = "uebereinstimmung"
    ABWEICHUNG = "abweichung"
    KEIN_VERGLEICH = "kein_vergleich"


@dataclass(frozen=True)
class Vergleich:
    einordnung: Einordnung
    text: str


def _temperature_text(value: Decimal | None) -> str:
    if value is None:
        return "unbekannt"
    return f"{value:.1f}".replace(".", ",")


def vergleichen(
    *,
    would_heat: bool,
    ist_c: Decimal | None,
    soll_c: Decimal | None,
    altsystem_heizt: bool | None,
) -> Vergleich:
    """Vergleicht die eigene Entscheidung mit dem Altsystem zum selben Zeitpunkt.

    `would_heat` ist `ShadowDecision.would_heat` (die eigene Entscheidung, ungeschaltet).
    `ist_c`/`soll_c` sind die Werte, die diese Entscheidung gesehen hat — sie erscheinen
    nur im Klartext, gehen nicht in die Einordnung ein. `altsystem_heizt` ist aus
    `thermostatActualState` abgeleitet (`heat` -> True, `off` -> False); `None` heisst, dass
    zum Vergleichszeitpunkt kein Altsystem-Wert vorliegt — dann ist gar kein Vergleich
    moeglich, was ausdruecklich keine Abweichung ist.
    """
    if altsystem_heizt is None:
        return Vergleich(
            Einordnung.KEIN_VERGLEICH,
            "Zum Vergleichszeitpunkt liegt kein Altsystem-Wert vor.",
        )

    if would_heat == altsystem_heizt:
        text = (
            "thermoctl und das Altsystem heizen beide."
            if would_heat
            else "thermoctl und das Altsystem heizen beide nicht."
        )
        return Vergleich(Einordnung.UEBEREINSTIMMUNG, text)

    ist_text = _temperature_text(ist_c)
    soll_text = _temperature_text(soll_c)
    if would_heat:
        text = (
            "thermoctl haette geheizt, das Altsystem heizte nicht — "
            f"Ist {ist_text} °C, Soll {soll_text} °C."
        )
    else:
        text = (
            "thermoctl haette nicht geheizt, das Altsystem heizte — "
            f"Ist {ist_text} °C, Soll {soll_text} °C."
        )
    return Vergleich(Einordnung.ABWEICHUNG, text)
