import inspect
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from aiomqtt import Topic

from thermoctl.integrations.mqtt import veroeffentlichung
from thermoctl.integrations.mqtt.veroeffentlichung import (
    BefehlsTopics,
    DiscoveryNachricht,
    ZustandsTopics,
    alle_topics,
    befehls_topics,
    discovery_abmeldung,
    discovery_anmeldung,
    discovery_config_topic,
    discovery_nutzlast,
    verfuegbarkeits_topic,
    zustands_topics,
)


def _zonenname() -> str:
    daten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in daten["geraete"] if name == "Über Küche")


def _weiterer_zonenname() -> str:
    daten = json.loads(Path("tests/daten/anlage-beispiele.json").read_text())
    return next(name for name in daten["geraete"] if name != _zonenname())


def test_zustands_topics_sind_ohne_get_suffix() -> None:
    assert zustands_topics(17, "haus_nord") == ZustandsTopics(
        ist_temperatur="haus_nord/zonen/17/zustand/ist_temperatur",
        sollwert="haus_nord/zonen/17/zustand/sollwert",
        betriebsart="haus_nord/zonen/17/zustand/betriebsart",
        sensorzustand="haus_nord/zonen/17/zustand/sensorzustand",
        wuerde_heizen="haus_nord/zonen/17/zustand/wuerde_heizen",
        letzte_schaltung="haus_nord/zonen/17/zustand/letzte_schaltung",
        naechste_schaltung="haus_nord/zonen/17/zustand/naechste_schaltung",
    )


def test_befehls_topics_liegen_im_eigenen_baum() -> None:
    assert befehls_topics(17, "haus_nord") == BefehlsTopics(
        sollwert="haus_nord/zonen/17/befehl/sollwert",
        betriebsart="haus_nord/zonen/17/befehl/betriebsart",
        boost="haus_nord/zonen/17/befehl/boost",
    )


def test_verfuegbarkeit_gilt_fuer_den_dienst() -> None:
    assert verfuegbarkeits_topic("haus_nord") == "haus_nord/verfuegbarkeit"


def test_discovery_config_topic_ist_eindeutig_je_instanz_und_zone() -> None:
    assert discovery_config_topic(17, "Haus/Nord") == (
        "homeassistant/climate/haus_nord_zone_17/config"
    )


def test_zonenname_mit_umlaut_geraet_nicht_ins_topic() -> None:
    name = _zonenname()
    assert name == "Über Küche"
    topics = alle_topics(17, "haus_nord")
    assert all(name not in topic for topic in topics)
    assert all(not ({"+", "#", "\0"} & set(topic)) for topic in topics)


def test_zustandsabonnement_trifft_mit_mqtt_wildcards_keinen_befehl() -> None:
    abonnement = "haus_nord/zonen/+/zustand/#"
    zustand = zustands_topics(17, "haus_nord").__dict__.values()
    befehl = befehls_topics(17, "haus_nord").__dict__.values()
    assert all(Topic(topic).matches(abonnement) for topic in zustand)
    assert not any(Topic(topic).matches(abonnement) for topic in befehl)


