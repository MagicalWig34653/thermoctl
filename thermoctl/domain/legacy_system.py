"""Reine Auswertung der Altsystem-MQTT-Topics (Vergleichsbetrieb, Teilprojekt 4).

Das Altsystem veroeffentlicht seinen Zustand unter `heizung/thermostate/<id>/<attribut>/get`
(Bestandsaufnahme, Abschnitt 5). Dieses Modul macht daraus eine `AltsystemBeobachtung` —
ohne Datenbank, ohne Netz, ohne Uhr. Genau wie `beobachtung.py` fuer Zigbee2MQTT ist die
Auswertung tolerant: ein fremdes Topic, ein `/set` statt `/get`, ein unbekanntes Attribut
oder ein unlesbarer Wert sind keine Ausnahme, sondern ein Ergebnis von `None`.

Diese Funktion liest nur — sie loest kein `publish` aus und veraendert nichts am Altsystem.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

log = logging.getLogger(__name__)

_PRAEFIX: Final = ("heizung", "thermostate")
_SUFFIX: Final = "get"

# Attribute, deren Wert eine Temperatur in Grad Celsius ist.
_NUMBER_ATTRIBUTE: Final = frozenset({"temperatureActual", "temperatureTarget"})

# Attribute, deren Wert als Text uebernommen wird — die vollstaendige Liste aus der
# Bestandsaufnahme. `thermostatActualState` und `thermostatTargetState` sind `off`/`heat`
# (theoretisch auch `cool`/`auto`, siehe Fallstrick 7 der Bestandsaufnahme), aber diese
# Auswertung erzwingt das nicht: sie legt nur ab, was ankam.
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
    """Eine einzelne ausgewertete Beobachtung des Altsystems zu einem Thermostat."""

    thermostat_id: int
    attribut: str
    text: str | None
    zahl: Decimal | None


def reading_from_topic(topic: str, payload: bytes | str) -> LegacyReading | None:
    """Wertet ein einzelnes Altsystem-Topic samt Nutzlast tolerant aus.

    Liefert `None` fuer alles, was keine Thermostat-Zustandsbeobachtung ist: ein fremdes
    Topic-Praefix (z. B. `zigbee2mqtt/...`), ein Konfigurations-Topic
    (`heizung/config/<schluessel>/get`), ein `/set`-Befehl statt `/get`, eine
    nicht-numerische Thermostat-Kennung, eine nicht als UTF-8 lesbare Nutzlast, ein
    unbekanntes Attribut oder — bei einem Temperaturattribut — ein nicht als Zahl lesbarer
    Wert. Keiner dieser Faelle ist ein Fehler dieser Funktion; alle werden protokolliert und
    fuehren zu `None`, nie zu einer Ausnahme.
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
