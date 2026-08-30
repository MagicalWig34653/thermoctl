"""Tests for the shadow run (`thermoctl.services.shadow_run`).

This task is measured by its tests (assignment task 9): that exactly one row per zone
and cycle is produced, that the minimum switching duration via `seit_s` really has an
effect, that a broken zone does not hold up the rest — and above all, that in this
phase nothing gets published anywhere.
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
    role,
    sensor_status_of,
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
    """A dedicated, named SQLite file per test.

    `_schattenschleife` and `_mqtt_nachricht_verarbeiten` open their own sessions
    via `app.state.session_factory` -- the transaction-isolated `session` fixture
    in this file does not fit here, because these functions have to commit to
    achieve anything that a second, independently opened session can see again.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db", future=True)
    Base.metadata.create_all(engine)
    return engine, session_factory(engine)


def _zone_with_state(
    session: Session,
    name: str,
    *,
    measured_c: Decimal | None,
    now: datetime = NOW,
    status: str = "ok",
) -> Zone:
    zone = create_zone(session, name)
    state = ZoneState(
        zone_id=zone.id,
        temperature_c=measured_c,
        measured_at=now,
        sensor_status_id=sensor_status_of(session, status).id,
        updated_at=now,
    )
    session.add(state)
    session.flush()
    return zone


def test_one_cycle_with_a_fresh_reading_writes_a_row_with_a_reason(
    session: Session,
) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    zone = _zone_with_state(session, "buero", measured_c=Decimal("10.0"))

    rows = shadow_run.cycle(session, NOW)

    assert len(rows) == 1
    row = rows[0]
    assert row.zone_id == zone.id
    assert row.would_heat is True
    assert row.outcome_code == "heizen"
    assert row.reason and "Ist" in row.reason
    assert row.previous_would_heat is None  # no history in the first cycle
    assert session.query(ShadowDecision).count() == 1


def test_several_cycles_with_an_unchanged_situation_yield_unchanged_without_a_flood_of_rows(
    session: Session,
) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    # Frost-protection fallback is 16.0 °C (no schedule configured); 16.0 °C sits
    # inside the ±0.30 K hysteresis band and therefore never changes anything.
    zone = _zone_with_state(session, "flur", measured_c=Decimal("16.0"))
    # Minimum switching duration set to 0: otherwise rule 5 (minimum switching
    # duration) would already kick in from the second cycle and yield
    # 'gesperrt_mindestdauer' instead of 'unveraendert' — regardless of the fact
    # that nothing about the situation changed. That is not the behavior this
    # test is meant to demonstrate here (the next test covers that).
    zone.min_on_seconds = 0
    zone.min_off_seconds = 0
    session.flush()

    for i in range(3):
        result = shadow_run.cycle(session, NOW + timedelta(minutes=i))
        assert len(result) == 1  # exactly one row per cycle, no gap

    rows = list(
        session.scalars(
            select(ShadowDecision)
            .where(ShadowDecision.zone_id == zone.id)
            .order_by(ShadowDecision.decided_at)
        )
    )
    assert len(rows) == 3  # no flood of rows across the three cycles
    assert [z.outcome_code for z in rows] == ["unveraendert"] * 3
    assert [z.would_heat for z in rows] == [False, False, False]


