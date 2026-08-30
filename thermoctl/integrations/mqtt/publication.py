"""Reine MQTT-Topics und Home-Assistant-Discovery-Nutzlasten.

Die stabile Datenbankkennung der Zone steht im Topic; der aenderbare, moeglicherweise
Leerzeichen oder Umlaute enthaltende Anzeigename bleibt in der Nutzlast. Zustand und Befehl
liegen in getrennten Teilbaeumen. Dadurch trifft ein Abonnement auf ``zustand/#`` niemals
einen Befehl, und Zustands-Topics brauchen kein missverstaendliches ``/get``-Suffix.

Diese Funktionen werden erst in Phase 4/5 an einen sendenden Adapter angeschlossen. Der
Vertrag entsteht schon jetzt, damit Topic-Struktur und Discovery ohne Zugriff auf die echte
Heizungsanlage vollstaendig geprueft werden koennen.
"""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C


@dataclass(frozen=True)
class StateTopics:
    """Die einzeln abonnierbaren Zustandswerte einer Zone."""

    current_temperature: str
    setpoint: str
    operating_mode: str
    sensor_state: str
    wuerde_heizen: str
    last_switch: str
    next_switch: str


@dataclass(frozen=True)
class CommandTopics:
    """Die von Zustandsabonnements getrennten Befehle einer Zone."""

    setpoint: str
    operating_mode: str
    boost: str


@dataclass(frozen=True)
class DiscoveryMessage:
    """Topic und Nutzlast einer spaeter zu sendenden Discovery-Nachricht."""

    topic: str
    payload: str


def _praefix(praefix: str) -> str:
    bereinigt = praefix.strip("/")
    if not bereinigt or any(zeichen in bereinigt for zeichen in ("+", "#", "\0")):
        raise ValueError("Das MQTT-Praefix muss gueltig sein und darf keine Wildcards enthalten")
    return bereinigt


def _zonebasis(zone_id: int, praefix: str) -> str:
    if zone_id < 1:
        raise ValueError("Die Zonenkennung muss groesser als null sein")
    return f"{_praefix(praefix)}/zones/{zone_id}"


def states_topics(zone_id: int, praefix: str = "thermoctl") -> StateTopics:
    """Baut alle Zustands-Topics einer Zone."""
    basis = f"{_zonebasis(zone_id, praefix)}/state"
    return StateTopics(
        current_temperature=f"{basis}/current_temperature",
        setpoint=f"{basis}/setpoint",
        operating_mode=f"{basis}/operating_mode",
        sensor_state=f"{basis}/sensor_state",
        wuerde_heizen=f"{basis}/would_heat",
        last_switch=f"{basis}/last_switch",
        next_switch=f"{basis}/next_switch",
    )


def command_topics(zone_id: int, praefix: str = "thermoctl") -> CommandTopics:
    """Baut die getrennten Befehls-Topics einer Zone."""
    basis = f"{_zonebasis(zone_id, praefix)}/command"
    return CommandTopics(
        setpoint=f"{basis}/setpoint",
        operating_mode=f"{basis}/operating_mode",
        boost=f"{basis}/boost",
    )


def mode_topics(zone_id: int, mode_id: int, praefix: str = "thermoctl") -> tuple[str, str]:
    """Zustand und Befehl fuer die Solltemperatur **eines** Modus dieser Zone."""
    if mode_id < 1:
        raise ValueError("Die Moduskennung muss groesser als null sein")
    basis = _zonebasis(zone_id, praefix)
    return (f"{basis}/state/mode/{mode_id}", f"{basis}/command/mode/{mode_id}")


