"""Tests fuer den Schattenlauf (`thermoctl.services.schattenlauf`).

Diese Aufgabe wird an ihren Tests gemessen (Auftragstext Aufgabe 9): dass genau eine
Zeile je Zone und Zyklus entsteht, dass die Mindestschaltdauer ueber `seit_s` wirklich
etwas bewirkt, dass eine kaputte Zone die uebrigen nicht aufhaelt — und vor allem, dass
in dieser Phase nirgends veroeffentlicht wird.
"""

import asyncio
import json
import types
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from tests.helpers import (
    create_device,
    create_settings,
    create_zone,
    create_zone_state,
    integration,
    rolle,
    sensorstatus,
    source,
)
from thermoctl import app as app_modul
from thermoctl.app import create_app
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.integrations.mqtt import client as client_modul
from thermoctl.integrations.mqtt.client import MqttClient
from thermoctl.services import shadow_run

NOW = datetime(2026, 8, 29, 8, 0)
DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"


def _own_database(tmp_path: Path, name: str) -> tuple[Engine, sessionmaker[Session]]:
    """Eine eigene, benannte SQLite-Datei je Test.

    `_schattenschleife` und `_mqtt_nachricht_verarbeiten` oeffnen ihre Sitzungen selbst
    ueber `app.state.session_factory` -- die transaktionsisolierte Fixture ``session``
    dieser Datei passt hier nicht, weil diese Funktionen commiten muessen, um etwas zu
    bewirken, das eine zweite, unabhaengig geoeffnete Sitzung wieder sehen kann.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db", future=True)
    Base.metadata.create_all(engine)
    return engine, session_factory(engine)


def _zone_with_state(
    session: Session,
    name: str,
    *,
    ist_c: Decimal | None,
    now: datetime = NOW,
    status: str = "ok",
) -> Zone:
    zone = create_zone(session, name)
    state = ZoneState(
        zone_id=zone.id,
        temperature_c=ist_c,
        measured_at=now,
        sensor_status_id=sensorstatus(session, status).id,
        updated_at=now,
    )
    session.add(state)
    session.flush()
    return zone


def test_ein_zyklus_frischer_messwert_schreibt_eine_zeile_mit_begruendung(
    session: Session,
) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    zone = _zone_with_state(session, "buero", ist_c=Decimal("10.0"))

    zeilen = shadow_run.cycle(session, NOW)

    assert len(zeilen) == 1
    zeile = zeilen[0]
    assert zeile.zone_id == zone.id
    assert zeile.would_heat is True
    assert zeile.outcome_code == "heizen"
    assert zeile.reason and "Ist" in zeile.reason
    assert zeile.previous_would_heat is None  # keine Vorgeschichte im ersten Zyklus
    assert session.query(ShadowDecision).count() == 1


def test_mehrere_zyklen_unveraenderte_lage_ergeben_unveraendert_ohne_zeilenflut(
    session: Session,
) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    # Frostschutz-Rueckfall ist 16.0 °C (kein Zeitplan hinterlegt); 16.0 °C liegt
    # innerhalb der Hysteresebandbreite von ±0.30 K und aendert deshalb nie etwas.
    zone = _zone_with_state(session, "flur", ist_c=Decimal("16.0"))
    # Mindestschaltdauer auf 0 gesetzt: sonst griffe Regel 5 (Mindestschaltdauer) schon
    # ab dem zweiten Zyklus und ergaebe 'gesperrt_mindestdauer' statt 'unveraendert' —
    # unabhaengig davon, dass sich an der Lage nichts geaendert hat. Das ist hier nicht
    # das Verhalten, das dieser Test belegen soll (das uebernimmt der naechste Test).
    zone.min_on_seconds = 0
    zone.min_off_seconds = 0
    session.flush()

    for i in range(3):
        result = shadow_run.cycle(session, NOW + timedelta(minutes=i))
        assert len(result) == 1  # genau eine Zeile je Zyklus, keine Luecke

    zeilen = list(
        session.scalars(
            select(ShadowDecision)
            .where(ShadowDecision.zone_id == zone.id)
            .order_by(ShadowDecision.decided_at)
        )
    )
    assert len(zeilen) == 3  # keine Zeilenflut ueber die drei Zyklen hinweg
    assert [z.outcome_code for z in zeilen] == ["unveraendert"] * 3
    assert [z.would_heat for z in zeilen] == [False, False, False]


def test_seit_s_waechst_ueber_zyklen_und_faellt_bei_wechsel_zurueck(
    session: Session,
) -> None:
    """Die Mindestschaltdauer wirkt nur, wenn `seit_s` ueber Zyklen hinweg mitwaechst.

    Ohne die Herleitung aus der eigenen Entscheidungskette waere `seit_s` bei jedem
    Zyklus `None`, und Regel 5 (Mindestschaltdauer) griffe im Schattenbetrieb nie —
    ohne dass ein einzelner Test das je auffiele. Der Ablauf hier zwingt die Regel
    zweimal zum Entscheiden: einmal, waehrend die Mindestdauer noch nicht um ist
    (Sperre haelt), einmal danach (Sperre faellt).
    """
    create_settings(session, hysteresis=Decimal("0.50"))
    zone = create_zone(session, "keller")
    zone.min_on_seconds = 100
    zone.min_off_seconds = 5
    session.flush()

    def _state(ist_c: Decimal, now: datetime) -> None:
        bisherig = session.get(ZoneState, zone.id)
        if bisherig is not None:
            session.delete(bisherig)
            session.flush()
        session.add(
            ZoneState(
                zone_id=zone.id,
                temperature_c=ist_c,
                measured_at=now,
                sensor_status_id=sensorstatus(session, "ok").id,
                updated_at=now,
            )
        )
        session.flush()

    # Zyklus 1 (t=0s): weit ueber dem Sollwert (Frostschutz-Rueckfall 16.0 °C) —
    # keine Vorgeschichte, bleibt aus.
    _state(Decimal("20.0"), NOW)
    z1 = shadow_run.cycle(session, NOW)[0]
    assert z1.would_heat is False
    assert z1.outcome_code == "unveraendert"

    # Zyklus 2 (t=+10s): weit unter dem Sollwert. seit_s=10s reicht fuer
    # min_off_seconds=5, die Hysterese schaltet ein.
    moment_two = NOW + timedelta(seconds=10)
    _state(Decimal("5.0"), moment_two)
    z2 = shadow_run.cycle(session, moment_two)[0]
    assert z2.would_heat is True
    assert z2.outcome_code == "heizen"
    assert z2.previous_would_heat is False

    # Zyklus 3 (t=+20s): wieder ueber dem Sollwert, aber die Heizphase begann erst vor
    # 10s — bei min_on_seconds=100 haelt die Sperre, ungeachtet der Hysterese.
    moment_three = NOW + timedelta(seconds=20)
    _state(Decimal("20.0"), moment_three)
    z3 = shadow_run.cycle(session, moment_three)[0]
    assert z3.would_heat is True
    assert z3.outcome_code == "gesperrt_mindestdauer"
    assert z3.previous_would_heat is True

    # Zyklus 4 (t=+130s): dieselbe Lage wie eben, aber die Heizphase (Beginn bei
    # Zyklus 2) laeuft jetzt seit 120s — laenger als min_on_seconds=100. Die Sperre
    # faellt, die Hysterese schaltet ab. Das waere unmoeglich, wenn `seit_s` nicht
    # ueber alle drei vorangegangenen Zyklen hinweg mitgewachsen waere.
    moment_four = NOW + timedelta(seconds=130)
    _state(Decimal("20.0"), moment_four)
    z4 = shadow_run.cycle(session, moment_four)[0]
    assert z4.would_heat is False
    assert z4.outcome_code == "aus"
    assert z4.previous_would_heat is True

    # Zyklus 5 (t=+131s): sofort nach dem Wechsel wieder unter dem Sollwert. seit_s
    # faellt beim Wechsel auf 1s zurueck — zu kurz fuer min_off_seconds=5, die Sperre
    # haelt erneut, obwohl dieselbe Zone Sekunden zuvor noch ungesperrt war.
    moment_five = NOW + timedelta(seconds=131)
    _state(Decimal("5.0"), moment_five)
    z5 = shadow_run.cycle(session, moment_five)[0]
    assert z5.would_heat is False
    assert z5.outcome_code == "gesperrt_mindestdauer"


def test_zone_ohne_messquelle_bekommt_keine_quelle_zeile(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "abstellraum")  # keine ZoneState-Zeile ueberhaupt

    zeilen = shadow_run.cycle(session, NOW)

    assert len(zeilen) == 1
    assert zeilen[0].zone_id == zone.id
    assert zeilen[0].outcome_code == "keine_quelle"
    assert zeilen[0].would_heat is False
    assert zeilen[0].temperature_c is None


def test_zone_ohne_fensterkontakt_heizt_trotz_unbekanntem_fensterzustand(
    session: Session,
) -> None:
    create_settings(session)
    zone = _zone_with_state(session, "ohne-fensterkontakt", ist_c=Decimal("5.0"))
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is None

    zeile = shadow_run.cycle(session, NOW)[0]

    assert zeile.would_heat is True
    assert zeile.outcome_code == "heizen"


def test_fensterschliessung_startet_wachsende_wiederanlaufverzoegerung(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.default_window_resume_delay_seconds = 120
    zone = _zone_with_state(session, "fensterpause", ist_c=Decimal("5.0"))
    zone.min_off_seconds = 0
    contact = DeviceCapability(code="contact", label="Kontakt")
    session.add(contact)
    session.flush()
    device_name = json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"][0]
    device = create_device(session, device_name)
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=rolle(session, "window_contact").id,
        )
    )
    for value, moment in (
        ("false", NOW - timedelta(seconds=30)),
        ("true", NOW - timedelta(seconds=20)),
    ):
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=contact.id,
                value_text=value,
                measured_at=moment,
                received_at=moment,
            )
        )
    state = session.get(ZoneState, zone.id)
    assert state is not None
    state.window_open = False
    session.flush()

    first = shadow_run.cycle(session, NOW)[0]
    zweite = shadow_run.cycle(session, NOW + timedelta(seconds=30))[0]

    assert first.would_heat is False
    assert first.outcome_code == "aus"
    assert "Fenster seit 20s zu" in first.reason
    assert "Fenster seit 50s zu" in zweite.reason


def test_scheiternde_zone_haelt_uebrige_nicht_auf(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    gesund = _zone_with_state(session, "gesund", ist_c=Decimal("10.0"))
    kaputt = _zone_with_state(session, "kaputt", ist_c=Decimal("10.0"))
    assert kaputt.id > gesund.id  # Reihenfolge nach id, wie `zyklus()` sie durchgeht

    original = shadow_run.control_parameters

    def _manchmal_kaputt(session: Session, zone: Zone) -> object:
        if zone.id == kaputt.id:
            raise RuntimeError("Simulierter Fehler in einer Zone")
        return original(session, zone)

    monkeypatch.setattr(shadow_run, "control_parameters", _manchmal_kaputt)

    zeilen = shadow_run.cycle(session, NOW)

    assert [z.zone_id for z in zeilen] == [gesund.id]
    # Der Fehlversuch der kaputten Zone hat keine (halbfertige) Zeile hinterlassen —
    # das Savepoint je Zone hat ihn vollstaendig zurueckgenommen.
    assert session.query(ShadowDecision).filter_by(zone_id=kaputt.id).count() == 0
    assert session.query(ShadowDecision).count() == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_keine_veroeffentlichung_trotz_heizender_entscheidung(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belegt die harte Grenze aus Abschnitt 1 der Spezifikation mit einem gefaelschten
    Client: Ein voller Zyklus kommt zu 'wuerde heizen' — trotzdem bleibt jeder Versuch,
    ueber den erreichbaren MQTT-Client zu veroeffentlichen, folgenlos.
    """

    class GefaelschterClient:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        async def publish(self, topic: str, payload: str) -> None:
            self.published.append((topic, payload))

    create_settings(session)
    _zone_with_state(session, "wohnzimmer", ist_c=Decimal("5.0"))
    zeilen = shadow_run.cycle(session, NOW)
    assert zeilen[0].would_heat is True  # die Ausgangslage ist scharf: es wuerde heizen

    monkeypatch.setattr(client_modul.aiomqtt, "Client", GefaelschterClient)
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="k" * 32,
        mqtt_enabled=True,
        mqtt_host="mqtt.example.invalid",
    )

    async def leerer_handler(_topic: str, _payload: bytes) -> None:
        return None

    client = MqttClient(settings, leerer_handler)
    gefaelscht = GefaelschterClient()
    client._client = gefaelscht  # type: ignore[assignment]

    result = await client.publishing(
        "zigbee2mqtt/Ventil/set", '{"state": "ON"}', switches=True
    )

    assert result is False
    assert gefaelscht.published == []


