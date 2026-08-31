# ruff: noqa: E501
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast


@dataclass(frozen=True)
class DeviceDescription:
    name: str
    address: str | None
    model: str | None
    manufacturer: str | None
    ist_group: bool
    capabilities: frozenset[str]
    properties: tuple[PropertyDescription, ...] = ()


@dataclass(frozen=True)
class PropertyDescription:
    """A single device property as described by Zigbee2MQTT."""

    name: str
    value_type: str
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    is_readable: bool
    is_writable: bool
    values: tuple[str, ...]


_CAPABILITY_BY_FEATURE = {
    "temperature": "temperature",
    "local_temperature": "temperature",
    "humidity": "humidity",
    "battery": "battery",
    "linkquality": "link_quality",
    "illuminance": "illuminance",
    "illuminance_lux": "illuminance",
    "occupancy": "occupancy",
    "contact": "contact",
    "current_heating_setpoint": "setpoint",
    "occupied_heating_setpoint": "setpoint",
    "position": "valve_position",
    "valve_position": "valve_position",
    "pi_heating_demand": "valve_position",
    "power": "power",
    "energy": "energy",
    "running_state": "running_state",
    "window_open": "window_open",
}

# A thermostatic radiator valve (e.g. WT-A03E) exposes no `state` field to switch --
# it is driven through these two features together. Neither one alone implies a
# valve: `occupied_heating_setpoint` alone is also exposed by a plain wall
# thermostat display, and `system_mode` alone would be unusual but not impossible.
# Only the combination is treated as proof of an actual thermostat.
_THERMOSTAT_FEATURES = frozenset({"occupied_heating_setpoint", "system_mode"})


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _collect_capabilities(
    entries: list[object], *, inside_switch: bool = False
) -> set[str]:
    result: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            continue
        entry = cast(Mapping[str, object], raw_entry)
        feature_type = _text(entry.get("type"))
        property = _text(entry.get("property")) or feature_type
        capability = _CAPABILITY_BY_FEATURE.get(property or "")
        if capability is not None:
            result.add(capability)
        if inside_switch and feature_type == "binary" and property == "state":
            result.add("switch")

        features = entry.get("features")
        if isinstance(features, list):
            result.update(
                _collect_capabilities(
                    cast(list[object], features), inside_switch=inside_switch or feature_type == "switch"
                )
            )
    return result


def _writable_property_names(entries: list[object]) -> set[str]:
    """The names of the properties in one expose branch that can actually be **set**.

    Zigbee2MQTT states this in `access`, a bitmask: bit 1 means the value is published,
    bit 2 that it can be written via `/set`. Only the second one matters here. A device
    that merely *reports* `occupied_heating_setpoint` and `system_mode` -- a wall display
    mirroring someone else's thermostat, for instance -- would otherwise be classified
    as an actuator, and the service would send it commands it cannot obey. What that
    looks like is a heating that never comes on, in winter, seeming like a bug in the
    control logic (principle 7).

    A property without `access` counts as not writable: an omission is not permission.
    """
    names: set[str] = set()
    for raw_item in entries:
        if not isinstance(raw_item, Mapping):
            continue
        entry = cast(Mapping[str, object], raw_item)
        property_name = _text(entry.get("property"))
        access = entry.get("access")
        if property_name is not None and isinstance(access, int) and access & 2:
            names.add(property_name)
    return names


def _is_thermostat(entries: list[object]) -> bool:
    """Whether some expose describes a settable heating thermostat.

    Both features have to sit in the **same** expose and both have to be writable.
    Collecting names across the whole tree would let two unrelated branches -- an
    unwritable setpoint here, some `system_mode` there -- add up to a thermostat that
    exists nowhere on the device.
    """
    for raw_item in entries:
        if not isinstance(raw_item, Mapping):
            continue
        entry = cast(Mapping[str, object], raw_item)
        features = entry.get("features")
        if not isinstance(features, list):
            continue
        branch = cast(list[object], features)
        if _THERMOSTAT_FEATURES <= _writable_property_names(branch):
            return True
        # Nested composites: Zigbee2MQTT nests `climate` inside another expose for
        # multi-endpoint devices.
        if _is_thermostat(branch):
            return True
    return False


def capabilities_from_exposes(
    exposes: list[dict[str, object]],
) -> frozenset[str]:
    """Derives only explicitly agreed-upon capabilities from Zigbee2MQTT."""
    entries = cast(list[object], exposes)
    result = _collect_capabilities(entries)
    if _is_thermostat(entries):
        result.add("thermostat")
    return frozenset(result)


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def properties_from_exposes(exposes: list[dict[str, object]]) -> tuple[PropertyDescription, ...]:
    """Reads properties recursively; containers with no property of their own are skipped."""
    result: list[PropertyDescription] = []

    def visit(entries: list[object]) -> None:
        for raw in entries:
            if not isinstance(raw, Mapping):
                continue
            entry = cast(Mapping[str, object], raw)
            name = _text(entry.get("property"))
            kind = _text(entry.get("type"))
            access = entry.get("access")
            if (
                name is not None
                and kind in {"numeric", "binary", "enum", "text"}
                and isinstance(access, int)
            ):
                raw_values = entry.get("values")
                values = tuple(str(value) for value in raw_values) if isinstance(raw_values, list) else ()
                bits = access
                result.append(
                    PropertyDescription(
                        name=name,
                        value_type=kind,
                        unit=_text(entry.get("unit")),
                        min_value=_decimal(entry.get("value_min")),
                        max_value=_decimal(entry.get("value_max")),
                        is_readable=bool(bits & 1),
                        is_writable=bool(bits & 2),
                        values=values,
                    )
                )
            children = entry.get("features")
            if isinstance(children, list):
                visit(cast(list[object], children))

    visit(cast(list[object], exposes))
    return tuple(result)


def descriptions_from_bridge_list(
    payload: str | bytes,
) -> list[DeviceDescription]:
    """Reads the device descriptions from the Zigbee2MQTT bridge list."""
    try:
        raw_entry = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Geraeteliste ist kein gueltiges JSON") from exc
    if not isinstance(raw_entry, list):
        raise ValueError("Geraeteliste muss eine JSON-Liste sein")

    descriptions: list[DeviceDescription] = []
    for element in raw_entry:
        if not isinstance(element, Mapping):
            continue
        entry = cast(Mapping[str, object], element)
        name = _text(entry.get("friendly_name"))
        if name is None:
            continue
        if entry.get("type") == "Coordinator" or name in {"Coordinator", "bridge"}:
            continue

        address = _text(entry.get("ieee_address"))
        definition_raw = entry.get("definition")
        definition = (
            cast(Mapping[str, object], definition_raw)
            if isinstance(definition_raw, Mapping)
            else {}
        )
        exposes_raw = definition.get("exposes")
        exposes = (
            cast(list[dict[str, object]], exposes_raw)
            if isinstance(exposes_raw, list)
            else []
        )
        descriptions.append(
            DeviceDescription(
                name=name,
                address=address,
                model=_text(definition.get("model")),
                manufacturer=_text(definition.get("vendor")),
                ist_group=address is None,
                capabilities=capabilities_from_exposes(exposes),
                properties=properties_from_exposes(exposes),
            )
        )
    return descriptions