def test_discovery_nutzlast_ist_gueltiges_json_und_verweist_auf_topics() -> None:
    daten: dict[str, Any] = json.loads(
        discovery_nutzlast(17, _zonenname(), temp_step=Decimal("0.25"), praefix="haus_nord")
    )
    zustand = zustands_topics(17, "haus_nord")
    befehl = befehls_topics(17, "haus_nord")

    assert daten["unique_id"] == "haus_nord_zone_17"
    # Ausdruecklich gesetzt: Ohne `object_id` leitet Home Assistant die
    # Entitaetskennung aus dem Namen ab -- und sie haengt dann an der Schreibweise
    # des Zonennamens von damals.
    assert daten["object_id"] == "haus_nord_zone_17"
    # `name: null` heisst in Home Assistant "heisse wie dein Geraet". Das Geraet ist
    # seit der Aufteilung je Zone die Zone selbst, also steht der Name dort.
    assert daten["name"] is None
    assert daten["device"]["name"] == "Über Küche"
    assert daten["availability_topic"] == "haus_nord/verfuegbarkeit"
    assert daten["current_temperature_topic"] == zustand.ist_temperatur
    assert daten["temperature_state_topic"] == zustand.sollwert
    assert daten["mode_state_topic"] == zustand.betriebsart
    assert daten["action_topic"] == zustand.wuerde_heizen
    assert daten["temperature_command_topic"] == befehl.sollwert
    assert daten["mode_command_topic"] == befehl.betriebsart
    # Die Grenzen kommen aus der Domaene: Home Assistant zeigt damit denselben Bereich
    # an, den der Dienst auch annimmt. Ein abgeschriebenes Paar Zahlen waere beim
    # naechsten Verschieben zurueckgeblieben -- und die Karte haette einen Wert
    # angeboten, den der Server ablehnt.
    from thermoctl.domain.modi import HOECHSTTEMPERATUR_C, MINDESTTEMPERATUR_C

    assert (daten["min_temp"], daten["max_temp"], daten["temp_step"]) == (
        float(MINDESTTEMPERATUR_C),
        float(HOECHSTTEMPERATUR_C),
        0.25,
    )


def test_je_zone_ein_geraet_unter_dem_dienst() -> None:
    """Vorher hingen alle Entitaeten an einem einzigen Geraet "thermoctl".

    Bei einer Handvoll Zonen mit je einem Dutzend Reglern ist das eine unsortierte
    Liste. `via_device` haelt sie trotzdem zusammen: In Home Assistant stehen die
    Zonen als eigene Geraete unter dem Dienst.
    """
    erste = json.loads(discovery_nutzlast(17, _zonenname(), praefix="haus_nord"))
    zweite = json.loads(discovery_nutzlast(23, _weiterer_zonenname(), praefix="haus_nord"))
    assert erste["device"] == {
        "identifiers": ["thermoctl:haus_nord:zone:17"],
        "manufacturer": "thermoctl",
        "name": _zonenname(),
        "via_device": "thermoctl:haus_nord",
    }
    assert erste["device"]["identifiers"] != zweite["device"]["identifiers"]
    assert erste["device"]["via_device"] == zweite["device"]["via_device"]


def test_abmeldung_nutzt_dasselbe_config_topic_und_leere_nutzlast() -> None:
    anmeldung = discovery_anmeldung(17, _zonenname(), praefix="haus_nord")
    assert anmeldung == DiscoveryNachricht(
        "homeassistant/climate/haus_nord_zone_17/config", anmeldung.nutzlast
    )
    assert discovery_abmeldung(17, "haus_nord") == DiscoveryNachricht(anmeldung.topic, "")


def test_dieses_modul_veroeffentlicht_nichts() -> None:
    quelltext = inspect.getsource(veroeffentlichung)
    assert "publish" not in quelltext
    assert "aiomqtt" not in quelltext
    assert "integrations.mqtt.client" not in quelltext


@pytest.mark.parametrize("praefix", ["", "haus/+", "haus/#", "haus\0nord"])
def test_ungueltiges_praefix_wird_abgewiesen(praefix: str) -> None:
    with pytest.raises(ValueError, match="MQTT-Praefix"):
        verfuegbarkeits_topic(praefix)


def test_ungueltige_discovery_eingaben_werden_abgewiesen() -> None:
    with pytest.raises(ValueError, match="Zonenkennung"):
        zustands_topics(0)
    with pytest.raises(ValueError, match="Discovery-Kennung"):
        discovery_config_topic(17, "🔥")
    with pytest.raises(ValueError, match="Zonenname"):
        discovery_nutzlast(17, "  ")
    with pytest.raises(ValueError, match="Temperaturschritt"):
        discovery_nutzlast(17, _zonenname(), temp_step=Decimal("0"))


@pytest.mark.parametrize(
    ("aufruf", "argumente"),
    [
        (veroeffentlichung.modus_topics, (17, 0)),
        (veroeffentlichung.parameter_topics, (17, "Hysterese")),
        (veroeffentlichung.parameter_topics, (17, "hysterese/../ganz-woanders")),
        (veroeffentlichung.zeitstempel_anmeldung, (17, "Bad", "irgendwas", "Irgendwas")),
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