def test_hintergrundlauf_startet_nicht_ohne_mqtt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vorgabe aus dem Auftrag: ohne `mqtt_enabled` darf beim Start keine
    Hintergrundaufgabe entstehen — die Testsuite baut die Anwendung staendig.
    """
    # Eine benannte Datei statt 'sqlite://': Eine unbenannte In-Memory-Datenbank waere
    # je Verbindung eine eigene, leere Datenbank -- die Anwendung und dieser Test
    # saehen dann unterschiedliche, beide leere Datenbanken, ohne dass ein Fehler
    # aufgetreten waere, der das erklaeren wuerde.
    datenbank_url = f"sqlite:///{tmp_path}/hintergrundlauf.db"
    own_engine = create_engine(datenbank_url, future=True)
    Base.metadata.create_all(own_engine)
    settings = Settings(_env_file=None, database_url=datenbank_url, secret_key="h" * 32)
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()

    def _should_not_have_started(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "asyncio.create_task() wurde aufgerufen, obwohl mqtt_enabled=False ist"
        )

    monkeypatch.setattr(app_modul.asyncio, "create_task", _should_not_have_started)

    anwendung = create_app()
    anwendung.state.engine.dispose()
    anwendung.state.engine = own_engine
    anwendung.state.session_factory = lambda: Session(own_engine)

    with TestClient(anwendung):
        pass

    own_engine.dispose()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_schattenschleife_liest_intervall_und_schreibt_ergebnis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_schattenschleife` wartet den in `setting.shadow_interval_seconds` konfigurierten
    Abstand ab und schreibt danach wirklich ein Ergebnis -- in einer eigenen Sitzung, wie
    es der Auftrag verlangt (nicht in der Sitzung, die den Lifespan aufgebaut hat).
    """
    engine, fabrik = _own_database(tmp_path, "schleife")
    with fabrik() as http_session:
        create_settings(http_session)
        settings = http_session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 42
        sensorstatus(http_session, "keine_quelle")  # `zonenzustand_fortschreiben` braucht sie
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            # Simuliert den Abbruch beim Herunterfahren, waehrend die Schleife im
            # zweiten Durchlauf gerade wartet.
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadowschleife(fake_app)  # type: ignore[arg-type]

    assert waited[0] == 42  # aus setting.shadow_interval_seconds gelesen, nicht dem
    # eingebauten Vorgabewert

    with fabrik() as http_session:
        zeilen = list(http_session.scalars(select(ShadowDecision)))
    assert len(zeilen) == 1  # ein Durchlauf vor dem simulierten Abbruch
    assert zeilen[0].outcome_code == "keine_quelle"  # die Zone hat keine Temperaturquelle

    engine.dispose()


