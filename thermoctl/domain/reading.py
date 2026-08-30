import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

log = logging.getLogger(__name__)


FIELD_TO_CAPABILITY: Final[dict[str, str]] = {
    # A controller's button press. It is stored as a reading like any other value --
    # and that is exactly how the interface later knows which buttons this device
    # even has, without anyone reading a datasheet.
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
        # Zigbee2MQTT can be configured to send local time without a timezone. Such a
        # value cannot be converted to UTC -- we do not know the zone. Interpreting it
        # as UTC would mean being off by two hours in summer, and a reading with a
        # wrong timestamp is worse than one with the imprecise but correct receipt
        # time: fault detection depends on it, and it decides based on exactly this
        # age.
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
    # Practically unreachable: `json.loads(..., parse_float=Decimal, parse_int=Decimal)`
    # only ever produces the types handled above. This line stays as a fallback in
    # case the parsing is ever called without these parsers -- a test for it would
    # have to bypass the parser and would only be testing itself.
    return None  # pragma: no cover


def readings_from_payload(
    payload: str | bytes, empfangen_am: datetime
) -> list[Reading]:
    """Tolerantly parses the known fields of a Zigbee2MQTT state message."""
    try:
        daten = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        log.warning("Zigbee2MQTT-Nutzlast ist kein gueltiges JSON")
        return []

    if not isinstance(daten, dict):
        # Valid JSON, but not a state message -- e.g. a list or a bare value. This
        # happens on foreign topics and is not a fault of this device.
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