def test_the_elapsed_time_grows_across_cycles_and_resets_on_a_change(
    session: Session,
) -> None:
    """The minimum switching duration only works if `seit_s` keeps growing across cycles.

    Without deriving it from the zone's own decision history, `seit_s` would be
    `None` on every cycle, and rule 5 (minimum switching duration) would never
    kick in during shadow operation — without a single test ever catching that.
    The sequence here forces the rule to decide twice: once while the minimum
    duration has not yet elapsed (the lock holds), once after (the lock lifts).
    """
    create_settings(session, hysteresis=Decimal("0.50"))
    zone = create_zone(session, "keller")
    zone.min_on_seconds = 100
    zone.min_off_seconds = 5
    session.flush()

    def _state(measured_c: Decimal, now: datetime) -> None:
        bisherig = session.get(ZoneState, zone.id)
        if bisherig is not None:
            session.delete(bisherig)
            session.flush()
        session.add(
            ZoneState(
                zone_id=zone.id,
                temperature_c=measured_c,
                measured_at=now,
                sensor_status_id=sensor_status_of(session, "ok").id,
                updated_at=now,
            )
        )
        session.flush()

    # Cycle 1 (t=0s): far above the setpoint (frost-protection fallback 16.0 °C) —
    # no history, stays off.
    _state(Decimal("20.0"), NOW)
    z1 = shadow_run.cycle(session, NOW)[0]
    assert z1.would_heat is False
    assert z1.outcome_code == "unveraendert"

    # Cycle 2 (t=+10s): far below the setpoint. seit_s=10s is enough for
    # min_off_seconds=5, the hysteresis switches on.
    moment_two = NOW + timedelta(seconds=10)
    _state(Decimal("5.0"), moment_two)
    z2 = shadow_run.cycle(session, moment_two)[0]
    assert z2.would_heat is True
    assert z2.outcome_code == "heizen"
    assert z2.previous_would_heat is False

    # Cycle 3 (t=+20s): above the setpoint again, but the heating phase only
    # started 10s ago — with min_on_seconds=100 the lock holds, regardless of
    # the hysteresis.
    moment_three = NOW + timedelta(seconds=20)
    _state(Decimal("20.0"), moment_three)
    z3 = shadow_run.cycle(session, moment_three)[0]
    assert z3.would_heat is True
    assert z3.outcome_code == "gesperrt_mindestdauer"
    assert z3.previous_would_heat is True

    # Cycle 4 (t=+130s): the same situation as before, but the heating phase
    # (started at cycle 2) has now been running for 120s — longer than
    # min_on_seconds=100. The lock lifts, the hysteresis switches off. That
    # would be impossible if `seit_s` had not kept growing across all three
    # preceding cycles.
    moment_four = NOW + timedelta(seconds=130)
    _state(Decimal("20.0"), moment_four)
    z4 = shadow_run.cycle(session, moment_four)[0]
    assert z4.would_heat is False
    assert z4.outcome_code == "aus"
    assert z4.previous_would_heat is True

    # Cycle 5 (t=+131s): immediately after the switch, below the setpoint again.
    # seit_s falls back to 1s on the change — too short for min_off_seconds=5,
    # the lock holds again, even though the same zone was unlocked seconds
    # earlier.
    moment_five = NOW + timedelta(seconds=131)
    _state(Decimal("5.0"), moment_five)
    z5 = shadow_run.cycle(session, moment_five)[0]
    assert z5.would_heat is False
    assert z5.outcome_code == "gesperrt_mindestdauer"


def test_a_zone_without_a_temperature_source_gets_a_no_source_row(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "abstellraum")  # no ZoneState row at all

    rows = shadow_run.cycle(session, NOW)

    assert len(rows) == 1
    assert rows[0].zone_id == zone.id
    assert rows[0].outcome_code == "keine_quelle"
    assert rows[0].would_heat is False
    assert rows[0].temperature_c is None


def test_a_zone_without_a_window_contact_heats_despite_an_unknown_window_state(
    session: Session,
) -> None:
    create_settings(session)
    zone = _zone_with_state(session, "ohne-fensterkontakt", measured_c=Decimal("5.0"))
    state = session.get(ZoneState, zone.id)
    assert state is not None and state.window_open is None

    row = shadow_run.cycle(session, NOW)[0]

    assert row.would_heat is True
    assert row.outcome_code == "heizen"


