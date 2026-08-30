import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.reading import Reading, readings_from_payload

DATA_PATH = Path(__file__).parent / "daten" / "anlage-beispiele.json"
RECEIVED_AT = datetime(2035, 1, 2, 3, 4, 5)


def _values(readings: list[Reading]) -> list[tuple[str, Decimal | None, str | None]]:
    return [(b.capability, b.number, b.text) for b in readings]


@pytest.mark.parametrize(
    ("device", "expected", "measured_at"),
    [
        (
            "Wohnraum Couchlicht",
            [("link_quality", Decimal("102"), None), ("switch", None, "OFF")],
            datetime(2026, 8, 29, 6, 43, 58, 479000),
        ),
        (
            "Küche Esstisch",
            [("link_quality", Decimal("96"), None), ("switch", None, "OFF")],
            datetime(2026, 8, 29, 6, 44, 29, 61000),
        ),
        (
            "Bewegungsmelder außen",
            [
                ("battery", Decimal("100"), None),
                ("illuminance", Decimal("5241"), None),
                ("link_quality", Decimal("99"), None),
                ("occupancy", None, "false"),
                ("temperature", Decimal("18.85"), None),
            ],
            datetime(2026, 8, 29, 6, 43, 50, 547000),
        ),
        (
            "Abstellraum Multisensor",
            [
                ("battery", Decimal("100"), None),
                ("humidity", Decimal("49.7"), None),
                ("link_quality", Decimal("129"), None),
                ("temperature", Decimal("24.3"), None),
            ],
            datetime(2026, 8, 29, 6, 45, 13, 963000),
        ),
        (
            "Zimmer 1 Fernbedienung",
            [
                ("battery", Decimal("10.5"), None),
                ("link_quality", Decimal("144"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 59, 394000),
        ),
        (
            "Bad Steckdose Entfeuchter",
            [
                ("energy", Decimal("28.52"), None),
                ("link_quality", Decimal("45"), None),
                ("power", Decimal("0"), None),
                ("switch", None, "OFF"),
            ],
            datetime(2026, 8, 29, 6, 44, 55, 241000),
        ),
        (
            "Schlafzimmer Multisensor",
            [
                ("battery", Decimal("95.5"), None),
                ("humidity", Decimal("80"), None),
                ("link_quality", Decimal("144"), None),
                ("temperature", Decimal("24.12"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 40, 822000),
        ),
        (
            "Zimmer 3 Multisensor",
            [
                ("battery", Decimal("87"), None),
                ("humidity", Decimal("53.63"), None),
                ("link_quality", Decimal("90"), None),
                ("temperature", Decimal("25.83"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 45, 1000),
        ),
        (
            "Zimmer 3 Pflanzensensor",
            [
                ("battery", Decimal("100"), None),
                ("humidity", Decimal("59"), None),
                ("link_quality", Decimal("132"), None),
                ("soil_moisture", Decimal("24"), None),
                ("temperature", Decimal("26.5"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 57, 891000),
        ),
        (
            "Bewegungsmelder innen",
            [
                ("battery", Decimal("60.5"), None),
                ("illuminance", Decimal("651"), None),
                ("link_quality", Decimal("123"), None),
                ("occupancy", None, "false"),
                ("temperature", Decimal("24.58"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 59, 186000),
        ),
    ],
)
def test_real_state_messages(
    device: str,
    expected: list[tuple[str, Decimal | None, str | None]],
    measured_at: datetime,
) -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    result = readings_from_payload(
        json.dumps(data["zustaende"][device]), RECEIVED_AT
    )

    assert _values(result) == expected
    assert {b.gemessen_am for b in result} == {measured_at}


def test_voltage_is_read_neither_as_battery_level_nor_as_a_measurement() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))["zustaende"]

    outlet = _values(
        readings_from_payload(json.dumps(data["Bad Steckdose Entfeuchter"]), RECEIVED_AT)
    )
    battery_device = _values(
        readings_from_payload(json.dumps(data["Schlafzimmer Multisensor"]), RECEIVED_AT)
    )

    assert ("battery", Decimal("230"), None) not in outlet
    assert ("battery", Decimal("2900"), None) not in battery_device
    assert all(capability != "voltage" for capability, _number, _text in outlet + battery_device)


def test_null_objects_and_an_unknown_field_do_not_disturb_a_known_value() -> None:
    payload = json.dumps(
        {
            "temperature": 21.5,
            "battery": None,
            "update": {"state": "idle"},
            "color": {"x": 0.1},
            "neues_feld": 7,
        }
    )

    assert readings_from_payload(payload, RECEIVED_AT) == [
        Reading("temperature", Decimal("21.5"), None, RECEIVED_AT)
    ]


def test_an_unreadable_last_seen_uses_the_received_time() -> None:
    result = readings_from_payload(
        '{"temperature": 20, "last_seen": "gestern"}', RECEIVED_AT
    )

    assert result[0].gemessen_am == RECEIVED_AT


@pytest.mark.parametrize("payload", ["", "{kaputt", b"\xff"])
def test_broken_json_is_logged(
    payload: str | bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.reading"):
        assert readings_from_payload(payload, RECEIVED_AT) == []

    assert "kein gueltiges JSON" in caplog.text


def test_bytes_payload_is_evaluated() -> None:
    result = readings_from_payload(b'{"state":"ON"}', RECEIVED_AT)

    assert result == [Reading("switch", None, "ON", RECEIVED_AT)]


def test_a_valve_and_a_window_contact_are_recognized_without_example_data() -> None:
    result = readings_from_payload(
        b'{"contact":false,"local_temperature":19.25,"current_heating_setpoint":21}',
        RECEIVED_AT,
    )

    assert _values(result) == [
        ("contact", None, "false"),
        ("temperature", Decimal("19.25"), None),
        ("setpoint", Decimal("21"), None),
    ]


def test_last_seen_without_a_timezone_is_discarded() -> None:
    """Without a timezone, the timestamp cannot be converted to UTC.

    Zigbee2MQTT can be set to local time with no offset. Interpreting such a
    value as UTC would be off by two hours in summer — and fault detection
    depends on the age of the reading. The received time is less precise,
    but not wrong.
    """
    received = datetime(2026, 8, 29, 12, 0, 0)
    readings = readings_from_payload(
        json.dumps({"last_seen": "2026-08-29T06:00:00", "temperature": 21.5}), received
    )
    assert [b.gemessen_am for b in readings] == [received]


def test_valid_json_that_is_not_an_object_yields_nothing() -> None:
    """A list or a bare value is not a state message, but not an error either."""
    received = datetime(2026, 8, 29, 12, 0, 0)
    assert readings_from_payload("[1, 2, 3]", received) == []
    assert readings_from_payload("42", received) == []
