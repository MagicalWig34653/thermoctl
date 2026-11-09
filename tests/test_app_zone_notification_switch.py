"""Raum-Schalter "Ausfallbenachrichtigungen" auf Zyklus- und Schleifenebene.

Der Schalter (`zone.fault_notifications_enabled`) hält nur die Zustellung zurück
(`deliver`/Webhook). Prüfen: kein Versand, Audit `notification.suppressed`, die
Störung selbst bleibt (Home-Assistant-Weg und Audit-Eintrag), eine Notbetrieb-Episode
kommt trotzdem in ihren Endzustand, und ein Gerät mit mehreren Zonen wird nur
unterdrückt, wenn alle abgeschaltet sind.
"""

import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import create_all_command_outcomes, create_zone_state
from tests.test_actuator_switches import NOW as SWITCH_NOW
from tests.test_actuator_switches import FailingClient, _actuator_zone, _decision
from tests.test_app_emergency_retry import (
    Sender,
    _cycle,
    _run_loop_once,
    _seed,
    _state,
)
from thermoctl import app as app_modul
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory, session_scope
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.sensor_failure import SensorFailureEpisode
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import arm
from thermoctl.domain.fault_notice import FaultNotice
from thermoctl.services.publishing import PublicationState, cycle


@pytest.fixture
def fabrik(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/schalter.db", future=True)
    Base.metadata.create_all(engine)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_stale_in_flight() -> Iterator[None]:
    app_modul._emergency_in_flight.clear()
    yield
    app_modul._emergency_in_flight.clear()


def _fault_audit(fabrik: sessionmaker[Session]) -> list[str]:
    with fabrik() as session:
        return [
            entry.action
            for entry in session.scalars(
                select(AuditEvent).where(AuditEvent.object_type == "fault").order_by(AuditEvent.id)
            )
        ]


def _set_zone_switch(fabrik: sessionmaker[Session], enabled: bool) -> None:
    with session_scope(fabrik) as session:
        for zone in session.scalars(select(Zone)):
            zone.fault_notifications_enabled = enabled


# -- Notbetrieb-Episode -------------------------------------------------------------------


@pytest.mark.anyio
async def test_emergency_notice_of_a_muted_zone_is_not_sent_but_episode_still_closes(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    episode_id = _seed(fabrik)
    _set_zone_switch(fabrik, False)

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert sender.sent == []
    assert _state(fabrik, episode_id) == "gemeldet"
    assert _fault_audit(fabrik) == ["notification.suppressed"]
    assert app_modul._emergency_in_flight == set()
    # Kein Wiederholungsversuch im nächsten Zyklus: die Episode ist im Endzustand.
    assert _cycle(fabrik) == []


@pytest.mark.anyio
async def test_emergency_notice_of_a_zone_with_switch_on_is_sent_and_audited_as_sent(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    episode_id = _seed(fabrik)

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert [n.key for n in sender.sent] == [f"notbetrieb:{episode_id}"]
    assert _fault_audit(fabrik) == ["notification.sent"]


@pytest.mark.anyio
async def test_switch_turned_off_in_the_middle_of_an_episode_suppresses_the_all_clear(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Störung wurde gemeldet, dann Schalter aus, dann Entwarnung: unterdrückt, die
    Episode geht trotzdem nach `entwarnung_gesendet` (wie beim anlagenweiten Schalter:
    jede Meldung wird für sich zum Zeitpunkt ihres Versands entschieden)."""
    sender = Sender()
    episode_id = _seed(fabrik)
    await _run_loop_once(fabrik, monkeypatch, sender)
    assert [n.severity for n in sender.sent] == ["stoerung"]

    _set_zone_switch(fabrik, False)
    with session_scope(fabrik) as session:
        episode = session.get(SensorFailureEpisode, episode_id)
        assert episode is not None
        episode.ended_at = app_modul.utcnow()
        episode.ended_reason_code = "rueckkehr"
    await _run_loop_once(fabrik, monkeypatch, sender)

    assert [n.severity for n in sender.sent] == ["stoerung"]  # keine Entwarnung hinzugekommen
    assert _state(fabrik, episode_id) == "entwarnung_gesendet"
    assert _fault_audit(fabrik) == ["notification.sent", "notification.suppressed"]


@pytest.mark.anyio
async def test_switch_turned_on_in_the_middle_of_an_episode_sends_the_all_clear(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Störung wurde unterdrückt, dann Schalter an, dann Entwarnung: sie geht
    hinaus -- gleiches Verhalten wie beim anlagenweiten Schalter (kein Zustand
    darüber, ob die Störung zugestellt wurde)."""
    sender = Sender()
    episode_id = _seed(fabrik)
    _set_zone_switch(fabrik, False)
    await _run_loop_once(fabrik, monkeypatch, sender)
    assert sender.sent == []

    _set_zone_switch(fabrik, True)
    with session_scope(fabrik) as session:
        episode = session.get(SensorFailureEpisode, episode_id)
        assert episode is not None
        episode.ended_at = app_modul.utcnow()
        episode.ended_reason_code = "rueckkehr"
    await _run_loop_once(fabrik, monkeypatch, sender)

    assert [n.severity for n in sender.sent] == ["entwarnung"]
    assert _state(fabrik, episode_id) == "entwarnung_gesendet"


@pytest.mark.anyio
async def test_plant_wide_switch_still_applies_when_the_zone_switch_is_on(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    episode_id = _seed(fabrik)
    with session_scope(fabrik) as session:
        row = session.get(Setting, 1)
        assert row is not None
        row.notify_sensor_faults = False

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert sender.sent == []
    assert _state(fabrik, episode_id) == "gemeldet"
    assert _fault_audit(fabrik) == ["notification.suppressed"]


# -- Fenster-Alarm: Webhook unterdrückt, Home-Assistant-Weg unverändert -------------------


async def _run_window_alarm_loop(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[FaultNotice], list[FaultNotice]]:
    """One real shadow-loop cycle in which a window alarm starts. Returns the
    notices handed to the webhook and to the Home Assistant path."""
    import asyncio

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            raise asyncio.CancelledError

    def _advance(session: Session, _now: object) -> None:
        for state in session.scalars(select(ZoneState)):
            state.window_alarm = True

    webhook: list[FaultNotice] = []
    home_assistant: list[FaultNotice] = []

    async def _deliver(_factory: object, _settings: object, notice: FaultNotice) -> bool:
        webhook.append(notice)
        return True

    async def _send_window(_publisher: object, notice: FaultNotice, _prefix: str) -> None:
        home_assistant.append(notice)

    async def _publication_cycle(*_a: object, **_k: object) -> None:
        return None

    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            session_factory=fabrik,
            sending_allowed=False,
            publisher=object(),
            publication_state=PublicationState(),
        )
    )
    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)
    monkeypatch.setattr(app_modul, "deliver", _deliver)
    monkeypatch.setattr(app_modul, "cycle", lambda *_a, **_k: None)
    monkeypatch.setattr(app_modul, "advance_zone_state", _advance)
    monkeypatch.setattr(app_modul, "publication_cycle", _publication_cycle)
    monkeypatch.setattr(app_modul, "send_window_alarm_notice", _send_window)
    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]
    for task in list(app_modul._running_notices):
        await task
    return webhook, home_assistant


def _seed_window_zone(fabrik: sessionmaker[Session], *, switch: bool) -> None:
    from tests.helpers import create_settings, create_zone, source

    with session_scope(fabrik) as session:
        create_settings(session)
        source(session, "system")
        zone = create_zone(session, "Bad")
        zone.fault_notifications_enabled = switch
        state = create_zone_state(session, zone)
        state.window_alarm = False


@pytest.mark.anyio
async def test_muted_window_alarm_skips_the_webhook_but_still_reaches_home_assistant(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_window_zone(fabrik, switch=False)

    webhook, home_assistant = await _run_window_alarm_loop(fabrik, monkeypatch)

    assert webhook == []
    assert [n.severity for n in home_assistant] == ["stoerung"]
    assert _fault_audit(fabrik) == ["notification.suppressed"]


@pytest.mark.anyio
async def test_window_alarm_of_a_zone_with_switch_on_is_delivered_as_before(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_window_zone(fabrik, switch=True)

    webhook, home_assistant = await _run_window_alarm_loop(fabrik, monkeypatch)

    assert [n.severity for n in webhook] == ["stoerung"]
    assert [n.severity for n in home_assistant] == ["stoerung"]
    assert _fault_audit(fabrik) == ["notification.sent"]


# -- Schaltbefehl gescheitert: Gerät -> Zonen ---------------------------------------------


async def _failing_command_audit(
    session: Session, *, extra_zone_off: bool | None, first_zone_off: bool
) -> str:
    """Audit action of one failed switching command. `extra_zone_off` is `None` for a
    device in a single zone, otherwise the state of a second zone sharing the device."""
    from tests.helpers import create_settings, create_zone, role, source

    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "relaiszone")
    zone.fault_notifications_enabled = not first_zone_off
    if extra_zone_off is not None:
        second = create_zone(session, "zweitzone")
        second.fault_notifications_enabled = not extra_zone_off
        session.add(
            ZoneDevice(
                zone_id=second.id, device_id=device.id, device_role_id=role(session, "actuator").id
            )
        )
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    notices: list[FaultNotice] = []
    await cycle(
        session, FailingClient(), PublicationState(), "thermoctl", SWITCH_NOW, notices=notices
    )
    assert len(notices) == 1
    return session.query(AuditEvent).filter(AuditEvent.object_type == "fault").one().action


@pytest.mark.anyio
async def test_failed_command_to_a_device_of_a_muted_zone_is_audited_as_suppressed(
    session: Session,
) -> None:
    action = await _failing_command_audit(session, extra_zone_off=None, first_zone_off=True)
    assert action == "notification.suppressed"


@pytest.mark.anyio
async def test_failed_command_to_a_device_of_an_unmuted_zone_is_audited_as_sent(
    session: Session,
) -> None:
    action = await _failing_command_audit(session, extra_zone_off=None, first_zone_off=False)
    assert action == "notification.sent"


@pytest.mark.anyio
async def test_failed_command_to_a_device_in_two_zones_is_suppressed_only_if_both_are_muted(
    session: Session,
) -> None:
    both = await _failing_command_audit(session, extra_zone_off=True, first_zone_off=True)
    assert both == "notification.suppressed"


@pytest.mark.anyio
async def test_failed_command_to_a_device_in_two_zones_is_delivered_if_one_is_not_muted(
    session: Session,
) -> None:
    one = await _failing_command_audit(session, extra_zone_off=False, first_zone_off=True)
    assert one == "notification.sent"



@pytest.mark.anyio
async def test_a_broken_zone_switch_lookup_delivers_the_notice_anyway(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open: wenn der Schalter nicht gelesen werden kann, geht die Meldung hinaus
    (und die Episode kommt in ihren Endzustand)."""
    sender = Sender()
    episode_id = _seed(fabrik)
    _set_zone_switch(fabrik, False)

    def _broken(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(app_modul, "notice_deliverable", _broken)

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert [n.key for n in sender.sent] == [f"notbetrieb:{episode_id}"]
    assert _state(fabrik, episode_id) == "gemeldet"
