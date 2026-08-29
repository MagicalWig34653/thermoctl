import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

STUNDEN_JE_TAG = 24
TAGE_JE_WOCHE = 7


@dataclass(frozen=True)
class Schaltpunktentwurf:
    weekday: int
    minute_of_day: int
    nacht: bool


def nachtstunden_lesen(blob: str) -> dict[int, frozenset[int]]:
    """Liest das ungepruefte Stundenraster, ohne unlesbare Teile zu uebernehmen."""
    ergebnis: dict[int, frozenset[int]] = {
        wochentag: frozenset() for wochentag in range(1, TAGE_JE_WOCHE + 1)
    }
    try:
        roh: Any = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        log.warning("Nachtstunden sind kein gueltiges JSON und werden als leer behandelt")
        return ergebnis

    if not isinstance(roh, list):
        log.warning("Nachtstunden sind kein Array und werden als leer behandelt")
        return ergebnis
    if len(roh) != TAGE_JE_WOCHE + 1:
        log.warning(
            "Nachtstunden haben %d statt acht Slots; lesbare Wochentage werden uebernommen",
            len(roh),
        )

    for wochentag in range(1, TAGE_JE_WOCHE + 1):
        if wochentag >= len(roh):
            continue
        slot = roh[wochentag]
        if not isinstance(slot, list):
            log.warning("Nachtstunden-Slot %d ist keine Liste und wird verworfen", wochentag)
            continue
        stunden: set[int] = set()
        for wert in slot:
            stunde = _stunde_lesen(wert)
            if stunde is None or stunde in stunden:
                log.warning(
                    "Ungueltige oder doppelte Nachtstunde in Slot %d wird verworfen",
                    wochentag,
                )
                continue
            stunden.add(stunde)
        ergebnis[wochentag] = frozenset(stunden)
    return ergebnis


def _stunde_lesen(wert: object) -> int | None:
    # Bool ist technisch ein Integer, war aber nie ein moeglicher Wert des PHP-Formulars.
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        stunde = wert
    elif isinstance(wert, str) and wert in {str(stunde) for stunde in range(STUNDEN_JE_TAG)}:
        stunde = int(wert)
    else:
        return None
    return stunde if 0 <= stunde < STUNDEN_JE_TAG else None


def schaltpunkte_aus_nachtstunden(
    nachtstunden: dict[int, frozenset[int]],
) -> list[Schaltpunktentwurf]:
    """Verdichtet ein Stundenraster zu den Zustandswechseln des Wochenrings."""
    wochenbild = [
        stunde in nachtstunden.get(wochentag, frozenset())
        for wochentag in range(1, TAGE_JE_WOCHE + 1)
        for stunde in range(STUNDEN_JE_TAG)
    ]
    wechsel = [
        index
        for index, nacht in enumerate(wochenbild)
        if nacht != wochenbild[index - 1]
    ]
    if not wechsel:
        return [Schaltpunktentwurf(weekday=1, minute_of_day=0, nacht=wochenbild[0])]
    return [
        Schaltpunktentwurf(
            weekday=index // STUNDEN_JE_TAG + 1,
            minute_of_day=index % STUNDEN_JE_TAG * 60,
            nacht=wochenbild[index],
        )
        for index in wechsel
    ]
