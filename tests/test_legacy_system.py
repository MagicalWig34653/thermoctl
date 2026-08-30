import json
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.legacy_system import LegacyReading, reading_from_topic

DATA_PATH = Path(__file__).parent / "daten" / "anlage-beispiele.json"

# These three topics are configuration values, not thermostat states -- there is
# no thermostat id in the path that they could be attached to.
_CONFIGURATION_TOPICS = frozenset(
    {
        "heizung/config/OFF_TARGET_TEMP/get",
        "heizung/config/POLLING_RATE/get",
        "heizung/config/lastSeen/get",
    }
)


def _heating_topics() -> dict[str, str]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    topics: dict[str, str] = data["heizung_topics"]
    return topics


def test_all_forty_topics_are_present() -> None:
    # Verifies the test's basic assumption: the example file has not changed.
    assert len(_heating_topics()) == 40


@pytest.mark.parametrize("topic", sorted(_heating_topics()))
def test_every_thermostat_topic_is_evaluated(topic: str) -> None:
    payload = _heating_topics()[topic]
    result = reading_from_topic(topic, payload)
    if topic in _CONFIGURATION_TOPICS:
        # Skipped for a reason: no thermostat path segment.
        assert result is None
        return
    assert result is not None
    assert isinstance(result, LegacyReading)
    assert result.thermostat_id in {1, 2, 3, 4, 5, 9}
    if result.attribut in {"temperatureActual", "temperatureTarget"}:
        assert result.number == Decimal(payload)
        assert result.text is None
    else:
        assert result.text == payload
        assert result.number is None


@pytest.mark.parametrize(
    ("thermostat_id", "attribute", "number", "raw"),
    [
        (1, "temperatureTarget", Decimal("20.0"), "20.0"),
        (1, "temperatureActual", Decimal("24.58"), "24.58"),
        (5, "temperatureActual", Decimal("19.7"), "19.7"),
    ],
)
def test_temperature_values_are_read_as_decimal(
    thermostat_id: int, attribute: str, number: Decimal, raw: str
) -> None:
    topic = f"heizung/thermostate/{thermostat_id}/{attribute}/get"
    result = reading_from_topic(topic, raw)
    assert result == LegacyReading(thermostat_id, attribute, None, number)


@pytest.mark.parametrize(
    ("thermostat_id", "value"),
    [(1, "off"), (5, "heat")],
)
def test_thermostat_current_state_is_read_as_text(thermostat_id: int, value: str) -> None:
    topic = f"heizung/thermostate/{thermostat_id}/thermostatActualState/get"
    result = reading_from_topic(topic, value)
    assert result == LegacyReading(thermostat_id, "thermostatActualState", value, None)


def test_foreign_topic_prefix_is_skipped() -> None:
    assert reading_from_topic("zigbee2mqtt/Bad Thermostat", b"{}") is None


def test_set_instead_of_get_is_skipped() -> None:
    assert (
        reading_from_topic("heizung/thermostate/1/thermostatActualState/set", "heat") is None
    )


def test_thermostat_refresh_topic_is_skipped() -> None:
    # `heizung/thermostate/refresh` — no numeric id segment, no attribute.
    assert reading_from_topic("heizung/thermostate/refresh", b"force") is None


def test_configuration_topic_is_skipped() -> None:
    assert reading_from_topic("heizung/config/POLLING_RATE/get", "30") is None


def test_unreadable_temperature_value_is_skipped() -> None:
    assert reading_from_topic("heizung/thermostate/1/temperatureActual/get", "kaputt") is None


def test_unknown_attribute_is_skipped() -> None:
    assert reading_from_topic("heizung/thermostate/1/unbekannt/get", "irgendwas") is None


def test_non_numeric_thermostat_id_is_skipped() -> None:
    assert (
        reading_from_topic("heizung/thermostate/wohnzimmer/thermostatActualState/get", "off")
        is None
    )


def test_unreadable_payload_is_skipped() -> None:
    broken_bytes = b"\xff\xfe\x00"
    assert (
        reading_from_topic(
            "heizung/thermostate/1/thermostatActualState/get", broken_bytes
        )
        is None
    )


def test_bytes_payload_is_evaluated_like_text() -> None:
    result = reading_from_topic(
        "heizung/thermostate/5/temperatureTarget/get", b"21.0"
    )
    assert result == LegacyReading(5, "temperatureTarget", None, Decimal("21.0"))
