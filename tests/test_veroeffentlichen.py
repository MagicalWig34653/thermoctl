"""Der Aufrufer, der den eigenen Zustand sendet und die Zonen bei Home Assistant anmeldet.

Die Nutzlasten selbst sind in `test_veroeffentlichung.py` geprueft. Hier geht es um die
Fragen daneben: **wann** gesendet wird, **wie** der Betriebszustand dabei sichtbar bleibt,
und wann abgemeldet wird.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import betriebsart, einstellungen_anlegen, quelle, zone_anlegen
from thermoctl.domain.steuerung import scharf_schalten
from thermoctl.services.veroeffentlichen import Veroeffentlichungsstand, zyklus

JETZT = datetime(2026, 8, 31, 7, 0)


class Mitschrift:
    """Ein Veroeffentlicher, der nur mitschreibt.

    Er sendet immer -- der Riegel des Trockenlaufs sitzt im echten Client und gilt allein
    Schaltbefehlen. Hier wird geprueft, *was* der Dienst senden will.
    """

    def __init__(self) -> None:
        self.nachrichten: list[tuple[str, str]] = []
        self.geschaltet: list[str] = []
        self.fluechtig: list[str] = []

    async def veroeffentlichen(
        self, topic: str, nutzlast: str, *, schaltet: bool, behalten: bool = False
    ) -> bool:
        self.nachrichten.append((topic, nutzlast))
        if schaltet:
            self.geschaltet.append(topic)
        if not behalten:
            self.fluechtig.append(topic)
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
async def test_der_trockenlauf_steht_nicht_mehr_im_namen(session: Session) -> None:
    """Er stand dort, weil es sichtbar war -- und war genau deshalb falsch.

    Home Assistant leitet die Entitaetskennung beim ersten Auftauchen aus dem Namen ab.
    Eine Zone, die zuerst im Trockenlauf erschien, hiess danach fuer immer
    `climate.thermoctl_zone_1_trockenlauf`, auch scharf geschaltet.
    """
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "namenszone")

    client = await _lauf(session, Veroeffentlichungsstand())
    anmeldung = dict(client.nachrichten)[
        f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    ]
    assert "rockenlauf" not in anmeldung


@pytest.mark.anyio
async def test_die_kennung_bleibt_ueber_das_scharfschalten_hinweg_gleich(
    session: Session,
) -> None:
    """Die Gegenprobe zur Zeile darueber, und die eigentliche Zusage.

    Verglichen wird die ganze Anmeldung, nicht nur der Name: Waere irgendetwas darin vom
    Betriebszustand abhaengig, faende es dieser Test -- und die Entitaet in Home
    Assistant haette sich mit dem Scharfschalten veraendert.
    """
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone = zone_anlegen(session, "kennungszone")
    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"

    trocken = dict((await _lauf(session, Veroeffentlichungsstand())).nachrichten)[config]
    scharf_schalten(session, True, begruendung="Test", user_id=None)
    geschaerft = dict((await _lauf(session, Veroeffentlichungsstand())).nachrichten)[config]

    assert trocken == geschaerft
    assert '"unique_id":"thermoctl_zone_' in trocken
    assert '"object_id":"thermoctl_zone_' in trocken


@pytest.mark.anyio
async def test_der_betriebszustand_steht_in_einer_eigenen_entitaet(session: Session) -> None:
    """Er muss sichtbar bleiben -- nur eben nicht im Namen einer anderen Entitaet."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    zone_anlegen(session, "zustandszone")

    trocken = dict((await _lauf(session, Veroeffentlichungsstand())).nachrichten)
    assert "homeassistant/binary_sensor/thermoctl_scharf/config" in trocken
    assert trocken["thermoctl/zustand/scharf"] == "false"

    scharf_schalten(session, True, begruendung="Test", user_id=None)
    geschaerft = dict((await _lauf(session, Veroeffentlichungsstand())).nachrichten)
    assert geschaerft["thermoctl/zustand/scharf"] == "true"


@pytest.mark.anyio
async def test_anmeldungen_und_zustaende_gehen_behalten_hinaus(session: Session) -> None:
    """Ohne retain steht in Home Assistant nach jedem Neustart eine leere Karte.

    Bis der Dienst das naechste Mal sendet, vergeht ein ganzer Regelzyklus -- und beim
    Umschalten eines Modus sah es aus, als sei der Befehl verschluckt worden.
    """
    einstellungen_anlegen(session)
    zone_anlegen(session, "behaltene-zone")
    client = await _lauf(session, Veroeffentlichungsstand())
    assert client.nachrichten
    assert client.fluechtig == []


@pytest.mark.anyio
async def test_je_zone_stehen_boost_zeitpunkte_modi_und_parameter_bereit(
    session: Session,
) -> None:
    """Was in Home Assistant je Zone bedienbar sein soll, muss auch angemeldet werden."""
    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "vollausstattung")
    client = await _lauf(session, Veroeffentlichungsstand())
    topics = set(client.topics())
    kennung = f"thermoctl_zone_{zone.id}"

    assert f"homeassistant/button/{kennung}_boost/config" in topics
    assert f"homeassistant/sensor/{kennung}_letzte_schaltung/config" in topics
    assert f"homeassistant/sensor/{kennung}_naechste_schaltung/config" in topics
    # Je Regelparameter ein Drehregler, und der Zustand dazu.
    for name in ("hysteresis_k", "min_on_seconds", "temperature_offset_k"):
        assert f"homeassistant/number/{kennung}_parameter_{name}/config" in topics
        assert f"thermoctl/zonen/{zone.id}/zustand/parameter/{name}" in topics
    # Je Modus ein Drehregler. Welche Modi es gibt, entscheidet die Anlage.
    modi = [t for t in topics if t.startswith(f"homeassistant/number/{kennung}_modus_")]
    assert modi, "kein Modus angemeldet"


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

    # Jede Entitaet der Zone, nicht nur der Thermostat: Sonst blieben Boost-Knopf und
    # Drehregler einer geloeschten Zone in Home Assistant stehen.
    abgemeldet = {topic for topic, nutzlast in client.nachrichten if nutzlast == ""}
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in abgemeldet
    assert f"homeassistant/button/thermoctl_zone_{zone.id}_boost/config" in abgemeldet
    assert stand.angemeldet == {}


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


