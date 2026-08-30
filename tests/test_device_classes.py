import json
from pathlib import Path

import pytest

from thermoctl.domain.device_classes import (
    DeviceDescription,
    capabilities_from_exposes,
    descriptions_from_bridge_list,
)


def _installation_name(part: str) -> str:
    path = Path(__file__).parent / "daten" / "anlage-beispiele.json"
    names = json.loads(path.read_text(encoding="utf-8"))["geraete"]
    return next(name for name in names if part in name)


def test_a_valve_is_recognized_from_exposes_alone() -> None:
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "current_heating_setpoint"},
                {"type": "numeric", "property": "local_temperature"},
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset({"setpoint", "temperature"})


def test_a_window_contact_is_recognized() -> None:
    exposes = [{"type": "binary", "property": "contact"}]

    assert capabilities_from_exposes(exposes) == frozenset({"contact"})


def test_a_multisensor_carries_its_real_installation_name_and_metadata() -> None:
    name = _installation_name("Über Küche")
    payload = json.dumps(
        [
            {
                "friendly_name": name,
                "ieee_address": "aus-der-bridge-liste",
                "type": "EndDevice",
                "definition": {
                    "model": "Modell aus der Bridge",
                    "vendor": "Hersteller aus der Bridge",
                    "exposes": [
                        {"type": "numeric", "property": "temperature"},
                        {"type": "numeric", "property": "humidity"},
                        {"type": "numeric", "property": "battery"},
                        {"type": "numeric", "property": "linkquality"},
                    ],
                },
            }
        ]
    )

    assert descriptions_from_bridge_list(payload) == [
        DeviceDescription(
            name=name,
            address="aus-der-bridge-liste",
            model="Modell aus der Bridge",
            manufacturer="Hersteller aus der Bridge",
            ist_group=False,
            capabilities=frozenset(
                {"temperature", "humidity", "battery", "link_quality"}
            ),
        )
    ]


def test_coordinator_and_bridge_are_filtered_out() -> None:
    payload = json.dumps(
        [
            {"friendly_name": "anderer Name", "type": "Coordinator"},
            {"friendly_name": "Coordinator", "type": "Router"},
            {"friendly_name": "bridge", "type": "Router"},
        ]
    )

    assert descriptions_from_bridge_list(payload) == []


def test_a_group_remains_visible_and_is_marked() -> None:
    name = _installation_name("wohnraum")

    [group] = descriptions_from_bridge_list(
        json.dumps([{"friendly_name": name, "definition": {"exposes": []}}])
    )

    assert group.name == name
    assert group.ist_group is True
    assert group.address is None


def test_an_unrecognized_device_has_an_empty_description() -> None:
    name = _installation_name("Thermostat")

    [device] = descriptions_from_bridge_list(
        json.dumps([{"friendly_name": name, "ieee_address": "aus-der-bridge-liste"}])
    )

    assert device.model is None
    assert device.manufacturer is None
    assert device.capabilities == frozenset()


def test_an_empty_device_list_stays_empty() -> None:
    assert descriptions_from_bridge_list("[]") == []


def test_broken_json_is_reported_understandably() -> None:
    with pytest.raises(ValueError, match="kein gueltiges JSON"):
        descriptions_from_bridge_list("[")


def test_a_json_object_instead_of_a_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="JSON-Liste"):
        descriptions_from_bridge_list("{}")


def test_incomplete_foreign_elements_are_tolerated() -> None:
    payload = json.dumps(
        [
            None,
            {"type": "Router"},
            {
                "friendly_name": "aus-der-bridge-liste",
                "ieee_address": "aus-der-bridge-liste",
                "definition": "unbekannt",
            },
        ]
    )

    [device] = descriptions_from_bridge_list(payload)

    assert device.capabilities == frozenset()


def test_features_are_traversed_arbitrarily_deep() -> None:
    exposes = [
        {
            "type": "switch",
            "features": [
                {
                    "type": "composite",
                    "features": [
                        {"type": "binary", "property": "state"},
                        {"type": "numeric", "property": "power"},
                    ],
                }
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset({"switch", "power"})


def test_unknown_and_unstructured_exposes_are_skipped() -> None:
    exposes: list[dict[str, object]] = [
        {"type": "numeric", "property": "voltage"},
        {"type": "composite", "features": [None, "fremd"]},
    ]

    assert capabilities_from_exposes(exposes) == frozenset()