def parameter_topics(zone_id: int, name: str, praefix: str = "thermoctl") -> tuple[str, str]:
    """Zustand und Befehl fuer **einen** Regelparameter dieser Zone."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"Kein gueltiger Parametername: {name!r}")
    basis = _zonebasis(zone_id, praefix)
    return (f"{basis}/state/parameter/{name}", f"{basis}/command/parameter/{name}")


def armed_topic(praefix: str = "thermoctl") -> str:
    """Ob die Regelung wirklich schaltet -- eine Aussage fuer den ganzen Dienst.

    Der Trockenlauf stand bis hierher im *Namen* jeder Zone. Das war gut sichtbar und
    genau deshalb falsch: Home Assistant leitet die Entitaetskennung beim ersten
    Auftauchen aus dem Namen ab, und eine Zone, die zuerst im Trockenlauf erschien,
    hiess danach fuer immer `climate.thermoctl_zone_1_trockenlauf`. Der Betriebszustand
    gehoert in eine eigene Entitaet, nicht in den Namen einer anderen.
    """
    return f"{_praefix(praefix)}/state/armed"


def availability_topic(praefix: str = "thermoctl") -> str:
    """Baut das gemeinsame Last-Will-Topic des Dienstes."""
    return f"{_praefix(praefix)}/availability"


def _identifier(praefix: str) -> str:
    """Das Praefix, auf das reduziert, was in einer Discovery-Kennung stehen darf."""
    ohne_akzente = unicodedata.normalize("NFKD", _praefix(praefix)).encode("ascii", "ignore")
    identifier = re.sub(rb"[^a-zA-Z0-9_-]+", b"_", ohne_akzente).decode().strip("_").lower()
    if not identifier:
        raise ValueError("Das MQTT-Praefix ergibt keine gueltige Discovery-Kennung")
    return identifier


def _objekt_id(zone_id: int, praefix: str) -> str:
    _zonebasis(zone_id, praefix)
    return f"{_identifier(praefix)}_zone_{zone_id}"


def discovery_config_topic(zone_id: int, praefix: str = "thermoctl") -> str:
    """Baut das Home-Assistant-Config-Topic einer Climate-Zone."""
    _zonebasis(zone_id, praefix)
    return f"homeassistant/climate/{_objekt_id(zone_id, praefix)}/config"


def _config_topic(komponente: str, objekt_id: str) -> str:
    return f"homeassistant/{komponente}/{objekt_id}/config"


def _devicesblock(zone_id: int, zone_name: str, praefix: str) -> dict[str, Any]:
    """Ein Home-Assistant-Geraet je Zone, unter dem Dienst als uebergeordnetem.

    Vorher hingen alle Entitaeten an einem einzigen Geraet "thermoctl". Bei einer
    Handvoll Zonen mit je einem Dutzend Reglern ist das eine unsortierte Liste; nach
    Zonen gruppiert steht beieinander, was zusammengehoert. Die `unique_id` der
    Entitaeten aendert sich dadurch nicht -- Home Assistant haengt eine bestehende
    Entitaet nur um, die Entitaetskennung bleibt.
    """
    return {
        "identifiers": [f"thermoctl:{_praefix(praefix)}:zone:{zone_id}"],
        "name": zone_name,
        "manufacturer": "thermoctl",
        "via_device": f"thermoctl:{_praefix(praefix)}",
    }


def _grundgeruest(zone_id: int, zone_name: str, praefix: str) -> dict[str, Any]:
    """Was jede Entitaet einer Zone gleich traegt."""
    return {
        "device": _devicesblock(zone_id, zone_name, praefix),
        "availability_topic": availability_topic(praefix),
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def _als_json(daten: dict[str, Any]) -> str:
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def discovery_payload(
    zone_id: int,
    zone_name: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    praefix: str = "thermoctl",
) -> str:
    """Baut die JSON-Nutzlast fuer eine Home-Assistant-Climate-Zone."""
    if not zone_name.strip():
        raise ValueError("Der Zonenname darf nicht leer sein")
    if temp_step <= 0:
        raise ValueError("Der Temperaturschritt muss groesser als null sein")

    state = states_topics(zone_id, praefix)
    command = command_topics(zone_id, praefix)
    objekt_id = _objekt_id(zone_id, praefix)
    daten: dict[str, Any] = {
        **_grundgeruest(zone_id, zone_name, praefix),
        "name": None,
        "unique_id": objekt_id,
        # Ausdruecklich gesetzt, damit die Entitaetskennung nicht aus dem Namen
        # abgeleitet wird. Sonst haengt sie an der Schreibweise des Zonennamens von
        # damals -- und aenderte sich mit jeder Umbenennung der Zone.
        "object_id": objekt_id,
        "current_temperature_topic": state.current_temperature,
        "temperature_state_topic": state.setpoint,
        "temperature_command_topic": command.setpoint,
        "mode_state_topic": state.operating_mode,
        "mode_command_topic": command.operating_mode,
        "mode_state_template": "{{ 'heat' if value == 'manual' else value }}",
        "mode_command_template": "{{ 'manual' if value == 'heat' else value }}",
        "action_topic": state.wuerde_heizen,
        "action_template": "{{ 'heating' if value == 'true' else 'idle' }}",
        "modes": ["auto", "heat", "off"],
        # Aus der Domaene, nicht abgeschrieben: Home Assistant zeigt damit
        # denselben Bereich an, den der Dienst auch annimmt.
        "min_temp": float(MINIMUM_TEMPERATURE_C),
        "max_temp": float(MAXIMUM_TEMPERATURE_C),
        "temp_step": float(temp_step),
        "temperature_unit": "C",
    }
    return _als_json(daten)


def zone_discovery(
    zone_id: int,
    zone_name: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    praefix: str = "thermoctl",
) -> DiscoveryMessage:
    """Buendelt Config-Topic und Discovery-Nutzlast fuer eine Zone."""
    return DiscoveryMessage(
        discovery_config_topic(zone_id, praefix),
        discovery_payload(zone_id, zone_name, temp_step=temp_step, praefix=praefix),
    )


def discovery_removal(zone_id: int, praefix: str = "thermoctl") -> DiscoveryMessage:
    """Baut die leere Discovery-Nachricht zum Entfernen einer Zone."""
    return DiscoveryMessage(discovery_config_topic(zone_id, praefix), "")


def boost_discovery(
    zone_id: int, zone_name: str, praefix: str = "thermoctl"
) -> DiscoveryMessage:
    """Der Knopf, der die naechste Schaltung vorzieht."""
    objekt_id = f"{_objekt_id(zone_id, praefix)}_boost"
    daten: dict[str, Any] = {
        **_grundgeruest(zone_id, zone_name, praefix),
        "name": "Boost",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "command_topic": command_topics(zone_id, praefix).boost,
        "payload_press": "boost",
        "icon": "mdi:fast-forward",
    }
    return DiscoveryMessage(_config_topic("button", objekt_id), _als_json(daten))


def timestamp_discovery(
    zone_id: int,
    zone_name: str,
    kind: str,
    label: str,
    praefix: str = "thermoctl",
) -> DiscoveryMessage:
    """Ein Zeitpunkt als Sensor -- 'letzte Schaltung' und 'naechster Moduswechsel'.

    `device_class: timestamp` heisst: Home Assistant erwartet ISO-8601 mit Zeitzone und
    zeigt selbst "vor 12 Minuten" an. Deshalb steht hier kein vorformatierter Text --
    die Anzeigesprache gehoert dorthin, wo sie gelesen wird.
    """
    if kind not in ("last_switch", "next_switch"):
        raise ValueError(f"Unbekannte Zeitstempelart: {kind!r}")
    objekt_id = f"{_objekt_id(zone_id, praefix)}_{kind}"
    daten: dict[str, Any] = {
        **_grundgeruest(zone_id, zone_name, praefix),
        "name": label,
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": getattr(states_topics(zone_id, praefix), kind),
        "device_class": "timestamp",
    }
    return DiscoveryMessage(_config_topic("sensor", objekt_id), _als_json(daten))


def mode_discovery(
    zone_id: int,
    zone_name: str,
    mode_id: int,
    mode_name: str,
    praefix: str = "thermoctl",
    temp_step: Decimal = Decimal("0.5"),
) -> DiscoveryMessage:
    """Die Solltemperatur eines Modus als Zahleneingabe.

    Der Thermostat zeigt immer nur den Modus, der gerade gilt. Wer die Nachtabsenkung
    am Nachmittag verstellen will, braucht dafuer eine eigene Eingabe -- sonst muesste
    er bis zum Abend warten.
    """
    objekt_id = f"{_objekt_id(zone_id, praefix)}_modus_{mode_id}"
    daten: dict[str, Any] = {
        **_grundgeruest(zone_id, zone_name, praefix),
        "name": f"Sollwert {mode_name}",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": mode_topics(zone_id, mode_id, praefix)[0],
        "command_topic": mode_topics(zone_id, mode_id, praefix)[1],
        "min": float(MINIMUM_TEMPERATURE_C),
        "max": float(MAXIMUM_TEMPERATURE_C),
        "step": float(temp_step),
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "mode": "box",
    }
    return DiscoveryMessage(_config_topic("number", objekt_id), _als_json(daten))


def parameter_discovery(
    zone_id: int,
    zone_name: str,
    name: str,
    label: str,
    minimum: Decimal,
    maximum: Decimal,
    step: Decimal,
    einheit: str | None = None,
    praefix: str = "thermoctl",
) -> DiscoveryMessage:
    """Ein Regelparameter der Zone als Zahleneingabe."""
    state, command = parameter_topics(zone_id, name, praefix)
    objekt_id = f"{_objekt_id(zone_id, praefix)}_parameter_{name}"
    daten: dict[str, Any] = {
        **_grundgeruest(zone_id, zone_name, praefix),
        "name": label,
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": state,
        "command_topic": command,
        "min": float(minimum),
        "max": float(maximum),
        "step": float(step),
        "mode": "box",
        # Regelparameter gehoeren nicht auf die Zonenkarte, sondern hinter "Konfiguration".
        "entity_category": "config",
    }
    if einheit is not None:
        daten["unit_of_measurement"] = einheit
    return DiscoveryMessage(_config_topic("number", objekt_id), _als_json(daten))


def armed_discovery(praefix: str = "thermoctl") -> DiscoveryMessage:
    """Ob die Regelung wirklich schaltet, als eigene Entitaet fuer den ganzen Dienst."""
    objekt_id = f"{_identifier(praefix)}_scharf"
    daten: dict[str, Any] = {
        "device": {
            "identifiers": [f"thermoctl:{_praefix(praefix)}"],
            "name": "thermoctl",
            "manufacturer": "thermoctl",
        },
        "availability_topic": availability_topic(praefix),
        "payload_available": "online",
        "payload_not_available": "offline",
        "name": "Regelung scharf",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": armed_topic(praefix),
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "running",
    }
    return DiscoveryMessage(_config_topic("binary_sensor", objekt_id), _als_json(daten))


def alle_topics(zone_id: int, praefix: str = "thermoctl") -> tuple[str, ...]:
    """Liefert alle zonenbezogenen Topics fuer Vertragspruefungen."""
    state = asdict(states_topics(zone_id, praefix)).values()
    command = asdict(command_topics(zone_id, praefix)).values()
    return (*state, *command)
