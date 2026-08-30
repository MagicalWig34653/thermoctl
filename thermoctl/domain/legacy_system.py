"""Pure parsing of the legacy system's MQTT topics (shadow run, sub-project 4).

The legacy system publishes its state under `heizung/thermostate/<id>/<attribut>/get`
(inventory document, section 5). This module turns that into an `AltsystemBeobachtung` —
no database, no network, no clock. Just like `beobachtung.py` for Zigbee2MQTT, the
parsing is tolerant: a foreign topic, a `/set` instead of `/get`, an unknown attribute
or an unreadable value are not exceptions, but a result of `None`.

This function only reads — it triggers no `publish` and changes nothing on the legacy
system.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

log = logging.getLogger(__name__)

_PRAEFIX: Final = ("heizung", "thermostate")
_SUFFIX: Final = "get"

# Attributes whose value is a temperature in degrees Celsius.
_NUMBER_ATTRIBUTE: Final = frozenset({"temperatureActual", "temperatureTarget"})

# Attributes whose value is taken over as text — the full list from the inventory
# document. `thermostatActualState` and `thermostatTargetState` are `off`/`heat`
# (theoretically also `cool`/`auto`, see pitfall 7 of the inventory document), but this
# parsing does not enforce that: it only stores whatever arrived.
_TEXT_ATTRIBUTE: Final = frozenset(
    {
        "preset_mode",
        "thermostatTargetState",
        "thermostatActualState",
        "thermostatActualStateHA",
        "availability",
    }
)


@dataclass(frozen=True)
class LegacyReading:
    """A single parsed reading of the legacy system for one thermostat."""

    thermostat_id: int
    attribut: str
    text: str | None
    zahl: Decimal | None


def reading_from_topic(topic: str, payload: bytes | str) -> LegacyReading | None:
    """Tolerantly parses a single legacy-system topic together with its payload.

    Returns `None` for everything that is not a thermostat state reading: a foreign
    topic prefix (e.g. `zigbee2mqtt/...`), a config topic
    (`heizung/config/<schluessel>/get`), a `/set` command instead of `/get`, a
    non-numeric thermostat id, a payload not readable as UTF-8, an unknown attribute,
    or — for a temperature attribute — a value not readable as a number. None of these
    cases is a bug in this function; all of them get logged and lead to `None`, never
    to an exception.
    """
    teile = topic.split("/")
    if len(teile) != 5 or tuple(teile[:2]) != _PRAEFIX or teile[4] != _SUFFIX:
        log.debug("Kein Altsystem-Thermostat-Zustandstopic", extra={"topic": topic})
        return None

    thermostat_teil, attribut = teile[2], teile[3]
    try:
        thermostat_id = int(thermostat_teil)
    except ValueError:
        log.warning(
            "Altsystem-Topic ohne lesbare Thermostat-Kennung",
            extra={"topic": topic},
        )
        return None

    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError:
        log.warning("Altsystem-Nutzlast ist nicht als UTF-8 lesbar", extra={"topic": topic})
        return None
    text = text.strip()

    if attribut in _NUMBER_ATTRIBUTE:
        try:
            zahl = Decimal(text)
        except InvalidOperation:
            log.warning(
                "Altsystem-Temperaturwert ist nicht lesbar",
                extra={"topic": topic, "wert": text},
            )
            return None
        return LegacyReading(thermostat_id, attribut, None, zahl)

    if attribut in _TEXT_ATTRIBUTE:
        return LegacyReading(thermostat_id, attribut, text, None)

    log.info(
        "Altsystem-Attribut wird nicht ausgewertet",
        extra={"topic": topic, "attribut": attribut},
    )
    return None