@pytest.mark.anyio
async def test_schattenschleife_uebersteht_fehlenden_intervall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne `setting`-Zeile (Einrichtung nicht abgeschlossen) darf die Schleife weder
    abstuerzen noch haengen bleiben -- sie faellt auf den eingebauten Vorgabewert zurueck
    und versucht es beim naechsten Mal erneut."""
    engine, fabrik = _own_database(tmp_path, "ohne-setting")
    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadowschleife(fake_app)  # type: ignore[arg-type]

    assert waited == [60, 60]  # eingebauter Vorgabewert, zweimal in Folge

    with fabrik() as http_session:
        assert http_session.query(ShadowDecision).count() == 0  # kein Absturz, aber auch keine
        # Zeile ohne eine Zone

    engine.dispose()


@pytest.mark.anyio
async def test_schattenschleife_uebersteht_ausnahme_im_zyklus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Fehler in einem Zyklus (hier: `zonenzustand_fortschreiben` scheitert) beendet
    die Schleife nicht -- protokollieren, weiter, der naechste Durchlauf kommt regulaer."""
    engine, fabrik = _own_database(tmp_path, "fehler-im-zyklus")
    with fabrik() as http_session:
        create_settings(http_session)
        sensorstatus(http_session, "keine_quelle")
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))

    aufrufe = 0
    original = app_modul.advance_zone_state

    def _first_attempt_fails(session: Session, now: datetime) -> None:
        nonlocal aufrufe
        aufrufe += 1
        if aufrufe == 1:
            raise ValueError("Simulierter Fehler im ersten Zyklus")
        original(session, now)

    monkeypatch.setattr(app_modul, "advance_zone_state", _first_attempt_fails)

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadowschleife(fake_app)  # type: ignore[arg-type]

    assert aufrufe == 2  # der erste Versuch scheiterte, der zweite lief regulaer weiter
    with fabrik() as http_session:
        assert http_session.query(ShadowDecision).count() == 1  # nur der zweite Zyklus schrieb

    engine.dispose()


