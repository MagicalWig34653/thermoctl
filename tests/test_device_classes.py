import json
from pathlib import Path

import pytest

from thermoctl.domain.device_classes import (
    DeviceDescription,
    capabilities_from_exposes,
    descriptions_from_bridge_list,
    properties_from_exposes,
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


def test_a_thermostatic_radiator_valve_is_recognized_as_thermostat() -> None:
    """A WT-A03E-style TRV has no `state` to switch -- it is a writable `system_mode`
    plus a writable `occupied_heating_setpoint` in the same expose that mark it as an
    actual thermostat.

    `access` is Zigbee2MQTT's bitmask: 1 published, 2 settable, 4 gettable. The values
    below are what a real TRV reports -- 3 for the two it accepts commands on, 1 for
    the ones it only publishes.
    """
    exposes = [
        {
            "type": "climate",
            "features": [
                {
                    "type": "numeric",
                    "property": "occupied_heating_setpoint",
                    "access": 3,
                    "value_min": 5,
                    "value_max": 30,
                },
                {"type": "numeric", "property": "local_temperature", "access": 1},
                {
                    "type": "enum",
                    "property": "system_mode",
                    "access": 3,
                    "values": ["off", "heat", "auto"],
                },
                {
                    "type": "enum",
                    "property": "running_state",
                    "access": 1,
                    "values": ["idle", "heat"],
                },
                {"type": "numeric", "property": "position", "access": 1},
                {"type": "binary", "property": "window_open", "access": 1},
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset(
        {
            "thermostat",
            "setpoint",
            "temperature",
            "running_state",
            "valve_position",
            "window_open",
        }
    )


def test_a_display_that_only_reports_a_setpoint_is_no_thermostat() -> None:
    """The counter-check that matters, because getting it wrong is silent.

    A device that merely *publishes* both features -- a wall display mirroring someone
    else's thermostat -- would be admitted to the actuator slot, and the service would
    send it commands it cannot obey. The plant diagram would show a complete path and
    nothing would ever switch: a bug that surfaces in winter and looks like one in the
    control logic.
    """
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "occupied_heating_setpoint", "access": 1},
                {"type": "enum", "property": "system_mode", "access": 1},
            ],
        }
    ]
    assert "thermostat" not in capabilities_from_exposes(exposes)


def test_a_property_without_an_access_field_does_not_count_as_writable() -> None:
    """An omission is not permission -- Zigbee2MQTT simply may not say."""
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "occupied_heating_setpoint"},
                {"type": "enum", "property": "system_mode"},
            ],
        }
    ]
    assert "thermostat" not in capabilities_from_exposes(exposes)


def test_the_two_features_must_sit_in_the_same_expose() -> None:
    """Two unrelated branches must not add up to a thermostat that exists nowhere.

    A multi-endpoint device can perfectly well carry a settable setpoint on one
    endpoint and a `system_mode` belonging to something else on another. Collecting
    names across the whole tree would fuse them into one device that is not there.
    """
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "occupied_heating_setpoint", "access": 3},
            ],
        },
        {
            "type": "composite",
            "features": [{"type": "enum", "property": "system_mode", "access": 3}],
        },
    ]
    assert "thermostat" not in capabilities_from_exposes(exposes)


def test_a_thermostat_nested_in_another_expose_is_still_found() -> None:
    """Multi-endpoint devices nest `climate` one level deeper; that is still a valve."""
    exposes = [
        {
            "type": "composite",
            "features": [
                {
                    "type": "climate",
                    "features": [
                        {
                            "type": "numeric",
                            "property": "occupied_heating_setpoint",
                            "access": 3,
                        },
                        {"type": "enum", "property": "system_mode", "access": 3},
                    ],
                }
            ],
        }
    ]
    assert "thermostat" in capabilities_from_exposes(exposes)


def test_occupied_heating_setpoint_alone_is_not_enough_to_be_a_thermostat() -> None:
    """A plain setpoint display without `system_mode` is not a thermostat -- it
    cannot be armed one way or the other, only shown."""
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "occupied_heating_setpoint"},
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset({"setpoint"})


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


def test_an_unreadable_range_is_treated_as_no_range() -> None:
    """Zigbee2MQTT does not promise that `value_min` is a number.

    A device whose range cannot be read still has a usable property -- refusing the
    whole device over one unparsable field would lose the parts that are fine. So the
    range is dropped, not the property.
    """
    properties = properties_from_exposes(
        [
            {
                "type": "numeric",
                "property": "temperature",
                "access": 1,
                "value_min": "kalt",
                "value_max": None,
            }
        ]
    )
    assert len(properties) == 1
    assert properties[0].min_value is None
    assert properties[0].max_value is None


def test_entries_that_are_not_objects_are_skipped() -> None:
    """The list comes from another program; nothing guarantees its shape.

    A string where an expose belongs must not stop the walk -- the properties beside
    it are still readable, and dropping them would silently shrink a device.
    """
    properties = properties_from_exposes(
        [
            "kein Objekt",  # type: ignore[list-item]
            {
                "type": "climate",
                "features": [
                    42,  # type: ignore[list-item]
                    {"type": "numeric", "property": "local_temperature", "access": 1},
                ],
            },
        ]
    )
    assert [p.name for p in properties] == ["local_temperature"]
