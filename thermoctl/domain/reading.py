import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

log = logging.getLogger(__name__)


FIELD_TO_CAPABILITY: Final[dict[str, str]] = {
    # Der Tastendruck eines Bediengeraets. Er wird wie jeder andere Wert als Messung
    # abgelegt -- und genau daraus weiss die Oberflaeche spaeter, welche Tasten dieses
    # Geraet ueberhaupt hat, ohne dass jemand ein Datenblatt liest.
    "action": "action",
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
class Reading:
    capability: str
    zahl: Decimal | None
    text: str | None
    gemessen_am: datetime


def _measured_at(value: object, empfangen_am: datetime) -> datetime:
    if not isinstance(value, str):
        return empfangen_am
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return empfangen_am
    if moment.tzinfo is None:
        # Zigbee2MQTT kann so eingestellt werden, dass es Ortszeit ohne Zeitzonenangabe
        # sendet. Ein solcher Wert ist nicht in UTC umrechenbar — wir wissen die Zone
        # nicht. Ihn als UTC zu deuten hiesse, im Sommer zwei Stunden danebenzuliegen,
        # und ein Messwert mit falschem Zeitstempel ist schlimmer als einer mit dem
        # ungenauen, aber richtigen Empfangszeitpunkt: an ihm haengt die Stoerungs-
        # erkennung, die genau ueber das Alter entscheidet.
        return empfangen_am
    return moment.astimezone(UTC).replace(tzinfo=None)


def _value(value: object) -> tuple[Decimal | None, str | None] | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return None, "true" if value else "false"
    if isinstance(value, Decimal):
        return value, None
    if isinstance(value, str):
        return None, value
    # Praktisch unerreichbar: `json.loads(..., parse_float=Decimal, parse_int=Decimal)`
    # liefert nur die oben behandelten Typen. Die Zeile bleibt als Rueckfall stehen,
    # falls die Auswertung einmal ohne diese Parser aufgerufen wird — ein Test dafuer
    # muesste den Parser umgehen und wuerde nur sich selbst pruefen.
    return None  # pragma: no cover


def readings_from_payload(
    payload: str | bytes, empfangen_am: datetime
) -> list[Reading]:
    """Wertet bekannte Felder einer Zigbee2MQTT-Zustandsnachricht tolerant aus."""
    try:
        daten = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        log.warning("Zigbee2MQTT-Nutzlast ist kein gueltiges JSON")
        return []

    if not isinstance(daten, dict):
        # Gueltiges JSON, aber keine Zustandsnachricht — etwa eine Liste oder ein nackter
        # Wert. Kommt bei fremden Topics vor und ist kein Fehler dieses Geraets.
        return []

    gemessen_am = _measured_at(daten.get("last_seen"), empfangen_am)
    readings: list[Reading] = []
    for feld, raw_value in daten.items():
        capability = FIELD_TO_CAPABILITY.get(feld)
        if capability is None:
            continue
        value = _value(raw_value)
        if value is None:
            continue
        zahl, text = value
        readings.append(Reading(capability, zahl, text, gemessen_am))
    return readings
