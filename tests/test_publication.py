import inspect
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from aiomqtt import Topic

from thermoctl.integrations.mqtt import publication
from thermoctl.integrations.mqtt.publication import (
    CommandTopics,
    DiscoveryMessage,
    StateTopics,
    alle_topics,
    availability_topic,
    command_topics,
    discovery_config_topic,
    discovery_payload,
    discovery_removal,
    states_topics,
    zone_discovery,
)


def _zone_name() -> str:
    data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in data["geraete"] if name == "Über Küche")


def _another_zone_name() -> str:
    data = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in data["geraete"] if name != _zone_name())


def test_state_topics_have_no_get_suffix() -> None:
    assert states_topics(17, "haus_nord") == StateTopics(
        current_temperature="haus_nord/zones/17/state/current_temperature",
        setpoint="haus_nord/zones/17/state/setpoint",
        operating_mode="haus_nord/zones/17/state/operating_mode",
        sensor_state="haus_nord/zones/17/state/sensor_state",
        wuerde_heizen="haus_nord/zones/17/state/would_heat",
        last_switch="haus_nord/zones/17/state/last_switch",
        next_switch="haus_nord/zones/17/state/next_switch",
    )


def test_command_topics_live_in_their_own_tree() -> None:
    assert command_topics(17, "haus_nord") == CommandTopics(
        setpoint="haus_nord/zones/17/command/setpoint",
        operating_mode="haus_nord/zones/17/command/operating_mode",
        boost="haus_nord/zones/17/command/boost",
    )


def test_availability_applies_to_the_service() -> None:
    assert availability_topic("haus_nord") == "haus_nord/availability"


def test_discovery_config_topic_is_unique_per_instance_and_zone() -> None:
    assert discovery_config_topic(17, "Haus/Nord") == (
        "homeassistant/climate/haus_nord_zone_17/config"
    )


def test_a_zone_name_with_an_umlaut_does_not_end_up_in_the_topic() -> None:
    name = _zone_name()
    assert name == "Über Küche"
    topics = alle_topics(17, "haus_nord")
    assert all(name not in topic for topic in topics)
    assert all(not ({"+", "#", "\0"} & set(topic)) for topic in topics)


def test_the_state_subscription_with_mqtt_wildcards_matches_no_command() -> None:
    subscription = "haus_nord/zones/+/state/#"
    state = states_topics(17, "haus_nord").__dict__.values()
    command = command_topics(17, "haus_nord").__dict__.values()
    assert all(Topic(topic).matches(subscription) for topic in state)
    assert not any(Topic(topic).matches(subscription) for topic in command)


def test_the_discovery_payload_is_valid_json_and_references_the_topics() -> None:
    data: dict[str, Any] = json.loads(
        discovery_payload(17, _zone_name(), temp_step=Decimal("0.25"), praefix="haus_nord")
    )
    state = states_topics(17, "haus_nord")
    command = command_topics(17, "haus_nord")

    assert data["unique_id"] == "haus_nord_zone_17"
    # Explicitly set: without `object_id`, Home Assistant derives the entity
    # id from the name -- and it would then be pinned to however the zone
    # name happened to be spelled at the time.
    assert data["object_id"] == "haus_nord_zone_17"
    # `name: null` means "use your device's name" in Home Assistant. Since the
    # per-zone split, the device *is* the zone, so the name goes there.
    assert data["name"] is None
    assert data["device"]["name"] == "Über Küche"
    assert data["availability_topic"] == "haus_nord/availability"
    assert data["current_temperature_topic"] == state.current_temperature
    assert data["temperature_state_topic"] == state.setpoint
    assert data["mode_state_topic"] == state.operating_mode
    assert data["action_topic"] == state.wuerde_heizen
    assert data["temperature_command_topic"] == command.setpoint
    assert data["mode_command_topic"] == command.operating_mode
    # The bounds come from the domain: this way Home Assistant shows the same
    # range the service itself accepts. A copied-down pair of numbers would
    # have fallen behind at the next change -- and the card would have
    # offered a value the server rejects.
    from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C

    assert (data["min_temp"], data["max_temp"], data["temp_step"]) == (
        float(MINIMUM_TEMPERATURE_C),
        float(MAXIMUM_TEMPERATURE_C),
        0.25,
    )


def test_one_device_per_zone_under_the_service() -> None:
    """Previously every entity hung off a single device "thermoctl".

    With a handful of zones with a dozen controllers each, that is an
    unsorted list. `via_device` still keeps them together: in Home Assistant
    the zones appear as their own devices under the service.
    """
    first = json.loads(discovery_payload(17, _zone_name(), praefix="haus_nord"))
    second = json.loads(discovery_payload(23, _another_zone_name(), praefix="haus_nord"))
    assert first["device"] == {
        "identifiers": ["thermoctl:haus_nord:zone:17"],
        "manufacturer": "thermoctl",
        "name": _zone_name(),
        "via_device": "thermoctl:haus_nord",
    }
    assert first["device"]["identifiers"] != second["device"]["identifiers"]
    assert first["device"]["via_device"] == second["device"]["via_device"]


def test_removal_uses_the_same_config_topic_and_an_empty_payload() -> None:
    message = zone_discovery(17, _zone_name(), praefix="haus_nord")
    assert message == DiscoveryMessage(
        "homeassistant/climate/haus_nord_zone_17/config", message.payload
    )
    assert discovery_removal(17, "haus_nord") == DiscoveryMessage(message.topic, "")


def test_this_module_publishes_nothing() -> None:
    source_code = inspect.getsource(publication)
    assert "publish" not in source_code
    assert "aiomqtt" not in source_code
    assert "integrations.mqtt.client" not in source_code


@pytest.mark.parametrize("praefix", ["", "haus/+", "haus/#", "haus\0nord"])
def test_an_invalid_prefix_is_rejected(praefix: str) -> None:
    with pytest.raises(ValueError, match="MQTT-Praefix"):
        availability_topic(praefix)


def test_invalid_discovery_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="Zonenkennung"):
        states_topics(0)
    with pytest.raises(ValueError, match="Discovery-Kennung"):
        discovery_config_topic(17, "🔥")
    with pytest.raises(ValueError, match="Zonenname"):
        discovery_payload(17, "  ")
    with pytest.raises(ValueError, match="Temperaturschritt"):
        discovery_payload(17, _zone_name(), temp_step=Decimal("0"))


@pytest.mark.parametrize(
    ("call", "arguments"),
    [
        (publication.mode_topics, (17, 0)),
        (publication.parameter_topics, (17, "Hysterese")),
        (publication.parameter_topics, (17, "hysterese/../ganz-woanders")),
        (publication.timestamp_discovery, (17, "Bad", "irgendwas", "Irgendwas")),
    ],
)
def test_subkeys_do_not_reach_the_topic_unchecked(
    call: Any, arguments: tuple[Any, ...]
) -> None:
    """A name from a loop is still an input.

    Every caller today passes through constants. That is exactly why the
    check lives here: it costs nothing and catches the day a name comes from
    the database -- a slash in it would otherwise open a level in the topic
    tree that nobody intended.
    """
    with pytest.raises(ValueError):
        call(*arguments)
