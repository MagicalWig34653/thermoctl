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
    daten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in daten["geraete"] if name == "Über Küche")


def _another_zone_name() -> str:
    daten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in daten["geraete"] if name != _zone_name())


def test_zustands_topics_sind_ohne_get_suffix() -> None:
    assert states_topics(17, "haus_nord") == StateTopics(
        current_temperature="haus_nord/zones/17/state/current_temperature",
        setpoint="haus_nord/zones/17/state/setpoint",
        operating_mode="haus_nord/zones/17/state/operating_mode",
        sensor_state="haus_nord/zones/17/state/sensor_state",
        wuerde_heizen="haus_nord/zones/17/state/would_heat",
        last_switch="haus_nord/zones/17/state/last_switch",
        next_switch="haus_nord/zones/17/state/next_switch",
    )


def test_befehls_topics_liegen_im_eigenen_baum() -> None:
    assert command_topics(17, "haus_nord") == CommandTopics(
        setpoint="haus_nord/zones/17/command/setpoint",
        operating_mode="haus_nord/zones/17/command/operating_mode",
        boost="haus_nord/zones/17/command/boost",
    )


def test_verfuegbarkeit_gilt_fuer_den_dienst() -> None:
    assert availability_topic("haus_nord") == "haus_nord/availability"


def test_discovery_config_topic_ist_eindeutig_je_instanz_und_zone() -> None:
    assert discovery_config_topic(17, "Haus/Nord") == (
        "homeassistant/climate/haus_nord_zone_17/config"
    )


def test_zonenname_mit_umlaut_geraet_nicht_ins_topic() -> None:
    name = _zone_name()
    assert name == "Über Küche"
    topics = alle_topics(17, "haus_nord")
    assert all(name not in topic for topic in topics)
    assert all(not ({"+", "#", "\0"} & set(topic)) for topic in topics)


def test_zustandsabonnement_trifft_mit_mqtt_wildcards_keinen_befehl() -> None:
    abonnement = "haus_nord/zones/+/state/#"
    state = states_topics(17, "haus_nord").__dict__.values()
    command = command_topics(17, "haus_nord").__dict__.values()
    assert all(Topic(topic).matches(abonnement) for topic in state)
    assert not any(Topic(topic).matches(abonnement) for topic in command)


def test_discovery_nutzlast_ist_gueltiges_json_und_verweist_auf_topics() -> None:
    daten: dict[str, Any] = json.loads(
        discovery_payload(17, _zone_name(), temp_step=Decimal("0.25"), praefix="haus_nord")
    )
    state = states_topics(17, "haus_nord")
    command = command_topics(17, "haus_nord")

    assert daten["unique_id"] == "haus_nord_zone_17"
    # Ausdruecklich gesetzt: Ohne `object_id` leitet Home Assistant die
    # Entitaetskennung aus dem Namen ab -- und sie haengt dann an der Schreibweise
    # des Zonennamens von damals.
    assert daten["object_id"] == "haus_nord_zone_17"
    # `name: null` heisst in Home Assistant "heisse wie dein Geraet". Das Geraet ist
    # seit der Aufteilung je Zone die Zone selbst, also steht der Name dort.
    assert daten["name"] is None
    assert daten["device"]["name"] == "Über Küche"
    assert daten["availability_topic"] == "haus_nord/availability"
    assert daten["current_temperature_topic"] == state.current_temperature
    assert daten["temperature_state_topic"] == state.setpoint
    assert daten["mode_state_topic"] == state.operating_mode
    assert daten["action_topic"] == state.wuerde_heizen
    assert daten["temperature_command_topic"] == command.setpoint
    assert daten["mode_command_topic"] == command.operating_mode
    # Die Grenzen kommen aus der Domaene: Home Assistant zeigt damit denselben Bereich
    # an, den der Dienst auch annimmt. Ein abgeschriebenes Paar Zahlen waere beim
    # naechsten Verschieben zurueckgeblieben -- und die Karte haette einen Wert
    # angeboten, den der Server ablehnt.
    from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C

    assert (daten["min_temp"], daten["max_temp"], daten["temp_step"]) == (
        float(MINIMUM_TEMPERATURE_C),
        float(MAXIMUM_TEMPERATURE_C),
        0.25,
    )


