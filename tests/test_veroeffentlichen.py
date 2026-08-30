"""Der Aufrufer, der den eigenen Zustand sendet und die Zonen bei Home Assistant anmeldet.

Die Nutzlasten selbst sind in `test_veroeffentlichung.py` geprueft. Hier geht es um die
Frage, die daneben steht und schwerer wiegt: **wann** gesendet wird.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
from thermoctl.db.models.operations import Setting
from thermoctl.domain.steuerung import scharf_schalten
from thermoctl.services.veroeffentlichen import Veroeffentlichungsstand, zyklus

JETZT = datetime(2026, 8, 31, 7, 0)


class Mitschrift:
    """Ein Veroeffentlicher, der nur mitschreibt. Er ist absichtlich **scharf**: Der
    Trockenlauf wird hier ueber die Datenbank geprueft, nicht ueber den Client -- sonst
    liefe der Test gegen den zweiten Riegel und sagte ueber den ersten nichts."""

    def __init__(self) -> None:
        self.nachrichten: list[tuple[str, str]] = []

    async def veroeffentlichen(self, topic: str, nutzlast: str, *, scharf: bool) -> bool:
        self.nachrichten.append((topic, nutzlast))
        return True

    def topics(self) -> list[str]:
        return [t for t, _ in self.nachrichten]


async def _lauf(session: Session, stand: Veroeffentlichungsstand) -> Mitschrift:
    client = Mitschrift()
    await zyklus(session, client, stand, "thermoctl", JETZT)
    return client


@pytest.mark.anyio
async def test_im_trockenlauf_wird_nichts_gesendet(session: Session) -> None:
    """Der Kern der Sache. Eine Zone, die sich in Home Assistant als Thermostat
    anmeldet, bekommt dort einen Regler und eine Anzeige 'heizt' -- beides waere im
    Trockenlauf gelogen, und zwar in einer fremden Oberflaeche."""
    einstellungen_anlegen(session)
    zone_anlegen(session, "stille-zone")
    client = await _lauf(session, Veroeffentlichungsstand())
    assert client.nachrichten == []


@pytest.mark.anyio
async def test_scharf_meldet_an_und_sendet_zustaende(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "laute-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)

    stand = Veroeffentlichungsstand()
    client = await _lauf(session, stand)

    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in client.topics()
    assert "thermoctl/verfuegbarkeit" in client.topics()
    assert f"thermoctl/zonen/{zone.id}/zustand/sollwert" in client.topics()
    assert stand.angemeldet == {zone.id}


@pytest.mark.anyio
async def test_zweiter_zyklus_meldet_nicht_erneut_an(session: Session) -> None:
    """Sonst ginge je Zone und Minute eine Discovery-Nachricht hinaus -- viel Verkehr
    fuer eine Aussage, die sich nicht geaendert hat."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "einmal-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)

    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)
    zweiter = await _lauf(session, stand)
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" not in zweiter.topics()
    assert f"thermoctl/zonen/{zone.id}/zustand/sollwert" in zweiter.topics()


@pytest.mark.anyio
async def test_zurueck_in_den_trockenlauf_meldet_ab(session: Session) -> None:
    """Ohne das bliebe in Home Assistant ein Thermostat stehen, den niemand mehr
    bedient -- er zeigte den letzten bekannten Wert fuer immer weiter."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "abmeldezone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)
    stand = Veroeffentlichungsstand()
    await _lauf(session, stand)

    scharf_schalten(session, False, begruendung="", user_id=None)
    client = await _lauf(session, stand)

    abmeldung = (f"homeassistant/climate/thermoctl_zone_{zone.id}/config", "")
    assert abmeldung in client.nachrichten
    assert ("thermoctl/verfuegbarkeit", "offline") in client.nachrichten
    assert stand.angemeldet == set()


@pytest.mark.anyio
async def test_geloeschte_zone_wird_abgemeldet(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "verschwindende-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)
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
    quelle(session, "web")
    zone = zone_anlegen(session, "messwertlose-zone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)

    client = await _lauf(session, Veroeffentlichungsstand())
    ist = f"thermoctl/zonen/{zone.id}/zustand/ist_temperatur"
    assert ist not in client.topics()
    assert all(nutzlast != "" or topic.startswith("homeassistant/")
               for topic, nutzlast in client.nachrichten)


@pytest.mark.anyio
async def test_der_riegel_haengt_an_der_datenbank_nicht_am_aufrufer(
    session: Session,
) -> None:
    """Gegenprobe: Derselbe Client, dieselbe Zone -- allein `control_armed` entscheidet."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone_anlegen(session, "riegelzone")
    stand = Veroeffentlichungsstand()

    assert (await _lauf(session, stand)).nachrichten == []
    scharf_schalten(session, True, begruendung="jetzt", user_id=None)
    assert (await _lauf(session, stand)).nachrichten != []
    assert session.get(Setting, 1).control_armed is True


@pytest.mark.anyio
async def test_sollwert_wird_mit_punkt_gesendet(session: Session) -> None:
    """MQTT ist keine Oberflaeche: Home Assistant erwartet eine Zahl, kein deutsches
    Komma."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "punktzone")
    scharf_schalten(session, True, begruendung="Test", user_id=None)
    client = await _lauf(session, Veroeffentlichungsstand())
    werte = dict(client.nachrichten)
    sollwert = werte[f"thermoctl/zonen/{zone.id}/zustand/sollwert"]
    assert "," not in sollwert
    assert Decimal(sollwert) > 0