@pytest.mark.anyio
async def test_schattenschleife_stoesst_aufbewahrung_einmal_taeglich_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`alte_messwerte_loeschen()` laeuft aus derselben Schleife, aber nur einmal je Tag
    -- nicht in jedem Zyklus (Auftragstext, Abschnitt 'Aufbewahrung anstossen')."""
    engine, fabrik = _own_database(tmp_path, "aufbewahrung")
    with fabrik() as http_session:
        create_settings(http_session)
        sensorstatus(http_session, "keine_quelle")
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))

    # Erster Aufruf setzt 'naechste_aufbewahrung' auf JETZT+1 Tag; der zweite (der
    # Messzeitpunkt des einzigen Zyklus vor dem Abbruch) liegt zwei Tage spaeter --
    # deutlich darueber, die Aufbewahrung muss also genau einmal auftauchen.
    moments = iter([NOW, NOW + timedelta(days=2)])
    monkeypatch.setattr(app_modul, "utcnow", lambda: next(moments))

    aufrufe: list[datetime] = []
    delete_original = app_modul.delete_old_measurements

    def _aufzeichnen(session: Session, now: datetime, **kwargs: object) -> int:
        aufrufe.append(now)
        return delete_original(session, now, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app_modul, "delete_old_measurements", _aufzeichnen)

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadowschleife(fake_app)  # type: ignore[arg-type]

    assert aufrufe == [NOW + timedelta(days=2)]

    engine.dispose()

    engine.dispose()


@pytest.mark.anyio
async def test_mqtt_nachricht_verarbeiten_schreibt_in_eigener_sitzung(
    tmp_path: Path,
) -> None:
    """Der MQTT-Handler des Lifespan verarbeitet eine Nachricht in einer frisch
    geoeffneten Sitzung -- unabhaengig von der, die den Dienst gestartet hat."""
    engine, fabrik = _own_database(tmp_path, "mqtt-handler")
    with fabrik() as http_session:
        integration(http_session, "zigbee2mqtt")
        http_session.add(DeviceCapability(code="temperature", label="Temperaturmessung"))
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="q" * 32,
        mqtt_base_topic="testbasis",
    )

    await app_modul._process_mqtt_message(
        fake_app,  # type: ignore[arg-type]
        settings,
        "testbasis/Sensor1",
        b'{"temperature": 21.5}',
    )

    with fabrik() as http_session:
        count = http_session.query(Measurement).count()
    assert count == 1

    engine.dispose()


class _HangingStream:
    """Ein MQTT-Nachrichtenstrom, der nie liefert -- wie eine echte, ruhige Verbindung.

    Bricht nur ab, wenn die Aufgabe von aussen abgebrochen wird. Genau dieser Fall soll
    beim Herunterfahren zuverlaessig funktionieren: ohne haengenden Prozess.
    """

    def __aiter__(self) -> _HangingStream:
        return self

    async def __anext__(self) -> object:
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")  # pragma: no cover


class _FalscherAiomqttClient:
    def __init__(self, **_argumente: object) -> None:
        self.messages = _HangingStream()

    async def __aenter__(self) -> _FalscherAiomqttClient:
        return self

    async def __aexit__(self, *_argumente: object) -> None:
        return None

    async def subscribe(self, _topic: str) -> None:
        return None

    async def publish(self, _topic: str, _payload: str) -> None:
        return None


def test_lifespan_startet_und_beendet_mqtt_und_schattenschleife_sauber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigentliche Beleg fuer 'sauber beenden': Beide Hintergrundaufgaben laufen
    (die MQTT-Verbindung haengt absichtlich in einer nie liefernden Nachrichtenschleife),
    und `with TestClient(...)` kehrt trotzdem zurueck -- kein haengender Prozess beim
    Herunterfahren."""
    engine, fabrik = _own_database(tmp_path, "lifespan-mqtt")
    with fabrik() as http_session:
        sensorstatus(http_session, "keine_quelle")
        http_session.commit()

    monkeypatch.setattr(client_modul.aiomqtt, "Client", _FalscherAiomqttClient)
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "l" * 32)
    monkeypatch.setenv("THERMOCTL_MQTT_ENABLED", "true")
    monkeypatch.setenv("THERMOCTL_MQTT_HOST", "mqtt.example.invalid")
    get_settings.cache_clear()

    anwendung = create_app()
    anwendung.state.engine.dispose()
    anwendung.state.engine = engine
    anwendung.state.session_factory = fabrik

    with TestClient(anwendung):
        pass  # der Austritt aus diesem Block muss zurueckkehren, sonst haengt der Test

    engine.dispose()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_brueckenzustand_meldet_nur_den_wechsel(tmp_path: Path) -> None:
    """Zigbee2MQTT schickt `bridge/state` bei jeder Verbindung erneut.

    Gemeldet wird der Wechsel, nicht der Zustand — sonst haette man nach einer Nacht mit
    wackeligem Funk hundert gleiche Meldungen und wuerde sie stummschalten.
    """
    engine, fabrik = _own_database(tmp_path, "bridge_notice")
    with fabrik() as http_session:
        integration(http_session, "zigbee2mqtt")
        source(http_session, "system")
        http_session.commit()

    gesendet: list[object] = []

    async def mitschreiben(_settings: object, notice: object) -> None:
        gesendet.append(notice)

    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(session_factory=fabrik, bridge_reachable=True)
    )
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key="q" * 32,
        mqtt_base_topic="testbasis",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_modul, "send", mitschreiben)
        # Zweimal 'offline' — nur der erste Aufruf ist ein Wechsel.
        for _ in range(2):
            await app_modul._process_mqtt_message(
                fake_app,  # type: ignore[arg-type]
                settings, "testbasis/bridge/state", b'{"state": "offline"}',
            )
        # Zurueck auf 'online': die Entwarnung.
        await app_modul._process_mqtt_message(
            fake_app,  # type: ignore[arg-type]
            settings, "testbasis/bridge/state", b'{"state": "online"}',
        )
        # Eine unlesbare Nutzlast aendert nichts und meldet nichts.
        await app_modul._process_mqtt_message(
            fake_app,  # type: ignore[arg-type]
            settings, "testbasis/bridge/state", b"{kaputt",
        )

    assert [m.schwere for m in gesendet] == ["stoerung", "entwarnung"]  # type: ignore[attr-defined]
    with fabrik() as http_session:
        entries = http_session.query(AuditEvent).count()
    assert entries == 2, "Jede versandte Meldung bekommt einen Audit-Eintrag."

    engine.dispose()


