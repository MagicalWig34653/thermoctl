"""Pure MQTT topics and Home Assistant discovery payloads.

The zone's stable database id lives in the topic; the changeable display name, which
may contain spaces or umlauts, stays in the payload. State and command sit in separate
subtrees. This way a subscription to ``zustand/#`` never catches a command, and state
topics need no ambiguous ``/get`` suffix.

These functions are only wired up to a sending adapter in phase 4/5. The contract is
built already now, so topic structure and discovery can be fully verified without
access to the real heating system.
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
    """The individually subscribable state values of a zone."""

    current_temperature: str
    setpoint: str
    operating_mode: str
    sensor_state: str
    wuerde_heizen: str
    last_switch: str
    next_switch: str


@dataclass(frozen=True)
class CommandTopics:
    """The commands of a zone, kept separate from state subscriptions."""

    setpoint: str
    operating_mode: str
    boost: str


@dataclass(frozen=True)
class DiscoveryMessage:
    """Topic and payload of a discovery message to be sent later."""

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
    """Builds all state topics of a zone."""
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
    """Builds the separate command topics of a zone."""
    basis = f"{_zonebasis(zone_id, praefix)}/command"
    return CommandTopics(
        setpoint=f"{basis}/setpoint",
        operating_mode=f"{basis}/operating_mode",
        boost=f"{basis}/boost",
    )


def mode_topics(zone_id: int, mode_id: int, praefix: str = "thermoctl") -> tuple[str, str]:
    """State and command for the setpoint of **one** mode of this zone."""
    if mode_id < 1:
        raise ValueError("Die Moduskennung muss groesser als null sein")
    basis = _zonebasis(zone_id, praefix)
    return (f"{basis}/state/mode/{mode_id}", f"{basis}/command/mode/{mode_id}")


def parameter_topics(zone_id: int, name: str, praefix: str = "thermoctl") -> tuple[str, str]:
    """State and command for **one** control parameter of this zone."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"Kein gueltiger Parametername: {name!r}")
    basis = _zonebasis(zone_id, praefix)
    return (f"{basis}/state/parameter/{name}", f"{basis}/command/parameter/{name}")


def armed_topic(praefix: str = "thermoctl") -> str:
    """Whether control is really switching -- one statement for the whole service.

    Up until this point the dry run lived in the *name* of every zone. That was clearly
    visible and wrong for exactly that reason: Home Assistant derives the entity id
    from the name the first time it appears, and a zone that first showed up during the
    dry run was then called `climate.thermoctl_zone_1_trockenlauf` forever. The
    operating state belongs in its own entity, not in the name of a different one.
    """
    return f"{_praefix(praefix)}/state/armed"


def availability_topic(praefix: str = "thermoctl") -> str:
    """Builds the service's shared last-will topic."""
    return f"{_praefix(praefix)}/availability"


def _identifier(praefix: str) -> str:
    """The prefix, reduced to what is allowed to appear in a discovery id."""
    ohne_akzente = unicodedata.normalize("NFKD", _praefix(praefix)).encode("ascii", "ignore")
    identifier = re.sub(rb"[^a-zA-Z0-9_-]+", b"_", ohne_akzente).decode().strip("_").lower()
    if not identifier:
        raise ValueError("Das MQTT-Praefix ergibt keine gueltige Discovery-Kennung")
    return identifier


def _objekt_id(zone_id: int, praefix: str) -> str:
    _zonebasis(zone_id, praefix)
    return f"{_identifier(praefix)}_zone_{zone_id}"


def discovery_config_topic(zone_id: int, praefix: str = "thermoctl") -> str:
    """Builds the Home Assistant config topic of a climate zone."""
    _zonebasis(zone_id, praefix)
    return f"homeassistant/climate/{_objekt_id(zone_id, praefix)}/config"


def _config_topic(komponente: str, objekt_id: str) -> str:
    return f"homeassistant/{komponente}/{objekt_id}/config"


def _devicesblock(zone_id: int, zone_name: str, praefix: str) -> dict[str, Any]:
    """One Home Assistant device per zone, with the service as its parent.

    Previously, all entities hung off a single device "thermoctl". With a handful of
    zones with a dozen controls each, that's an unsorted list; grouped by zone, what
    belongs together sits together. The `unique_id` of the entities does not change as
    a result -- Home Assistant only re-parents an existing entity, the entity id stays
    the same.
    """
    return {
        "identifiers": [f"thermoctl:{_praefix(praefix)}:zone:{zone_id}"],
        "name": zone_name,
        "manufacturer": "thermoctl",
        "via_device": f"thermoctl:{_praefix(praefix)}",
    }


def _grundgeruest(zone_id: int, zone_name: str, praefix: str) -> dict[str, Any]:
    """What every entity of a zone carries alike."""
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
    """Builds the JSON payload for a Home Assistant climate zone."""
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
        # Explicitly set so the entity id is not derived from the name. Otherwise it
        # would depend on the zone name's spelling from back then -- and would change
        # with every renaming of the zone.
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
        # From the domain, not copied by hand: this way Home Assistant shows the
        # same range the service also accepts.
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
    """Bundles config topic and discovery payload for a zone."""
    return DiscoveryMessage(
        discovery_config_topic(zone_id, praefix),
        discovery_payload(zone_id, zone_name, temp_step=temp_step, praefix=praefix),
    )


def discovery_removal(zone_id: int, praefix: str = "thermoctl") -> DiscoveryMessage:
    """Builds the empty discovery message for removing a zone."""
    return DiscoveryMessage(discovery_config_topic(zone_id, praefix), "")


def boost_discovery(
    zone_id: int, zone_name: str, praefix: str = "thermoctl"
) -> DiscoveryMessage:
    """The button that pulls the next switch forward."""
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
    """A point in time as a sensor -- 'last switch' and 'next mode change'.

    `device_class: timestamp` means: Home Assistant expects ISO 8601 with timezone and
    displays "12 minutes ago" itself. That's why no preformatted text goes here -- the
    display language belongs wherever it is read.
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
    """The setpoint of a mode as a number input.

    The thermostat only ever shows the mode currently in effect. Whoever wants to
    adjust the night setback in the afternoon needs a dedicated input for that --
    otherwise they would have to wait until evening.
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
    """A control parameter of the zone as a number input."""
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
        # Control parameters don't belong on the zone card, but behind "Configuration".
        "entity_category": "config",
    }
    if einheit is not None:
        daten["unit_of_measurement"] = einheit
    return DiscoveryMessage(_config_topic("number", objekt_id), _als_json(daten))


def armed_discovery(praefix: str = "thermoctl") -> DiscoveryMessage:
    """Whether control is really switching, as its own entity for the whole service."""
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
    """Returns all zone-related topics for contract checks."""
    state = asdict(states_topics(zone_id, praefix)).values()
    command = asdict(command_topics(zone_id, praefix)).values()
    return (*state, *command)
