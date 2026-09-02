"""Pure MQTT topics and Home Assistant discovery payloads.

The zone's stable database id lives in the topic; the changeable display name, which
may contain spaces or umlauts, stays in the payload. State and command sit in separate
subtrees. This way a subscription to ``zustand/#`` never catches a command, and state
topics need no ambiguous ``/get`` suffix.

These functions provide topics and discovery payloads to the production MQTT service.
State and discovery messages are sent in dry run as well. Setpoint commands for
self-regulating valves pass through the separate persisted and startup-built latches;
on/off decisions have no wired actuator.
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
    would_heat: str
    last_switch: str
    next_switch: str


@dataclass(frozen=True)
class CommandTopics:
    """The commands of a zone, kept separate from state subscriptions."""

    setpoint: str
    operating_mode: str
    boost: str


@dataclass(frozen=True)
class FaultNoticeTopics:
    """State and attributes of the zone's persistent problem entity."""

    state: str
    attributes: str


@dataclass(frozen=True)
class DiscoveryMessage:
    """Topic and payload of a discovery message to be sent later."""

    topic: str
    payload: str


def _prefix_of(prefix: str) -> str:
    cleaned = prefix.strip("/")
    if not cleaned or any(character in cleaned for character in ("+", "#", "\0")):
        raise ValueError("Das MQTT-Praefix muss gueltig sein und darf keine Wildcards enthalten")
    return cleaned


def _zone_base(zone_id: int, prefix: str) -> str:
    if zone_id < 1:
        raise ValueError("Die Zonenkennung muss groesser als null sein")
    return f"{_prefix_of(prefix)}/zones/{zone_id}"


def states_topics(zone_id: int, prefix: str = "thermoctl") -> StateTopics:
    """Builds all state topics of a zone."""
    base = f"{_zone_base(zone_id, prefix)}/state"
    return StateTopics(
        current_temperature=f"{base}/current_temperature",
        setpoint=f"{base}/setpoint",
        operating_mode=f"{base}/operating_mode",
        sensor_state=f"{base}/sensor_state",
        would_heat=f"{base}/would_heat",
        last_switch=f"{base}/last_switch",
        next_switch=f"{base}/next_switch",
    )


def command_topics(zone_id: int, prefix: str = "thermoctl") -> CommandTopics:
    """Builds the separate command topics of a zone."""
    base = f"{_zone_base(zone_id, prefix)}/command"
    return CommandTopics(
        setpoint=f"{base}/setpoint",
        operating_mode=f"{base}/operating_mode",
        boost=f"{base}/boost",
    )


def fault_notice_topics(
    zone_id: int, prefix: str = "thermoctl"
) -> FaultNoticeTopics:
    """Builds the topics of a zone's sensor-fault notice."""
    base = f"{_zone_base(zone_id, prefix)}/state/sensor_fault"
    return FaultNoticeTopics(state=base, attributes=f"{base}/attributes")


def mode_topics(zone_id: int, mode_id: int, prefix: str = "thermoctl") -> tuple[str, str]:
    """State and command for the setpoint of **one** mode of this zone."""
    if mode_id < 1:
        raise ValueError("Die Moduskennung muss groesser als null sein")
    base = _zone_base(zone_id, prefix)
    return (f"{base}/state/mode/{mode_id}", f"{base}/command/mode/{mode_id}")


def parameter_topics(zone_id: int, name: str, prefix: str = "thermoctl") -> tuple[str, str]:
    """State and command for **one** control parameter of this zone."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"Kein gueltiger Parametername: {name!r}")
    base = _zone_base(zone_id, prefix)
    return (f"{base}/state/parameter/{name}", f"{base}/command/parameter/{name}")


def armed_topic(prefix: str = "thermoctl") -> str:
    """The persisted first control latch -- not a statement about actuator output.

    Up until this point the dry run lived in the *name* of every zone. That was clearly
    visible and wrong for exactly that reason: Home Assistant derives the entity id
    from the name the first time it appears, and a zone that first showed up during the
    dry run was then called `climate.thermoctl_zone_1_trockenlauf` forever. The
    operating state belongs in its own entity, not in the name of a different one.
    """
    return f"{_prefix_of(prefix)}/state/armed"


def availability_topic(prefix: str = "thermoctl") -> str:
    """Builds the service's shared last-will topic."""
    return f"{_prefix_of(prefix)}/availability"


def _identifier(prefix: str) -> str:
    """The prefix, reduced to what is allowed to appear in a discovery id."""
    without_accents = unicodedata.normalize("NFKD", _prefix_of(prefix)).encode("ascii", "ignore")
    identifier = re.sub(rb"[^a-zA-Z0-9_-]+", b"_", without_accents).decode().strip("_").lower()
    if not identifier:
        raise ValueError("Das MQTT-Praefix ergibt keine gueltige Discovery-Kennung")
    return identifier


