import json
from pathlib import Path

import pytest

from thermoctl.integrations.mqtt.zigbee2mqtt import (
    MessageKind,
    TopicCut,
    abonnements,
    zuschneiden,
)


@pytest.fixture
def basis() -> str:
    return "testbasis"


@pytest.fixture
def device_name_with_umlaut_and_space() -> str:
    daten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(
        name
        for name in daten["geraete"]
        if " " in name and any(zeichen in name for zeichen in "äöüÄÖÜ")
    )


def test_abonnements_sind_auf_lesende_topics_begrenzt(basis: str) -> None:
    assert abonnements(basis) == [
        f"{basis}/bridge/devices",
        f"{basis}/bridge/state",
        f"{basis}/+",
        f"{basis}/+/availability",
    ]


def test_brueckennachrichten_werden_unterschieden(basis: str) -> None:
    assert zuschneiden(f"{basis}/bridge/devices", basis) == TopicCut(
        MessageKind.DEVICE_LIST, None
    )
    assert zuschneiden(f"{basis}/bridge/state", basis) == TopicCut(
        MessageKind.BRIDGE_STATE, None
    )


def test_geraetename_bleibt_unveraendert(
    basis: str, device_name_with_umlaut_and_space: str
) -> None:
    name = device_name_with_umlaut_and_space
    assert zuschneiden(f"{basis}/{name}", basis) == TopicCut(
        MessageKind.DEVICE_STATE, name
    )
    assert zuschneiden(f"{basis}/{name}/availability", basis) == TopicCut(
        MessageKind.AVAILABILITY, name
    )


def test_unbekannte_topics_werden_nicht_als_geraet_gedeutet(basis: str) -> None:
    unbekannt = TopicCut(MessageKind.UNBEKANNT, None)
    assert zuschneiden("fremd/geraet", basis) == unbekannt
    assert zuschneiden(f"{basis}/bridge", basis) == unbekannt
    assert zuschneiden(f"{basis}/bridge/logging", basis) == unbekannt
    assert zuschneiden(f"{basis}/geraet/set", basis) == unbekannt
    assert zuschneiden(f"{basis}/geraet/availability/set", basis) == unbekannt