def test_closing_a_window_starts_a_growing_restart_delay(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.default_window_resume_delay_seconds = 120
    zone = _zone_with_state(session, "fensterpause", measured_c=Decimal("5.0"))
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
            device_role_id=role(session, "window_contact").id,
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


def test_a_failing_zone_does_not_hold_up_the_others(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    healthy = _zone_with_state(session, "gesund", measured_c=Decimal("10.0"))
    kaputt = _zone_with_state(session, "kaputt", measured_c=Decimal("10.0"))
    assert kaputt.id > healthy.id  # order by id, the way `zyklus()` walks through them

    original = shadow_run.control_parameters

    def _manchmal_kaputt(session: Session, zone: Zone) -> object:
        if zone.id == kaputt.id:
            raise RuntimeError("Simulated error in one zone")
        return original(session, zone)

    monkeypatch.setattr(shadow_run, "control_parameters", _manchmal_kaputt)

    rows = shadow_run.cycle(session, NOW)

    assert [z.zone_id for z in rows] == [healthy.id]
    # The failed attempt of the broken zone left behind no (half-finished) row —
    # the per-zone savepoint rolled it back completely.
    assert session.query(ShadowDecision).filter_by(zone_id=kaputt.id).count() == 0
    assert session.query(ShadowDecision).count() == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_no_publishing_despite_a_heating_decision(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demonstrates the hard boundary from section 1 of the spec with a fake
    client: a full cycle arrives at 'would heat' -- yet every attempt to
    publish via the reachable MQTT client remains without effect.
    """

    class GefaelschterClient:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        async def publish(self, topic: str, payload: str) -> None:
            self.published.append((topic, payload))

    create_settings(session)
    _zone_with_state(session, "wohnzimmer", measured_c=Decimal("5.0"))
    rows = shadow_run.cycle(session, NOW)
    assert rows[0].would_heat is True  # the starting situation is armed: it would heat

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


def test_the_background_run_does_not_start_without_mqtt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement from the assignment: without `mqtt_enabled`, no background task
    may be created at startup -- the test suite builds the application constantly.
    """
    # A named file instead of 'sqlite://': an unnamed in-memory database would be
    # its own, empty database per connection -- the application and this test
    # would then see two different, both empty, databases, without any error
    # having occurred that would explain it.
    datenbank_url = f"sqlite:///{tmp_path}/hintergrundlauf.db"
    own_engine = create_engine(datenbank_url, future=True)
    Base.metadata.create_all(own_engine)
    settings = Settings(_env_file=None, database_url=datenbank_url, secret_key="h" * 32)
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()

    def _should_not_have_started(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "asyncio.create_task() was called even though mqtt_enabled=False"
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
async def test_the_shadow_loop_reads_the_interval_and_writes_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_schattenschleife` waits out the interval configured in
    `setting.shadow_interval_seconds` and then really writes a result -- in its
    own session, as the assignment requires (not in the session that set up the
    lifespan).
    """
    engine, fabrik = _own_database(tmp_path, "schleife")
    with fabrik() as http_session:
        create_settings(http_session)
        settings = http_session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 42
        sensor_status_of(http_session, "keine_quelle")  # `zonenzustand_fortschreiben` needs it
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            # Simulates the shutdown abort while the loop is waiting during the
            # second pass.
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]

    assert waited[0] == 42  # read from setting.shadow_interval_seconds, not the
    # built-in default value

    with fabrik() as http_session:
        rows = list(http_session.scalars(select(ShadowDecision)))
    assert len(rows) == 1  # one pass before the simulated abort
    assert rows[0].outcome_code == "keine_quelle"  # the zone has no temperature source

    engine.dispose()


@pytest.mark.anyio
async def test_the_shadow_loop_survives_a_missing_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a `setting` row (setup not completed), the loop must neither crash
    nor hang -- it falls back to the built-in default value and tries again next
    time."""
    engine, fabrik = _own_database(tmp_path, "ohne-setting")
    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]

    assert waited == [60, 60]  # built-in default value, twice in a row

    with fabrik() as http_session:
        assert http_session.query(ShadowDecision).count() == 0  # no crash, but also no
        # row without a zone

    engine.dispose()


@pytest.mark.anyio
async def test_the_shadow_loop_survives_an_exception_in_the_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error in one cycle (here: `zonenzustand_fortschreiben` fails) does not end
    the loop -- log it, keep going, the next pass runs regularly."""
    engine, fabrik = _own_database(tmp_path, "fehler-im-zyklus")
    with fabrik() as http_session:
        create_settings(http_session)
        sensor_status_of(http_session, "keine_quelle")
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))

    aufrufe = 0
    original = app_modul.advance_zone_state

    def _first_attempt_fails(session: Session, now: datetime) -> None:
        nonlocal aufrufe
        aufrufe += 1
        if aufrufe == 1:
            raise ValueError("Simulated error in the first cycle")
        original(session, now)

    monkeypatch.setattr(app_modul, "advance_zone_state", _first_attempt_fails)

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]

    assert aufrufe == 2  # the first attempt failed, the second continued regularly
    with fabrik() as http_session:
        assert http_session.query(ShadowDecision).count() == 1  # only the second cycle wrote

    engine.dispose()


@pytest.mark.anyio
async def test_the_shadow_loop_triggers_retention_once_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`alte_messwerte_loeschen()` runs from the same loop, but only once per day
    -- not on every cycle (assignment text, 'trigger retention' section)."""
    engine, fabrik = _own_database(tmp_path, "aufbewahrung")
    with fabrik() as http_session:
        create_settings(http_session)
        sensor_status_of(http_session, "keine_quelle")
        create_zone(http_session, "flur")
        http_session.commit()

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))

    # The first call sets 'naechste_aufbewahrung' to NOW+1 day; the second (the
    # measurement time of the single cycle before the abort) is two days later --
    # well above that, so retention must show up exactly once.
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
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]

    assert aufrufe == [NOW + timedelta(days=2)]

    engine.dispose()

    engine.dispose()


@pytest.mark.anyio
async def test_processing_an_mqtt_message_writes_in_its_own_session(
    tmp_path: Path,
) -> None:
    """The lifespan's MQTT handler processes a message in a freshly opened session
    -- independent of the one that started the service."""
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
    """An MQTT message stream that never delivers -- like a real, quiet connection.

    It only breaks off when the task is cancelled from outside. That is exactly
    the case that must reliably work on shutdown: without a hanging process.
    """

    def __aiter__(self) -> _HangingStream:
        return self

    async def __anext__(self) -> object:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


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


def test_the_lifespan_starts_and_stops_mqtt_and_the_shadow_loop_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual proof of 'clean shutdown': both background tasks run
    (the MQTT connection deliberately hangs in a never-delivering message loop),
    and `with TestClient(...)` still returns -- no hanging process on shutdown."""
    engine, fabrik = _own_database(tmp_path, "lifespan-mqtt")
    with fabrik() as http_session:
        sensor_status_of(http_session, "keine_quelle")
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
        pass  # exiting this block must return, otherwise the test hangs

    engine.dispose()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_the_bridge_state_reports_only_the_change(tmp_path: Path) -> None:
    """Zigbee2MQTT sends `bridge/state` again on every reconnect.

    What gets reported is the change, not the state — otherwise, after a night
    with flaky radio, you would get a hundred identical notices and end up
    muting them.
    """
    engine, fabrik = _own_database(tmp_path, "bridge_notice")
    with fabrik() as http_session:
        integration(http_session, "zigbee2mqtt")
        source(http_session, "system")
        http_session.commit()

    sent_count: list[object] = []

    async def mitschreiben(_settings: object, notice: object) -> None:
        sent_count.append(notice)

    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(session_factory=fabrik, bridge_reachable=True)
    )
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key="q" * 32,
        mqtt_base_topic="testbasis",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_modul, "send", mitschreiben)
        # Two 'offline' calls — only the first one is a change.
        for _ in range(2):
            await app_modul._process_mqtt_message(
                fake_app,  # type: ignore[arg-type]
                settings, "testbasis/bridge/state", b'{"state": "offline"}',
            )
        # Back to 'online': the all-clear.
        await app_modul._process_mqtt_message(
            fake_app,  # type: ignore[arg-type]
            settings, "testbasis/bridge/state", b'{"state": "online"}',
        )
        # An unreadable payload changes nothing and reports nothing.
        await app_modul._process_mqtt_message(
            fake_app,  # type: ignore[arg-type]
            settings, "testbasis/bridge/state", b"{kaputt",
        )

    assert [m.severity for m in sent_count] == ["stoerung", "entwarnung"]  # type: ignore[attr-defined]
    with fabrik() as http_session:
        entries = http_session.query(AuditEvent).count()
    assert entries == 2, "Every notice sent gets an audit entry."

    engine.dispose()


@pytest.mark.anyio
async def test_the_shadow_loop_reports_a_new_sensor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice really goes out — until now only that it was created was proven.

    The zone starts in state `ok` and loses its measurement source. Exactly this
    change is worth reporting; the second cycle with an unchanged situation must
    not report again. A state with no history explicitly does not report --
    otherwise every still-unconfigured zone would fire on the very first start.
    """
    engine, fabrik = _own_database(tmp_path, "schleife-meldung")
    with fabrik() as http_session:
        create_settings(http_session)
        source(http_session, "system")
        sensor_status_of(http_session, "keine_quelle")
        sensor_status_of(http_session, "ok")
        zone = create_zone(http_session, "flur-ohne-quelle")
        create_zone_state(http_session, zone)  # starts as 'ok'
        http_session.commit()

    sent_count: list[object] = []

    async def mitschreiben(_settings: object, notice: object) -> None:
        sent_count.append(notice)

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            raise asyncio.CancelledError

    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(session_factory=fabrik))
    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)
    monkeypatch.setattr(app_modul, "send", mitschreiben)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]

    # Sending has run alongside since the closing review, so that a hanging
    # webhook does not shift the cycle cadence. The still-open tasks are
    # awaited here so the test does not depend on when the event loop gets
    # around to them.
    for task in list(app_modul._running_notices):
        await task

    assert len(sent_count) == 1, (
        "Two cycles with the same fault yield one notice, not two."
    )

    engine.dispose()