def _object_id(zone_id: int, prefix: str) -> str:
    _zone_base(zone_id, prefix)
    return f"{_identifier(prefix)}_zone_{zone_id}"


def discovery_config_topic(zone_id: int, prefix: str = "thermoctl") -> str:
    """Builds the Home Assistant config topic of a climate zone."""
    _zone_base(zone_id, prefix)
    return f"homeassistant/climate/{_object_id(zone_id, prefix)}/config"


def _config_topic(component: str, object_id: str) -> str:
    return f"homeassistant/{component}/{object_id}/config"


def _devices_block(zone_id: int, zone_name: str, prefix: str) -> dict[str, Any]:
    """One Home Assistant device per zone, with the service as its parent.

    Previously, all entities hung off a single device "thermoctl". With a handful of
    zones with a dozen controls each, that's an unsorted list; grouped by zone, what
    belongs together sits together. The `unique_id` of the entities does not change as
    a result -- Home Assistant only re-parents an existing entity, the entity id stays
    the same.
    """
    return {
        "identifiers": [f"thermoctl:{_prefix_of(prefix)}:zone:{zone_id}"],
        "name": zone_name,
        "manufacturer": "thermoctl",
        "via_device": f"thermoctl:{_prefix_of(prefix)}",
    }


def _skeleton(zone_id: int, zone_name: str, prefix: str) -> dict[str, Any]:
    """What every entity of a zone carries alike."""
    return {
        "device": _devices_block(zone_id, zone_name, prefix),
        "availability_topic": availability_topic(prefix),
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def _as_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def discovery_payload(
    zone_id: int,
    zone_name: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    prefix: str = "thermoctl",
) -> str:
    """Builds the JSON payload for a Home Assistant climate zone."""
    if not zone_name.strip():
        raise ValueError("Der Zonenname darf nicht leer sein")
    if temp_step <= 0:
        raise ValueError("Der Temperaturschritt muss groesser als null sein")

    state = states_topics(zone_id, prefix)
    command = command_topics(zone_id, prefix)
    object_id = _object_id(zone_id, prefix)
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": None,
        "unique_id": object_id,
        # Explicitly set so the entity id is not derived from the name. Otherwise it
        # would depend on the zone name's spelling from back then -- and would change
        # with every renaming of the zone.
        "object_id": object_id,
        "current_temperature_topic": state.current_temperature,
        "temperature_state_topic": state.setpoint,
        "temperature_command_topic": command.setpoint,
        "mode_state_topic": state.operating_mode,
        "mode_command_topic": command.operating_mode,
        "mode_state_template": "{{ 'heat' if value == 'manual' else value }}",
        "mode_command_template": "{{ 'manual' if value == 'heat' else value }}",
        "action_topic": state.would_heat,
        "action_template": "{{ 'heating' if value == 'true' else 'idle' }}",
        "modes": ["auto", "heat", "off"],
        # From the domain, not copied by hand: this way Home Assistant shows the
        # same range the service also accepts.
        "min_temp": float(MINIMUM_TEMPERATURE_C),
        "max_temp": float(MAXIMUM_TEMPERATURE_C),
        "temp_step": float(temp_step),
        "temperature_unit": "C",
    }
    return _as_json(data)


def zone_discovery(
    zone_id: int,
    zone_name: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    prefix: str = "thermoctl",
) -> DiscoveryMessage:
    """Bundles config topic and discovery payload for a zone."""
    return DiscoveryMessage(
        discovery_config_topic(zone_id, prefix),
        discovery_payload(zone_id, zone_name, temp_step=temp_step, prefix=prefix),
    )


def discovery_removal(zone_id: int, prefix: str = "thermoctl") -> DiscoveryMessage:
    """Builds the empty discovery message for removing a zone."""
    return DiscoveryMessage(discovery_config_topic(zone_id, prefix), "")


def boost_discovery(
    zone_id: int, zone_name: str, prefix: str = "thermoctl"
) -> DiscoveryMessage:
    """The button that pulls the next switch forward."""
    object_id = f"{_object_id(zone_id, prefix)}_boost"
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": "Boost",
        "unique_id": object_id,
        "object_id": object_id,
        "command_topic": command_topics(zone_id, prefix).boost,
        "payload_press": "boost",
        "icon": "mdi:fast-forward",
    }
    return DiscoveryMessage(_config_topic("button", object_id), _as_json(data))


