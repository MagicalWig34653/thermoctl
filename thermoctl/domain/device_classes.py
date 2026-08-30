import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class DeviceDescription:
    name: str
    adresse: str | None
    modell: str | None
    manufacturer: str | None
    ist_group: bool
    capabilities: frozenset[str]


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
    for roh in entries:
        if not isinstance(roh, Mapping):
            continue
        entry = cast(Mapping[str, object], roh)
        typ = _text(entry.get("type"))
        merkmal = _text(entry.get("property")) or typ
        capability = _CAPABILITY_BY_FEATURE.get(merkmal or "")
        if capability is not None:
            result.add(capability)
        if inside_switch and typ == "binary" and merkmal == "state":
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
    """Leitet nur ausdruecklich vereinbarte Faehigkeiten aus Zigbee2MQTT ab."""
    return frozenset(_collect_capabilities(cast(list[object], exposes)))


def descriptions_from_bridge_list(
    payload: str | bytes,
) -> list[DeviceDescription]:
    """Liest die Geraetebeschreibungen aus der Zigbee2MQTT-Brueckenliste."""
    try:
        roh = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Geraeteliste ist kein gueltiges JSON") from exc
    if not isinstance(roh, list):
        raise ValueError("Geraeteliste muss eine JSON-Liste sein")

    beschreibungen: list[DeviceDescription] = []
    for element in roh:
        if not isinstance(element, Mapping):
            continue
        entry = cast(Mapping[str, object], element)
        name = _text(entry.get("friendly_name"))
        if name is None:
            continue
        if entry.get("type") == "Coordinator" or name in {"Coordinator", "bridge"}:
            continue

        adresse = _text(entry.get("ieee_address"))
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
        beschreibungen.append(
            DeviceDescription(
                name=name,
                adresse=adresse,
                modell=_text(definition.get("model")),
                manufacturer=_text(definition.get("vendor")),
                ist_group=adresse is None,
                capabilities=capabilities_from_exposes(exposes),
            )
        )
    return beschreibungen