@pytest.mark.anyio
async def test_schattenschleife_meldet_einen_neuen_sensorausfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Meldung geht wirklich hinaus — bisher war nur belegt, dass sie entsteht.

    Die Zone startet im Zustand `ok` und verliert ihre Messquelle. Genau dieser Wechsel
    ist meldungswuerdig; der zweite Zyklus mit unveraenderter Lage darf nicht erneut
    melden. Ein Zustand ohne Vorgeschichte meldet ausdruecklich nicht — sonst feuerte
    beim ersten Start jede noch unkonfigurierte Zone.
    """
    engine, fabrik = _own_database(tmp_path, "schleife-meldung")
    with fabrik() as http_session:
        create_settings(http_session)
        source(http_session, "system")
        sensorstatus(http_session, "keine_quelle")
        sensorstatus(http_session, "ok")
        zone = create_zone(http_session, "flur-ohne-quelle")
        create_zone_state(http_session, zone)  # startet als 'ok'
        http_session.commit()

    gesendet: list[object] = []

    async def mitschreiben(_settings: object, notice: object) -> None:
        gesendet.append(notice)

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            raise asyncio.CancelledError

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)
    monkeypatch.setattr(app_modul, "send", mitschreiben)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadowschleife(fake_app)  # type: ignore[arg-type]

    # Der Versand laeuft seit dem Abschlussreview nebenher, damit ein haengender Webhook
    # den Zyklustakt nicht verschiebt. Die noch offenen Aufgaben werden hier abgewartet,
    # damit der Test nicht davon abhaengt, wann der Ereignisschleife danach ist.
    for aufgabe in list(app_modul._running_notices):
        await aufgabe

    assert len(gesendet) == 1, (
        "Zwei Zyklen mit derselben Stoerung ergeben eine Meldung, nicht zwei."
    )

    engine.dispose()
