"""Der Aufrufer, der den eigenen Zustand sendet und die Zonen bei Home Assistant anmeldet.

Die Nutzlasten selbst sind in `test_veroeffentlichung.py` geprueft. Hier geht es um die
Fragen daneben: **wann** gesendet wird, **wie** der Betriebszustand dabei sichtbar bleibt,
und wann abgemeldet wird.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, operating_mode, source
from thermoctl.domain.control import arm
from thermoctl.services.publishing import PublicationState, cycle

NOW = datetime(2026, 8, 31, 7, 0)


class Mitschrift:
    """Ein Veroeffentlicher, der nur mitschreibt.

    Er sendet immer -- der Riegel des Trockenlaufs sitzt im echten Client und gilt allein
    Schaltbefehlen. Hier wird geprueft, *was* der Dienst senden will.
    """

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.switched: list[str] = []
        self.fluechtig: list[str] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, behalten: bool = False
    ) -> bool:
        self.messages.append((topic, payload))
        if switches:
            self.switched.append(topic)
        if not behalten:
            self.fluechtig.append(topic)
        return True

    def topics(self) -> list[str]:
        return [t for t, _ in self.messages]


async def _run(session: Session, state: PublicationState) -> Mitschrift:
    client = Mitschrift()
    await cycle(session, client, state, "thermoctl", NOW)
    return client


@pytest.mark.anyio
async def test_im_trockenlauf_wird_veroeffentlicht(session: Session) -> None:
    """Eine Zustandsmeldung bewegt nichts. Eine Anbindung, die man erst nach dem
    Scharfschalten ausprobieren kann, laesst sich genau dann nicht mehr gefahrlos
    pruefen, wenn ein Fehler noch folgenlos waere."""
    create_settings(session)
    zone = create_zone(session, "probezone")

    client = await _run(session, PublicationState())

    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in client.topics()
    assert f"thermoctl/zones/{zone.id}/state/setpoint" in client.topics()


@pytest.mark.anyio
async def test_keine_dieser_nachrichten_schaltet(session: Session) -> None:
    """Gegenprobe zur Zeile oben: Veroeffentlicht wird, geschaltet nicht. Ohne sie waere
    der Test darueber auch von einer Fassung erfuellt, die im Trockenlauf Ventile
    bewegt."""
    create_settings(session)
    create_zone(session, "harmlos")
    client = await _run(session, PublicationState())
    assert client.switched == []


@pytest.mark.anyio
async def test_der_trockenlauf_steht_nicht_mehr_im_namen(session: Session) -> None:
    """Er stand dort, weil es sichtbar war -- und war genau deshalb falsch.

    Home Assistant leitet die Entitaetskennung beim ersten Auftauchen aus dem Namen ab.
    Eine Zone, die zuerst im Trockenlauf erschien, hiess danach fuer immer
    `climate.thermoctl_zone_1_trockenlauf`, auch scharf geschaltet.
    """
    create_settings(session)
    zone = create_zone(session, "namenszone")

    client = await _run(session, PublicationState())
    login = dict(client.messages)[
        f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    ]
    assert "rockenlauf" not in login


@pytest.mark.anyio
async def test_die_kennung_bleibt_ueber_das_scharfschalten_hinweg_gleich(
    session: Session,
) -> None:
    """Die Gegenprobe zur Zeile darueber, und die eigentliche Zusage.

    Verglichen wird die ganze Anmeldung, nicht nur der Name: Waere irgendetwas darin vom
    Betriebszustand abhaengig, faende es dieser Test -- und die Entitaet in Home
    Assistant haette sich mit dem Scharfschalten veraendert.
    """
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "kennungszone")
    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"

    trocken = dict((await _run(session, PublicationState())).messages)[config]
    arm(session, True, reason="Test", user_id=None)
    geschaerft = dict((await _run(session, PublicationState())).messages)[config]

    assert trocken == geschaerft
    assert '"unique_id":"thermoctl_zone_' in trocken
    assert '"object_id":"thermoctl_zone_' in trocken


@pytest.mark.anyio
async def test_der_betriebszustand_steht_in_einer_eigenen_entitaet(session: Session) -> None:
    """Er muss sichtbar bleiben -- nur eben nicht im Namen einer anderen Entitaet."""
    create_settings(session)
    source(session, "web")
    create_zone(session, "zustandszone")

    trocken = dict((await _run(session, PublicationState())).messages)
    assert "homeassistant/binary_sensor/thermoctl_scharf/config" in trocken
    assert trocken["thermoctl/state/armed"] == "false"

    arm(session, True, reason="Test", user_id=None)
    geschaerft = dict((await _run(session, PublicationState())).messages)
    assert geschaerft["thermoctl/state/armed"] == "true"


@pytest.mark.anyio
async def test_anmeldungen_und_zustaende_gehen_behalten_hinaus(session: Session) -> None:
    """Ohne retain steht in Home Assistant nach jedem Neustart eine leere Karte.

    Bis der Dienst das naechste Mal sendet, vergeht ein ganzer Regelzyklus -- und beim
    Umschalten eines Modus sah es aus, als sei der Befehl verschluckt worden.
    """
    create_settings(session)
    create_zone(session, "behaltene-zone")
    client = await _run(session, PublicationState())
    assert client.messages
    assert client.fluechtig == []


@pytest.mark.anyio
async def test_je_zone_stehen_boost_zeitpunkte_modi_und_parameter_bereit(
    session: Session,
) -> None:
    """Was in Home Assistant je Zone bedienbar sein soll, muss auch angemeldet werden."""
    create_settings(session)
    zone = create_zone(session, "vollausstattung")
    client = await _run(session, PublicationState())
    topics = set(client.topics())
    identifier = f"thermoctl_zone_{zone.id}"

    assert f"homeassistant/button/{identifier}_boost/config" in topics
    assert f"homeassistant/sensor/{identifier}_last_switch/config" in topics
    assert f"homeassistant/sensor/{identifier}_next_switch/config" in topics
    # Je Regelparameter ein Drehregler, und der Zustand dazu.
    for name in ("hysteresis_k", "min_on_seconds", "temperature_offset_k"):
        assert f"homeassistant/number/{identifier}_parameter_{name}/config" in topics
        assert f"thermoctl/zones/{zone.id}/state/parameter/{name}" in topics
    # Je Modus ein Drehregler. Welche Modi es gibt, entscheidet die Anlage.
    modes = [t for t in topics if t.startswith(f"homeassistant/number/{identifier}_modus_")]
    assert modes, "kein Modus angemeldet"


@pytest.mark.anyio
async def test_ohne_wechsel_wird_nicht_erneut_angemeldet(session: Session) -> None:
    """Sonst ginge je Zone und Minute eine Discovery-Nachricht hinaus -- viel Verkehr
    fuer eine Aussage, die sich nicht geaendert hat."""
    create_settings(session)
    zone = create_zone(session, "einmal-zone")
    state = PublicationState()
    await _run(session, state)

    zweiter = await _run(session, state)
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" not in zweiter.topics()
    assert f"thermoctl/zones/{zone.id}/state/setpoint" in zweiter.topics()


@pytest.mark.anyio
async def test_der_trockenlauf_meldet_nicht_ab(session: Session) -> None:
    """Abmelden und Neuanmelden bei jedem Umschalten liesse die Entitaet in Home
    Assistant kurz verschwinden -- Verlaufsdaten und Automatisierungen liefen dort ins
    Leere."""
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "bleibende-zone")
    arm(session, True, reason="Test", user_id=None)
    state = PublicationState()
    await _run(session, state)

    arm(session, False, reason="", user_id=None)
    client = await _run(session, state)

    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    assert (config, "") not in client.messages
    assert zone.id in state.angemeldet


@pytest.mark.anyio
async def test_nur_eine_geloeschte_zone_wird_abgemeldet(session: Session) -> None:
    """Der einzige Grund fuer eine Abmeldung. Ohne sie bliebe in Home Assistant ein
    Thermostat stehen, den niemand mehr bedient."""
    create_settings(session)
    zone = create_zone(session, "verschwindende-zone")
    state = PublicationState()
    await _run(session, state)

    session.delete(zone)
    session.flush()
    client = await _run(session, state)

    # Jede Entitaet der Zone, nicht nur der Thermostat: Sonst blieben Boost-Knopf und
    # Drehregler einer geloeschten Zone in Home Assistant stehen.
    abgemeldet = {topic for topic, payload in client.messages if payload == ""}
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in abgemeldet
    assert f"homeassistant/button/thermoctl_zone_{zone.id}_boost/config" in abgemeldet
    assert state.angemeldet == {}


@pytest.mark.anyio
async def test_ein_fehlender_messwert_wird_nicht_als_leere_nutzlast_gesendet(
    session: Session,
) -> None:
    """In MQTT loescht eine leere Nutzlast eine behaltene Nachricht. 'Noch kein
    Messwert' ist etwas anderes als 'diesen Wert gibt es nicht mehr'."""
    create_settings(session)
    zone = create_zone(session, "messwertlose-zone")

    client = await _run(session, PublicationState())
    assert f"thermoctl/zones/{zone.id}/state/current_temperature" not in client.topics()


