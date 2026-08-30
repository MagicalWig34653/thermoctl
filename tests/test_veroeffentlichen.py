"""Der Aufrufer, der den eigenen Zustand sendet und die Zonen bei Home Assistant anmeldet.

Die Nutzlasten selbst sind in `test_veroeffentlichung.py` geprueft. Hier geht es um die
Fragen daneben: **wann** gesendet wird, **wie** der Betriebszustand dabei sichtbar bleibt,
und wann abgemeldet wird.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
from thermoctl.domain.steuerung import scharf_schalten
from thermoctl.services.veroeffentlichen import (
    TROCKENLAUF_ZUSATZ,
    Veroeffentlichungsstand,
    zyklus,
)

JETZT = datetime(2026, 8, 31, 7, 0)


class Mitschrift:
    """Ein Veroeffentlicher, der nur mitschreibt.

    Er sendet immer -- der Riegel des Trockenlaufs sitzt im echten Client und gilt allein
    Schaltbefehlen. Hier wird geprueft, *was* der Dienst senden will.
    """

    def __init__(self) -> None:
        self.nachrichten: list[tuple[str, str]] = []
        self.geschaltet: list[str] = []

    async def veroeffentlichen(self, topic: str, nutzlast: str, *, schaltet: bool) -> bool:
        self.nachrichten.append((topic, nutzlast))
        if schaltet:
            self.geschaltet.append(topic)
        return True

    def topics(self) -> list[str]:
        return [t for t, _ in self.nachrichten]


async def _lauf(session: Session, stand: Veroeffentlichungsstand) -> Mitschrift:
    client = Mitschrift()
    await zyklus(session, client, stand, "thermoctl", JETZT)
    return client


@pytest.mark.anyio
async def test_im_trockenlauf_wird_veroeffentlicht(session: Session) -> None:
    """Eine Zustandsmeldung bewegt nichts. Eine Anbindung, die man erst nach dem
    Scharfschalten ausprobieren kann, laesst sich genau dann nicht mehr gefahrlos
    pruefen, wenn ein Fehler noch folgenlos waere."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "probezone")

    client = await _lauf(session, Veroeffentlichungsstand())

    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in client.topics()
    assert f"thermoctl/zonen/{zone.id}/zustand/sollwert" in client.topics()


@pytest.mark.anyio
async def test_keine_dieser_nachrichten_schaltet(session: Session) -> None:
    """Gegenprobe zur Zeile oben: Veroeffentlicht wird, geschaltet nicht. Ohne sie waere
    der Test darueber auch von einer Fassung erfuellt, die im Trockenlauf Ventile
    bewegt."""
    einstellungen_anlegen(session)
    zone_anlegen(session, "harmlos")
    client = await _lauf(session, Veroeffentlichungsstand())
    assert client.geschaltet == []


@pytest.mark.anyio
async def test_der_trockenlauf_steht_im_namen(session: Session) -> None:
    """In Home Assistant ist der Name das Einzige, was an jeder Karte sichtbar ist. Eine
    Notiz an anderer Stelle laese dort niemand."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "namenszone")

    client = await _lauf(session, Veroeffentlichungsstand())
    anmeldung = dict(client.nachrichten)[
        f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    ]
    assert TROCKENLAUF_ZUSATZ.strip() in anmeldung


@pytest.mark.anyio
async def test_scharf_verschwindet_der_zusatz(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "scharfe-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)

    client = await _lauf(session, Veroeffentlichungsstand())
    anmeldung = dict(client.nachrichten)[
        f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    ]
    assert TROCKENLAUF_ZUSATZ.strip() not in anmeldung


@pytest.mark.anyio
async def test_beim_wechsel_wird_die_anmeldung_erneuert(session: Session) -> None:
    """Der Name traegt den Betriebszustand -- ohne Erneuerung truege die Zone in Home
    Assistant fuer immer den Namen von damals."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "wechselzone")
    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)

    scharf_schalten(session, True, begruendung="Test", user_id=None)
    client = await _lauf(session, stand)
    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    assert config in client.topics()
    assert TROCKENLAUF_ZUSATZ.strip() not in dict(client.nachrichten)[config]


@pytest.mark.anyio
async def test_ohne_wechsel_wird_nicht_erneut_angemeldet(session: Session) -> None:
    """Sonst ginge je Zone und Minute eine Discovery-Nachricht hinaus -- viel Verkehr
    fuer eine Aussage, die sich nicht geaendert hat."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "einmal-zone")
    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)

    zweiter = await _lauf(session, stand)
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" not in zweiter.topics()
    assert f"thermoctl/zonen/{zone.id}/zustand/sollwert" in zweiter.topics()


@pytest.mark.anyio
async def test_der_trockenlauf_meldet_nicht_ab(session: Session) -> None:
    """Abmelden und Neuanmelden bei jedem Umschalten liesse die Entitaet in Home
    Assistant kurz verschwinden -- Verlaufsdaten und Automatisierungen liefen dort ins
    Leere."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "bleibende-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)
    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)

    scharf_schalten(session, False, begruendung="", user_id=None)
    client = await _lauf(session, stand)

    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    assert (config, "") not in client.nachrichten
    assert zone.id in stand.angemeldet


@pytest.mark.anyio
async def test_nur_eine_geloeschte_zone_wird_abgemeldet(session: Session) -> None:
    """Der einzige Grund fuer eine Abmeldung. Ohne sie bliebe in Home Assistant ein
    Thermostat stehen, den niemand mehr bedient."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "verschwindende-zone")
    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)

    session.delete(zone)
    session.flush()
    client = await _lauf(session, stand)

    assert (f"homeassistant/climate/thermoctl_zone_{zone.id}/config", "") in client.nachrichten
    assert stand.angemeldet == set()


@pytest.mark.anyio
async def test_ein_fehlender_messwert_wird_nicht_als_leere_nutzlast_gesendet(
    session: Session,
) -> None:
    """In MQTT loescht eine leere Nutzlast eine behaltene Nachricht. 'Noch kein
    Messwert' ist etwas anderes als 'diesen Wert gibt es nicht mehr'."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "messwertlose-zone")

    client = await _lauf(session, Veroeffentlichungsstand())
    assert f"thermoctl/zonen/{zone.id}/zustand/ist_temperatur" not in client.topics()


@pytest.mark.anyio
async def test_sollwert_wird_mit_punkt_gesendet(session: Session) -> None:
    """MQTT ist keine Oberflaeche: Home Assistant erwartet eine Zahl, kein deutsches
    Komma."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "punktzone")
    client = await _lauf(session, Veroeffentlichungsstand())
    sollwert = dict(client.nachrichten)[f"thermoctl/zonen/{zone.id}/zustand/sollwert"]
    assert "," not in sollwert
    assert Decimal(sollwert) > 0
