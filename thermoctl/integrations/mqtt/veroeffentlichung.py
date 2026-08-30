"""Reine MQTT-Topics und Home-Assistant-Discovery-Nutzlasten.

Die stabile Datenbankkennung der Zone steht im Topic; der aenderbare, moeglicherweise
Leerzeichen oder Umlaute enthaltende Anzeigename bleibt in der Nutzlast. Zustand und Befehl
liegen in getrennten Teilbaeumen. Dadurch trifft ein Abonnement auf ``zustand/#`` niemals
einen Befehl, und Zustands-Topics brauchen kein missverstaendliches ``/get``-Suffix.

Diese Funktionen werden erst in Phase 4/5 an einen sendenden Adapter angeschlossen. Der
Vertrag entsteht schon jetzt, damit Topic-Struktur und Discovery ohne Zugriff auf die echte
Heizungsanlage vollstaendig geprueft werden koennen.
"""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from thermoctl.domain.modi import HOECHSTTEMPERATUR_C, MINDESTTEMPERATUR_C


@dataclass(frozen=True)
class ZustandsTopics:
    """Die einzeln abonnierbaren Zustandswerte einer Zone."""

    ist_temperatur: str
    sollwert: str
    betriebsart: str
    sensorzustand: str
    wuerde_heizen: str
    letzte_schaltung: str
    naechste_schaltung: str


@dataclass(frozen=True)
class BefehlsTopics:
    """Die von Zustandsabonnements getrennten Befehle einer Zone."""

    sollwert: str
    betriebsart: str
    boost: str


@dataclass(frozen=True)
class DiscoveryNachricht:
    """Topic und Nutzlast einer spaeter zu sendenden Discovery-Nachricht."""

    topic: str
    nutzlast: str


def _praefix(praefix: str) -> str:
    bereinigt = praefix.strip("/")
    if not bereinigt or any(zeichen in bereinigt for zeichen in ("+", "#", "\0")):
        raise ValueError("Das MQTT-Praefix muss gueltig sein und darf keine Wildcards enthalten")
    return bereinigt


def _zonenbasis(zonen_id: int, praefix: str) -> str:
    if zonen_id < 1:
        raise ValueError("Die Zonenkennung muss groesser als null sein")
    return f"{_praefix(praefix)}/zonen/{zonen_id}"


def zustands_topics(zonen_id: int, praefix: str = "thermoctl") -> ZustandsTopics:
    """Baut alle Zustands-Topics einer Zone."""
    basis = f"{_zonenbasis(zonen_id, praefix)}/zustand"
    return ZustandsTopics(
        ist_temperatur=f"{basis}/ist_temperatur",
        sollwert=f"{basis}/sollwert",
        betriebsart=f"{basis}/betriebsart",
        sensorzustand=f"{basis}/sensorzustand",
        wuerde_heizen=f"{basis}/wuerde_heizen",
        letzte_schaltung=f"{basis}/letzte_schaltung",
        naechste_schaltung=f"{basis}/naechste_schaltung",
    )


def befehls_topics(zonen_id: int, praefix: str = "thermoctl") -> BefehlsTopics:
    """Baut die getrennten Befehls-Topics einer Zone."""
    basis = f"{_zonenbasis(zonen_id, praefix)}/befehl"
    return BefehlsTopics(
        sollwert=f"{basis}/sollwert",
        betriebsart=f"{basis}/betriebsart",
        boost=f"{basis}/boost",
    )


def modus_topics(zonen_id: int, modus_id: int, praefix: str = "thermoctl") -> tuple[str, str]:
    """Zustand und Befehl fuer die Solltemperatur **eines** Modus dieser Zone."""
    if modus_id < 1:
        raise ValueError("Die Moduskennung muss groesser als null sein")
    basis = _zonenbasis(zonen_id, praefix)
    return (f"{basis}/zustand/modus/{modus_id}", f"{basis}/befehl/modus/{modus_id}")