def timestamp_discovery(
    zone_id: int,
    zone_name: str,
    kind: str,
    label: str,
    prefix: str = "thermoctl",
) -> DiscoveryMessage:
    """A point in time as a sensor -- 'last switch' and 'next mode change'.

    `device_class: timestamp` means: Home Assistant expects ISO 8601 with timezone and
    displays "12 minutes ago" itself. That's why no preformatted text goes here -- the
    display language belongs wherever it is read.
    """
    if kind not in ("last_switch", "next_switch"):
        raise ValueError(f"Unbekannte Zeitstempelart: {kind!r}")
    object_id = f"{_object_id(zone_id, prefix)}_{kind}"
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": label,
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": getattr(states_topics(zone_id, prefix), kind),
        "device_class": "timestamp",
    }
    return DiscoveryMessage(_config_topic("sensor", object_id), _as_json(data))


def fault_notice_discovery(
    zone_id: int, zone_name: str, prefix: str = "thermoctl"
) -> DiscoveryMessage:
    """Registers one persistent problem entity per zone.

    A binary sensor is deliberate: an automation can trigger on both edges and the
    current fault remains visible after a Home Assistant restart. An MQTT event would
    be transient, while a text sensor would make automations compare presentation text.
    The attributes carry the human-readable notice without weakening the stable ON/OFF
    contract used by an automation that sends a phone notification.
    """
    topics = fault_notice_topics(zone_id, prefix)
    object_id = f"{_object_id(zone_id, prefix)}_sensorstoerung"
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": "Sensorstörung",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": topics.state,
        "json_attributes_topic": topics.attributes,
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "problem",
    }
    return DiscoveryMessage(_config_topic("binary_sensor", object_id), _as_json(data))


def mode_discovery(
    zone_id: int,
    zone_name: str,
    mode_id: int,
    mode_name: str,
    prefix: str = "thermoctl",
    temp_step: Decimal = Decimal("0.5"),
) -> DiscoveryMessage:
    """The setpoint of a mode as a number input.

    The thermostat only ever shows the mode currently in effect. Whoever wants to
    adjust the night setback in the afternoon needs a dedicated input for that --
    otherwise they would have to wait until evening.
    """
    object_id = f"{_object_id(zone_id, prefix)}_modus_{mode_id}"
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": f"Sollwert {mode_name}",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": mode_topics(zone_id, mode_id, prefix)[0],
        "command_topic": mode_topics(zone_id, mode_id, prefix)[1],
        "min": float(MINIMUM_TEMPERATURE_C),
        "max": float(MAXIMUM_TEMPERATURE_C),
        "step": float(temp_step),
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "mode": "box",
    }
    return DiscoveryMessage(_config_topic("number", object_id), _as_json(data))


def parameter_discovery(
    zone_id: int,
    zone_name: str,
    name: str,
    label: str,
    minimum: Decimal,
    maximum: Decimal,
    step: Decimal,
    unit: str | None = None,
    prefix: str = "thermoctl",
) -> DiscoveryMessage:
    """A control parameter of the zone as a number input."""
    state, command = parameter_topics(zone_id, name, prefix)
    object_id = f"{_object_id(zone_id, prefix)}_parameter_{name}"
    data: dict[str, Any] = {
        **_skeleton(zone_id, zone_name, prefix),
        "name": label,
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": state,
        "command_topic": command,
        "min": float(minimum),
        "max": float(maximum),
        "step": float(step),
        "mode": "box",
        # Control parameters don't belong on the zone card, but behind "Configuration".
        "entity_category": "config",
    }
    if unit is not None:
        data["unit_of_measurement"] = unit
    return DiscoveryMessage(_config_topic("number", object_id), _as_json(data))


def armed_discovery(prefix: str = "thermoctl") -> DiscoveryMessage:
    """The persisted first control latch, as one entity for the whole service."""
    object_id = f"{_identifier(prefix)}_scharf"
    data: dict[str, Any] = {
        "device": {
            "identifiers": [f"thermoctl:{_prefix_of(prefix)}"],
            "name": "thermoctl",
            "manufacturer": "thermoctl",
        },
        "availability_topic": availability_topic(prefix),
        "payload_available": "online",
        "payload_not_available": "offline",
        "name": "Regelung scharf",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": armed_topic(prefix),
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "running",
    }
    return DiscoveryMessage(_config_topic("binary_sensor", object_id), _as_json(data))


def alle_topics(zone_id: int, prefix: str = "thermoctl") -> tuple[str, ...]:
    """Returns all zone-related topics for contract checks."""
    state = asdict(states_topics(zone_id, prefix)).values()
    command = asdict(command_topics(zone_id, prefix)).values()
    notices = asdict(fault_notice_topics(zone_id, prefix)).values()
    return (*state, *command, *notices)
