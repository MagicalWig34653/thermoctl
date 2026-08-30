import json
from pathlib import Path

import pytest

from thermoctl.integrations.mqtt.zigbee2mqtt import (
    MessageKind,
    TopicCut,
    subscriptions,
    trim,
)


@pytest.fixture
def base() -> str:
    return "testbasis"


@pytest.fixture
def device_name_with_umlaut_and_space() -> str:
    data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(
        name
        for name in data["geraete"]
        if " " in name and any(character in name for character in "äöüÄÖÜ")
    )


def test_subscriptions_are_limited_to_read_only_topics(base: str) -> None:
    assert subscriptions(base) == [
        f"{base}/bridge/devices",
        f"{base}/bridge/state",
        f"{base}/+",
        f"{base}/+/availability",
    ]


def test_bridge_messages_are_distinguished(base: str) -> None:
    assert trim(f"{base}/bridge/devices", base) == TopicCut(
        MessageKind.DEVICE_LIST, None
    )
    assert trim(f"{base}/bridge/state", base) == TopicCut(
        MessageKind.BRIDGE_STATE, None
    )


def test_device_name_remains_unchanged(
    base: str, device_name_with_umlaut_and_space: str
) -> None:
    name = device_name_with_umlaut_and_space
    assert trim(f"{base}/{name}", base) == TopicCut(
        MessageKind.DEVICE_STATE, name
    )
    assert trim(f"{base}/{name}/availability", base) == TopicCut(
        MessageKind.AVAILABILITY, name
    )


def test_unknown_topics_are_not_interpreted_as_a_device(base: str) -> None:
    unknown = TopicCut(MessageKind.UNBEKANNT, None)
    assert trim("fremd/geraet", base) == unknown
    assert trim(f"{base}/bridge", base) == unknown
    assert trim(f"{base}/bridge/logging", base) == unknown
    assert trim(f"{base}/geraet/set", base) == unknown
    assert trim(f"{base}/geraet/availability/set", base) == unknown