@pytest.mark.anyio
async def test_sollwert_wird_mit_punkt_gesendet(session: Session) -> None:
    """MQTT ist keine Oberflaeche: Home Assistant erwartet eine Zahl, kein deutsches
    Komma."""
    create_settings(session)
    zone = create_zone(session, "punktzone")
    client = await _run(session, PublicationState())
    setpoint = dict(client.messages)[f"thermoctl/zones/{zone.id}/state/setpoint"]
    assert "," not in setpoint
    assert Decimal(setpoint) > 0


@pytest.mark.anyio
async def test_ein_befehl_wird_sofort_beantwortet(session: Session) -> None:
    """Die Climate-Karte in Home Assistant ist nicht optimistisch.

    Sie wartet auf den Zustand und zeigt bis dahin den alten. Kam der erst im naechsten
    Regelzyklus, sprang die eben gewaehlte Betriebsart fuer eine Minute zurueck -- und
    fuer den Benutzer sah es aus, als lasse sie sich nicht umstellen.
    """
    from types import SimpleNamespace

    from thermoctl.app import _process_mqtt_message
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "antwortzone")
    operating_mode(session, "off")
    client = Mitschrift()

    class _Sessions:
        """Gibt immer dieselbe Sitzung -- `session_scope` darf sie nicht schliessen.

        Die Fixture haelt die Transaktion offen und raeumt hinterher selbst auf; ein
        `close()` mittendrin loeste jedes bereits geladene Objekt von ihr.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(publisher=client, session_factory=_Sessions())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _process_mqtt_message(
        app, umgebung, f"thermoctl/zones/{zone.id}/command/operating_mode", b"off"
    )

    # Der neue Wert, nicht der alte: Wer nur den Fremdschluessel umschreibt, laesst ein
    # bereits geladenes `zone.operating_mode` stehen -- und meldete hier "auto".
    assert (f"thermoctl/zones/{zone.id}/state/operating_mode", "off") in client.messages
    assert zone.operating_mode.code == "off"


@pytest.mark.anyio
async def test_ein_verworfener_befehl_loest_keine_meldung_aus(session: Session) -> None:
    """Gegenprobe: Sonst antwortete der Dienst auch auf Unsinn und auf fremde Topics."""
    from types import SimpleNamespace

    from thermoctl.app import _process_mqtt_message
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "stillezone")
    client = Mitschrift()

    class _Sessions:
        """Gibt immer dieselbe Sitzung -- `session_scope` darf sie nicht schliessen.

        Die Fixture haelt die Transaktion offen und raeumt hinterher selbst auf; ein
        `close()` mittendrin loeste jedes bereits geladene Objekt von ihr.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(publisher=client, session_factory=_Sessions())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _process_mqtt_message(
        app, umgebung, f"thermoctl/zones/{zone.id}/command/operating_mode", b"gemuetlich"
    )

    assert client.messages == []


