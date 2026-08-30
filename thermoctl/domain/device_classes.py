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
}


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
        typ = _text(entry.get("type"))
        property = _text(entry.get("property")) or typ
        capability = _CAPABILITY_BY_FEATURE.get(property or "")
        if capability is not None:
            result.add(capability)
        if inside_switch and typ == "binary" and property == "state":
            result.add("switch")

        features = entry.get("features")
        if isinstance(features, list):
            result.update(
                _collect_capabilities(
                    cast(list[object], features), inside_switch=inside_switch or typ == "switch"
                )
            )
    return result


def capabilities_from_exposes(
    exposes: list[dict[str, object]],
) -> frozenset[str]:
    """Derives only explicitly agreed-upon capabilities from Zigbee2MQTT."""
    return frozenset(_collect_capabilities(cast(list[object], exposes)))


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
        definition_roh = entry.get("definition")
        definition = (
            cast(Mapping[str, object], definition_roh)
            if isinstance(definition_roh, Mapping)
            else {}
        )
        exposes_roh = definition.get("exposes")
        exposes = (
            cast(list[dict[str, object]], exposes_roh)
            if isinstance(exposes_roh, list)
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