@pytest.mark.anyio
async def test_ein_befehl_wird_sofort_beantwortet(session: Session) -> None:
    """Die Climate-Karte in Home Assistant ist nicht optimistisch.

    Sie wartet auf den Zustand und zeigt bis dahin den alten. Kam der erst im naechsten
    Regelzyklus, sprang die eben gewaehlte Betriebsart fuer eine Minute zurueck -- und
    fuer den Benutzer sah es aus, als lasse sie sich nicht umstellen.
    """
    from types import SimpleNamespace

    from thermoctl.app import _mqtt_nachricht_verarbeiten
    from thermoctl.config import Settings

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "antwortzone")
    betriebsart(session, "off")
    client = Mitschrift()

    class _Sitzungen:
        """Gibt immer dieselbe Sitzung -- `session_scope` darf sie nicht schliessen.

        Die Fixture haelt die Transaktion offen und raeumt hinterher selbst auf; ein
        `close()` mittendrin loeste jedes bereits geladene Objekt von ihr.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(veroeffentlicher=client, session_factory=_Sitzungen())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _mqtt_nachricht_verarbeiten(
        app, umgebung, f"thermoctl/zonen/{zone.id}/befehl/betriebsart", b"off"
    )

    # Der neue Wert, nicht der alte: Wer nur den Fremdschluessel umschreibt, laesst ein
    # bereits geladenes `zone.operating_mode` stehen -- und meldete hier "auto".
    assert (f"thermoctl/zonen/{zone.id}/zustand/betriebsart", "off") in client.nachrichten
    assert zone.operating_mode.code == "off"


@pytest.mark.anyio
async def test_ein_verworfener_befehl_loest_keine_meldung_aus(session: Session) -> None:
    """Gegenprobe: Sonst antwortete der Dienst auch auf Unsinn und auf fremde Topics."""
    from types import SimpleNamespace

    from thermoctl.app import _mqtt_nachricht_verarbeiten
    from thermoctl.config import Settings

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "stillezone")
    client = Mitschrift()

    class _Sitzungen:
        """Gibt immer dieselbe Sitzung -- `session_scope` darf sie nicht schliessen.

        Die Fixture haelt die Transaktion offen und raeumt hinterher selbst auf; ein
        `close()` mittendrin loeste jedes bereits geladene Objekt von ihr.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(veroeffentlicher=client, session_factory=_Sitzungen())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _mqtt_nachricht_verarbeiten(
        app, umgebung, f"thermoctl/zonen/{zone.id}/befehl/betriebsart", b"gemuetlich"
    )

    assert client.nachrichten == []


@pytest.mark.anyio
async def test_zustand_schaltzeitpunkte_und_sensorlage_gehen_mit(session: Session) -> None:
    """Was Home Assistant je Zone anzeigen soll, muss auch gesendet werden.

    „Letzte Schaltung" ist dabei nicht der letzte Regelzyklus, sondern der letzte
    *Wechsel*: Sonst stuende dort immer „vor einer Minute".
    """
    from tests.hilfen import sensorstatus, zonenzustand_anlegen
    from thermoctl.db.models.zustand import ShadowDecision

    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "zustandsreiche-zone")
    zustand = zonenzustand_anlegen(session, zone)
    zustand.temperature_c = Decimal("20.5")
    zustand.sensor_status_id = sensorstatus(session, "veraltet").id
    session.add_all(
        [
            ShadowDecision(
                decided_at=datetime(2026, 8, 31, 5, 0), zone_id=zone.id,
                setpoint_reason="Plan", would_heat=True, previous_would_heat=False,
                outcome_code="wuerde_heizen", reason="kalt",
            ),
            ShadowDecision(
                decided_at=datetime(2026, 8, 31, 6, 30), zone_id=zone.id,
                setpoint_reason="Plan", would_heat=True, previous_would_heat=True,
                outcome_code="wuerde_heizen", reason="weiter",
            ),
        ]
    )
    session.flush()

    nachrichten = dict((await _lauf(session, Veroeffentlichungsstand())).nachrichten)
    basis = f"thermoctl/zonen/{zone.id}/zustand"

    assert nachrichten[f"{basis}/ist_temperatur"] == "20.5"
    assert nachrichten[f"{basis}/sensorzustand"] == "veraltet"
    assert nachrichten[f"{basis}/wuerde_heizen"] == "true"
    # 05:00, nicht 06:30: Um 06:30 wurde nur bestaetigt, was schon galt.
    # Mit Zeitzone, weil `device_class: timestamp` sie verlangt.
    assert nachrichten[f"{basis}/letzte_schaltung"] == "2026-08-31T05:00:00+00:00"
