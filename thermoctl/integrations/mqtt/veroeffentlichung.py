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


@dataclass(frozen=True)
class BefehlsTopics:
    """Die von Zustandsabonnements getrennten Befehle einer Zone."""

    sollwert: str
    betriebsart: str


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
    )


def befehls_topics(zonen_id: int, praefix: str = "thermoctl") -> BefehlsTopics:
    """Baut die getrennten Befehls-Topics einer Zone."""
    basis = f"{_zonenbasis(zonen_id, praefix)}/befehl"
    return BefehlsTopics(
        sollwert=f"{basis}/sollwert",
        betriebsart=f"{basis}/betriebsart",
    )


def verfuegbarkeits_topic(praefix: str = "thermoctl") -> str:
    """Baut das gemeinsame Last-Will-Topic des Dienstes."""
    return f"{_praefix(praefix)}/verfuegbarkeit"


def _objekt_id(zonen_id: int, praefix: str) -> str:
    ohne_akzente = unicodedata.normalize("NFKD", _praefix(praefix)).encode("ascii", "ignore")
    kennung = re.sub(rb"[^a-zA-Z0-9_-]+", b"_", ohne_akzente).decode().strip("_").lower()
    if not kennung:
        raise ValueError("Das MQTT-Praefix ergibt keine gueltige Discovery-Kennung")
    return f"{kennung}_zone_{zonen_id}"


def discovery_config_topic(zonen_id: int, praefix: str = "thermoctl") -> str:
    """Baut das Home-Assistant-Config-Topic einer Climate-Zone."""
    _zonenbasis(zonen_id, praefix)
    return f"homeassistant/climate/{_objekt_id(zonen_id, praefix)}/config"


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
        "name": zonenname,
        "unique_id": objekt_id,
        "device": {
            "identifiers": [f"thermoctl:{_praefix(praefix)}"],
            "name": "thermoctl",
            "manufacturer": "thermoctl",
        },
        "availability_topic": verfuegbarkeits_topic(praefix),
        "payload_available": "online",
        "payload_not_available": "offline",
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
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def alle_topics(zonen_id: int, praefix: str = "thermoctl") -> tuple[str, ...]:
    """Liefert alle zonenbezogenen Topics fuer Vertragspruefungen."""
    zustand = asdict(zustands_topics(zonen_id, praefix)).values()
    befehl = asdict(befehls_topics(zonen_id, praefix)).values()
    return (*zustand, *befehl)