def test_je_zone_ein_geraet_unter_dem_dienst() -> None:
    """Vorher hingen alle Entitaeten an einem einzigen Geraet "thermoctl".

    Bei einer Handvoll Zonen mit je einem Dutzend Reglern ist das eine unsortierte
    Liste. `via_device` haelt sie trotzdem zusammen: In Home Assistant stehen die
    Zonen als eigene Geraete unter dem Dienst.
    """
    first = json.loads(discovery_payload(17, _zone_name(), praefix="haus_nord"))
    zweite = json.loads(discovery_payload(23, _another_zone_name(), praefix="haus_nord"))
    assert first["device"] == {
        "identifiers": ["thermoctl:haus_nord:zone:17"],
        "manufacturer": "thermoctl",
        "name": _zone_name(),
        "via_device": "thermoctl:haus_nord",
    }
    assert first["device"]["identifiers"] != zweite["device"]["identifiers"]
    assert first["device"]["via_device"] == zweite["device"]["via_device"]


def test_abmeldung_nutzt_dasselbe_config_topic_und_leere_nutzlast() -> None:
    login = zone_discovery(17, _zone_name(), praefix="haus_nord")
    assert login == DiscoveryMessage(
        "homeassistant/climate/haus_nord_zone_17/config", login.payload
    )
    assert discovery_removal(17, "haus_nord") == DiscoveryMessage(login.topic, "")


def test_dieses_modul_veroeffentlicht_nichts() -> None:
    quelltext = inspect.getsource(publication)
    assert "publish" not in quelltext
    assert "aiomqtt" not in quelltext
    assert "integrations.mqtt.client" not in quelltext


@pytest.mark.parametrize("praefix", ["", "haus/+", "haus/#", "haus\0nord"])
def test_ungueltiges_praefix_wird_abgewiesen(praefix: str) -> None:
    with pytest.raises(ValueError, match="MQTT-Praefix"):
        availability_topic(praefix)


def test_ungueltige_discovery_eingaben_werden_abgewiesen() -> None:
    with pytest.raises(ValueError, match="Zonenkennung"):
        states_topics(0)
    with pytest.raises(ValueError, match="Discovery-Kennung"):
        discovery_config_topic(17, "🔥")
    with pytest.raises(ValueError, match="Zonenname"):
        discovery_payload(17, "  ")
    with pytest.raises(ValueError, match="Temperaturschritt"):
        discovery_payload(17, _zone_name(), temp_step=Decimal("0"))


@pytest.mark.parametrize(
    ("aufruf", "argumente"),
    [
        (publication.mode_topics, (17, 0)),
        (publication.parameter_topics, (17, "Hysterese")),
        (publication.parameter_topics, (17, "hysterese/../ganz-woanders")),
        (publication.timestamp_discovery, (17, "Bad", "irgendwas", "Irgendwas")),
    ],
)
def test_unterschluessel_kommen_nicht_ungeprueft_ins_topic(
    aufruf: Any, argumente: tuple[Any, ...]
) -> None:
    """Ein Name aus einer Schleife ist trotzdem eine Eingabe.

    Alle heutigen Aufrufer reichen Konstanten durch. Genau deshalb steht die Pruefung
    hier: Sie kostet nichts und faengt den Tag ab, an dem ein Name aus der Datenbank
    kommt -- ein Schraegstrich darin oeffnete sonst eine Ebene im Topic-Baum, die
    niemand vorgesehen hat.
    """
    with pytest.raises(ValueError):
        aufruf(*argumente)
