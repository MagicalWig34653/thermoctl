import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

log = logging.getLogger(__name__)


FELD_ZU_FAEHIGKEIT: Final[dict[str, str]] = {
    "battery": "battery",
    "contact": "contact",
    "current_heating_setpoint": "setpoint",
    "energy": "energy",
    "humidity": "humidity",
    "illuminance": "illuminance",
    "linkquality": "link_quality",
    "local_temperature": "temperature",
    "occupancy": "occupancy",
    "power": "power",
    "soil_moisture": "soil_moisture",
    "state": "switch",
    "temperature": "temperature",
}


@dataclass(frozen=True)
class Beobachtung:
    faehigkeit: str
    zahl: Decimal | None
    text: str | None
    gemessen_am: datetime


def _messzeitpunkt(wert: object, empfangen_am: datetime) -> datetime:
    if not isinstance(wert, str):
        return empfangen_am
    try:
        zeitpunkt = datetime.fromisoformat(wert)
    except ValueError:
        return empfangen_am
    if zeitpunkt.tzinfo is None:
        return empfangen_am
    return zeitpunkt.astimezone(UTC).replace(tzinfo=None)


def _wert(wert: object) -> tuple[Decimal | None, str | None] | None:
    if wert is None or isinstance(wert, (dict, list)):
        return None
    if isinstance(wert, bool):
        return None, "true" if wert else "false"
    if isinstance(wert, Decimal):
        return wert, None
    if isinstance(wert, str):
        return None, wert
    return None


def beobachtungen_aus_nutzlast(
    nutzlast: str | bytes, empfangen_am: datetime
) -> list[Beobachtung]:
    """Wertet bekannte Felder einer Zigbee2MQTT-Zustandsnachricht tolerant aus."""
    try:
        daten = json.loads(nutzlast, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        log.warning("Zigbee2MQTT-Nutzlast ist kein gueltiges JSON")
        return []

    if not isinstance(daten, dict):
        return []

    gemessen_am = _messzeitpunkt(daten.get("last_seen"), empfangen_am)
    beobachtungen: list[Beobachtung] = []
    for feld, rohwert in daten.items():
        faehigkeit = FELD_ZU_FAEHIGKEIT.get(feld)
        if faehigkeit is None:
            continue
        wert = _wert(rohwert)
        if wert is None:
            continue
        zahl, text = wert
        beobachtungen.append(Beobachtung(faehigkeit, zahl, text, gemessen_am))
    return beobachtungen