@pytest.mark.anyio
async def test_zustand_schaltzeitpunkte_und_sensorlage_gehen_mit(session: Session) -> None:
    """Was Home Assistant je Zone anzeigen soll, muss auch gesendet werden.

    „Letzte Schaltung" ist dabei nicht der letzte Regelzyklus, sondern der letzte
    *Wechsel*: Sonst stuende dort immer „vor einer Minute".
    """
    from tests.helpers import create_zone_state, sensorstatus
    from thermoctl.db.models.state import ShadowDecision

    create_settings(session)
    zone = create_zone(session, "zustandsreiche-zone")
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("20.5")
    state.sensor_status_id = sensorstatus(session, "veraltet").id
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

    messages = dict((await _run(session, PublicationState())).messages)
    basis = f"thermoctl/zones/{zone.id}/state"

    assert messages[f"{basis}/current_temperature"] == "20.5"
    assert messages[f"{basis}/sensor_state"] == "veraltet"
    assert messages[f"{basis}/would_heat"] == "true"
    # 05:00, nicht 06:30: Um 06:30 wurde nur bestaetigt, was schon galt.
    # Mit Zeitzone, weil `device_class: timestamp` sie verlangt.
    assert messages[f"{basis}/last_switch"] == "2026-08-31T05:00:00+00:00"