def parameter_topics(zonen_id: int, name: str, praefix: str = "thermoctl") -> tuple[str, str]:
    """Zustand und Befehl fuer **einen** Regelparameter dieser Zone."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"Kein gueltiger Parametername: {name!r}")
    basis = _zonenbasis(zonen_id, praefix)
    return (f"{basis}/zustand/parameter/{name}", f"{basis}/befehl/parameter/{name}")


def scharf_topic(praefix: str = "thermoctl") -> str:
    """Ob die Regelung wirklich schaltet -- eine Aussage fuer den ganzen Dienst.

    Der Trockenlauf stand bis hierher im *Namen* jeder Zone. Das war gut sichtbar und
    genau deshalb falsch: Home Assistant leitet die Entitaetskennung beim ersten
    Auftauchen aus dem Namen ab, und eine Zone, die zuerst im Trockenlauf erschien,
    hiess danach fuer immer `climate.thermoctl_zone_1_trockenlauf`. Der Betriebszustand
    gehoert in eine eigene Entitaet, nicht in den Namen einer anderen.
    """
    return f"{_praefix(praefix)}/zustand/scharf"


def verfuegbarkeits_topic(praefix: str = "thermoctl") -> str:
    """Baut das gemeinsame Last-Will-Topic des Dienstes."""
    return f"{_praefix(praefix)}/verfuegbarkeit"


def _kennung(praefix: str) -> str:
    """Das Praefix, auf das reduziert, was in einer Discovery-Kennung stehen darf."""
    ohne_akzente = unicodedata.normalize("NFKD", _praefix(praefix)).encode("ascii", "ignore")
    kennung = re.sub(rb"[^a-zA-Z0-9_-]+", b"_", ohne_akzente).decode().strip("_").lower()
    if not kennung:
        raise ValueError("Das MQTT-Praefix ergibt keine gueltige Discovery-Kennung")
    return kennung


def _objekt_id(zonen_id: int, praefix: str) -> str:
    _zonenbasis(zonen_id, praefix)
    return f"{_kennung(praefix)}_zone_{zonen_id}"


def discovery_config_topic(zonen_id: int, praefix: str = "thermoctl") -> str:
    """Baut das Home-Assistant-Config-Topic einer Climate-Zone."""
    _zonenbasis(zonen_id, praefix)
    return f"homeassistant/climate/{_objekt_id(zonen_id, praefix)}/config"


def _config_topic(komponente: str, objekt_id: str) -> str:
    return f"homeassistant/{komponente}/{objekt_id}/config"


def _geraeteblock(zonen_id: int, zonenname: str, praefix: str) -> dict[str, Any]:
    """Ein Home-Assistant-Geraet je Zone, unter dem Dienst als uebergeordnetem.

    Vorher hingen alle Entitaeten an einem einzigen Geraet "thermoctl". Bei einer
    Handvoll Zonen mit je einem Dutzend Reglern ist das eine unsortierte Liste; nach
    Zonen gruppiert steht beieinander, was zusammengehoert. Die `unique_id` der
    Entitaeten aendert sich dadurch nicht -- Home Assistant haengt eine bestehende
    Entitaet nur um, die Entitaetskennung bleibt.
    """
    return {
        "identifiers": [f"thermoctl:{_praefix(praefix)}:zone:{zonen_id}"],
        "name": zonenname,
        "manufacturer": "thermoctl",
        "via_device": f"thermoctl:{_praefix(praefix)}",
    }


def _grundgeruest(zonen_id: int, zonenname: str, praefix: str) -> dict[str, Any]:
    """Was jede Entitaet einer Zone gleich traegt."""
    return {
        "device": _geraeteblock(zonen_id, zonenname, praefix),
        "availability_topic": verfuegbarkeits_topic(praefix),
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def _als_json(daten: dict[str, Any]) -> str:
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def discovery_nutzlast(
    zonen_id: int,
    zonenname: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    praefix: str = "thermoctl",
) -> str:
    """Baut die JSON-Nutzlast fuer eine Home-Assistant-Climate-Zone."""
    if not zonenname.strip():
        raise ValueError("Der Zonenname darf nicht leer sein")
    if temp_step <= 0:
        raise ValueError("Der Temperaturschritt muss groesser als null sein")

    zustand = zustands_topics(zonen_id, praefix)
    befehl = befehls_topics(zonen_id, praefix)
    objekt_id = _objekt_id(zonen_id, praefix)
    daten: dict[str, Any] = {
        **_grundgeruest(zonen_id, zonenname, praefix),
        "name": None,
        "unique_id": objekt_id,
        # Ausdruecklich gesetzt, damit die Entitaetskennung nicht aus dem Namen
        # abgeleitet wird. Sonst haengt sie an der Schreibweise des Zonennamens von
        # damals -- und aenderte sich mit jeder Umbenennung der Zone.
        "object_id": objekt_id,
        "current_temperature_topic": zustand.ist_temperatur,
        "temperature_state_topic": zustand.sollwert,
        "temperature_command_topic": befehl.sollwert,
        "mode_state_topic": zustand.betriebsart,
        "mode_command_topic": befehl.betriebsart,
        "mode_state_template": "{{ 'heat' if value == 'manual' else value }}",
        "mode_command_template": "{{ 'manual' if value == 'heat' else value }}",
        "action_topic": zustand.wuerde_heizen,
        "action_template": "{{ 'heating' if value == 'true' else 'idle' }}",
        "modes": ["auto", "heat", "off"],
        # Aus der Domaene, nicht abgeschrieben: Home Assistant zeigt damit
        # denselben Bereich an, den der Dienst auch annimmt.
        "min_temp": float(MINDESTTEMPERATUR_C),
        "max_temp": float(HOECHSTTEMPERATUR_C),
        "temp_step": float(temp_step),
        "temperature_unit": "C",
    }
    return _als_json(daten)


def discovery_anmeldung(
    zonen_id: int,
    zonenname: str,
    *,
    temp_step: Decimal = Decimal("0.5"),
    praefix: str = "thermoctl",
) -> DiscoveryNachricht:
    """Buendelt Config-Topic und Discovery-Nutzlast fuer eine Zone."""
    return DiscoveryNachricht(
        discovery_config_topic(zonen_id, praefix),
        discovery_nutzlast(zonen_id, zonenname, temp_step=temp_step, praefix=praefix),
    )


def discovery_abmeldung(zonen_id: int, praefix: str = "thermoctl") -> DiscoveryNachricht:
    """Baut die leere Discovery-Nachricht zum Entfernen einer Zone."""
    return DiscoveryNachricht(discovery_config_topic(zonen_id, praefix), "")


def boost_anmeldung(
    zonen_id: int, zonenname: str, praefix: str = "thermoctl"
) -> DiscoveryNachricht:
    """Der Knopf, der die naechste Schaltung vorzieht."""
    objekt_id = f"{_objekt_id(zonen_id, praefix)}_boost"
    daten: dict[str, Any] = {
        **_grundgeruest(zonen_id, zonenname, praefix),
        "name": "Boost",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "command_topic": befehls_topics(zonen_id, praefix).boost,
        "payload_press": "boost",
        "icon": "mdi:fast-forward",
    }
    return DiscoveryNachricht(_config_topic("button", objekt_id), _als_json(daten))


def zeitstempel_anmeldung(
    zonen_id: int,
    zonenname: str,
    art: str,
    beschriftung: str,
    praefix: str = "thermoctl",
) -> DiscoveryNachricht:
    """Ein Zeitpunkt als Sensor -- 'letzte Schaltung' und 'naechster Moduswechsel'.

    `device_class: timestamp` heisst: Home Assistant erwartet ISO-8601 mit Zeitzone und
    zeigt selbst "vor 12 Minuten" an. Deshalb steht hier kein vorformatierter Text --
    die Anzeigesprache gehoert dorthin, wo sie gelesen wird.
    """
    if art not in ("letzte_schaltung", "naechste_schaltung"):
        raise ValueError(f"Unbekannte Zeitstempelart: {art!r}")
    objekt_id = f"{_objekt_id(zonen_id, praefix)}_{art}"
    daten: dict[str, Any] = {
        **_grundgeruest(zonen_id, zonenname, praefix),
        "name": beschriftung,
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": getattr(zustands_topics(zonen_id, praefix), art),
        "device_class": "timestamp",
    }
    return DiscoveryNachricht(_config_topic("sensor", objekt_id), _als_json(daten))


def modus_anmeldung(
    zonen_id: int,
    zonenname: str,
    modus_id: int,
    modusname: str,
    praefix: str = "thermoctl",
    temp_step: Decimal = Decimal("0.5"),
) -> DiscoveryNachricht:
    """Die Solltemperatur eines Modus als Zahleneingabe.

    Der Thermostat zeigt immer nur den Modus, der gerade gilt. Wer die Nachtabsenkung
    am Nachmittag verstellen will, braucht dafuer eine eigene Eingabe -- sonst muesste
    er bis zum Abend warten.
    """
    objekt_id = f"{_objekt_id(zonen_id, praefix)}_modus_{modus_id}"
    daten: dict[str, Any] = {
        **_grundgeruest(zonen_id, zonenname, praefix),
        "name": f"Sollwert {modusname}",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": modus_topics(zonen_id, modus_id, praefix)[0],
        "command_topic": modus_topics(zonen_id, modus_id, praefix)[1],
        "min": float(MINDESTTEMPERATUR_C),
        "max": float(HOECHSTTEMPERATUR_C),
        "step": float(temp_step),
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "mode": "box",
    }
    return DiscoveryNachricht(_config_topic("number", objekt_id), _als_json(daten))


def parameter_anmeldung(
    zonen_id: int,
    zonenname: str,
    name: str,
    beschriftung: str,
    minimum: Decimal,
    maximum: Decimal,
    schritt: Decimal,
    einheit: str | None = None,
    praefix: str = "thermoctl",
) -> DiscoveryNachricht:
    """Ein Regelparameter der Zone als Zahleneingabe."""
    zustand, befehl = parameter_topics(zonen_id, name, praefix)
    objekt_id = f"{_objekt_id(zonen_id, praefix)}_parameter_{name}"
    daten: dict[str, Any] = {
        **_grundgeruest(zonen_id, zonenname, praefix),
        "name": beschriftung,
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": zustand,
        "command_topic": befehl,
        "min": float(minimum),
        "max": float(maximum),
        "step": float(schritt),
        "mode": "box",
        # Regelparameter gehoeren nicht auf die Zonenkarte, sondern hinter "Konfiguration".
        "entity_category": "config",
    }
    if einheit is not None:
        daten["unit_of_measurement"] = einheit
    return DiscoveryNachricht(_config_topic("number", objekt_id), _als_json(daten))


def scharf_anmeldung(praefix: str = "thermoctl") -> DiscoveryNachricht:
    """Ob die Regelung wirklich schaltet, als eigene Entitaet fuer den ganzen Dienst."""
    objekt_id = f"{_kennung(praefix)}_scharf"
    daten: dict[str, Any] = {
        "device": {
            "identifiers": [f"thermoctl:{_praefix(praefix)}"],
            "name": "thermoctl",
            "manufacturer": "thermoctl",
        },
        "availability_topic": verfuegbarkeits_topic(praefix),
        "payload_available": "online",
        "payload_not_available": "offline",
        "name": "Regelung scharf",
        "unique_id": objekt_id,
        "object_id": objekt_id,
        "state_topic": scharf_topic(praefix),
        "payload_on": "true",
        "payload_off": "false",
        "device_class": "running",
    }
    return DiscoveryNachricht(_config_topic("binary_sensor", objekt_id), _als_json(daten))


def alle_topics(zonen_id: int, praefix: str = "thermoctl") -> tuple[str, ...]:
    """Liefert alle zonenbezogenen Topics fuer Vertragspruefungen."""
    zustand = asdict(zustands_topics(zonen_id, praefix)).values()
    befehl = asdict(befehls_topics(zonen_id, praefix)).values()
    return (*zustand, *befehl)
