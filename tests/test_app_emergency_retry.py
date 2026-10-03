"""Dreistufiger Meldezustand der Notbetrieb-Meldungen (`app.py::_emergency_notices`).

Vor dem Versand wird ein Zwischenzustand committet (`melden_laeuft` bzw.
`entwarnung_laeuft`), nach dem Versand der Endzustand (`gemeldet` bzw.
`entwarnung_gesendet`). Findet ein Lauf -- auch nach einem Neustart -- eine Episode
im Zwischenzustand, wird der Versand genau einmal wiederholt und danach der
Endzustand gesetzt, auch wenn der zweite Versuch scheitert (keine Meldungsflut).
Lieber eine doppelte als eine fehlende Meldung.

Jeder Test benutzt seine eigene SQLite-Datei: Der Versand oeffnet eigene,
kurze Sitzungen, die ueber `session_factory` committen muessen, damit eine zweite,
unabhaengige Sitzung ("nach dem Neustart") das Ergebnis sieht.
"""

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import create_settings, create_zone, source
from tests.test_app_emergency_notices import NOW, _episode
from thermoctl import app as app_modul
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory, session_scope
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.sensor_failure import SensorFailureEpisode
from thermoctl.domain import emergency_operation
from thermoctl.domain.fault_notice import FaultNotice


@pytest.fixture
def fabrik(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/retry.db", future=True)
    Base.metadata.create_all(engine)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_stale_in_flight() -> Iterator[None]:
    app_modul._emergency_in_flight.clear()
    yield
    app_modul._emergency_in_flight.clear()


class Sender:
    """Replaces `deliver`; answers with the scripted outcomes, then with success."""

    def __init__(self, outcomes: list[bool | BaseException] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.sent: list[FaultNotice] = []

    async def __call__(self, _factory: object, _settings: object, notice: FaultNotice) -> bool:
        self.sent.append(notice)
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _seed(fabrik: sessionmaker[Session], **episode_kwargs: object) -> int:
    with session_scope(fabrik) as session:
        create_settings(session)
        source(session, "system")
        zone = create_zone(session, "Flur")
        return _episode(session, zone, **episode_kwargs).id  # type: ignore[arg-type]


def _cycle(fabrik: sessionmaker[Session], now: datetime = NOW) -> list[app_modul.EmergencyDispatch]:
    """What the shadow loop does per cycle: plan inside the transaction, commit."""
    from thermoctl.db.models.operations import Setting

    with session_scope(fabrik) as session:
        return app_modul._emergency_notices(session, now, session.get(Setting, 1))


def _state(fabrik: sessionmaker[Session], episode_id: int) -> str:
    with fabrik() as session:
        episode = session.get(SensorFailureEpisode, episode_id)
        assert episode is not None
        return episode.notification_state


async def _send(
    fabrik: sessionmaker[Session],
    dispatches: list[app_modul.EmergencyDispatch],
    *,
    send: bool = True,
) -> None:
    for dispatch in dispatches:
        await app_modul._deliver_emergency_notice(fabrik, object(), dispatch, send=send)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_entered_success_sends_exactly_one_notice(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    dispatches = _cycle(fabrik)
    assert [d.is_retry for d in dispatches] == [False]
    # Committed *before* the send -- this is the whole point.
    assert _state(fabrik, episode_id) == "melden_laeuft"
    await _send(fabrik, dispatches)

    assert _state(fabrik, episode_id) == "gemeldet"
    assert _cycle(fabrik) == []
    assert [n.severity for n in sender.sent] == ["stoerung"]


@pytest.mark.anyio
async def test_crash_between_commit_and_send_retries_once_after_restart(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    _cycle(fabrik)  # committed `melden_laeuft`, then the process dies: no send
    assert sender.sent == []
    assert _state(fabrik, episode_id) == "melden_laeuft"

    app_modul._emergency_in_flight.clear()  # restart: nothing is in flight any more
    dispatches = _cycle(fabrik)
    assert [(d.episode_id, d.is_retry) for d in dispatches] == [(episode_id, True)]
    assert dispatches[0].notice.severity == "stoerung"
    await _send(fabrik, dispatches)

    assert [n.key for n in sender.sent] == [f"notbetrieb:{episode_id}"]
    assert _state(fabrik, episode_id) == "gemeldet"
    assert _cycle(fabrik) == []


@pytest.mark.anyio
async def test_a_failed_first_send_is_retried_once_and_never_a_third_time(
    fabrik: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = Sender([False, False])
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    await _send(fabrik, _cycle(fabrik))
    # The first attempt failed: not confirmed, so the intermediate state stays.
    assert _state(fabrik, episode_id) == "melden_laeuft"

    caplog.set_level(logging.WARNING)
    retry = _cycle(fabrik)
    assert [d.is_retry for d in retry] == [True]
    await _send(fabrik, retry)
    # The retry failed as well: final state anyway, and it is logged.
    assert _state(fabrik, episode_id) == "gemeldet"
    assert "nicht zugestellt" in caplog.text

    assert _cycle(fabrik) == []
    assert _cycle(fabrik) == []
    assert len(sender.sent) == 2


@pytest.mark.anyio
async def test_a_failed_first_send_then_successful_retry_ends_in_the_final_state(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender([False, True])
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    await _send(fabrik, _cycle(fabrik))
    await _send(fabrik, _cycle(fabrik))

    assert _state(fabrik, episode_id) == "gemeldet"
    assert len(sender.sent) == 2
    assert _cycle(fabrik) == []


@pytest.mark.anyio
async def test_a_raising_deliver_counts_as_a_failed_attempt(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender([RuntimeError("boom"), RuntimeError("boom")])
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    await _send(fabrik, _cycle(fabrik))
    assert _state(fabrik, episode_id) == "melden_laeuft"
    await _send(fabrik, _cycle(fabrik))
    assert _state(fabrik, episode_id) == "gemeldet"
    assert len(sender.sent) == 2


@pytest.mark.anyio
async def test_resolved_notice_success_sends_exactly_one_entwarnung(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(
        fabrik,
        notification_state="gemeldet",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_RUECKKEHR_ABGESCHLOSSEN,
    )

    dispatches = _cycle(fabrik)
    assert _state(fabrik, episode_id) == "entwarnung_laeuft"
    await _send(fabrik, dispatches)

    assert _state(fabrik, episode_id) == "entwarnung_gesendet"
    assert _cycle(fabrik) == []
    assert [n.severity for n in sender.sent] == ["entwarnung"]


@pytest.mark.anyio
async def test_resolved_notice_crash_retries_once_and_never_a_third_time(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender([False])
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(
        fabrik,
        notification_state="entwarnung_laeuft",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_RUECKKEHR_ABGESCHLOSSEN,
    )

    # Found after a restart in the intermediate state: one more attempt, which fails.
    retry = _cycle(fabrik)
    assert [(d.is_retry, d.notice.severity) for d in retry] == [(True, "entwarnung")]
    await _send(fabrik, retry)

    assert _state(fabrik, episode_id) == "entwarnung_gesendet"
    assert _cycle(fabrik) == []
    assert len(sender.sent) == 1


@pytest.mark.anyio
async def test_resolved_notice_failed_first_send_is_retried_next_cycle(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender([False, False])
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(
        fabrik,
        notification_state="gemeldet",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_RUECKKEHR_ABGESCHLOSSEN,
    )

    await _send(fabrik, _cycle(fabrik))
    assert _state(fabrik, episode_id) == "entwarnung_laeuft"
    await _send(fabrik, _cycle(fabrik))
    assert _state(fabrik, episode_id) == "entwarnung_gesendet"
    assert _cycle(fabrik) == []
    assert len(sender.sent) == 2


@pytest.mark.anyio
async def test_a_switched_off_notice_kind_skips_the_send_but_closes_the_state(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`notify_sensor_faults` off: unchanged -- nothing is sent, the episode still
    moves on (no retry loop for a notice that is switched off on purpose)."""
    sender = Sender()
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(fabrik)

    await _send(fabrik, _cycle(fabrik), send=False)

    assert sender.sent == []
    assert _state(fabrik, episode_id) == "gemeldet"
    assert _cycle(fabrik) == []


def test_an_episode_whose_send_is_still_in_flight_is_not_retried(
    fabrik: sessionmaker[Session],
) -> None:
    episode_id = _seed(fabrik)
    _cycle(fabrik)
    app_modul._emergency_in_flight.add(episode_id)

    assert _cycle(fabrik) == []
    assert _state(fabrik, episode_id) == "melden_laeuft"


def test_a_retry_is_not_audited_a_second_time(fabrik: sessionmaker[Session]) -> None:
    _seed(fabrik)
    _cycle(fabrik)
    _cycle(fabrik)  # retry

    with fabrik() as session:
        assert len(session.scalars(select(AuditEvent)).all()) == 1


@pytest.mark.anyio
async def test_a_deactivated_episode_in_the_intermediate_state_is_retried_without_entwarnung(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash after the Stoerungsmeldung was planned, episode closed by deactivation in
    the meantime: the (missing) Stoerungsmeldung is still retried, but the episode then
    closes without an Entwarnung -- unchanged deactivation contract."""
    sender = Sender()
    monkeypatch.setattr(app_modul, "deliver", sender)
    episode_id = _seed(
        fabrik,
        notification_state="melden_laeuft",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_DEAKTIVIERT,
    )

    await _send(fabrik, _cycle(fabrik))
    assert _state(fabrik, episode_id) == "gemeldet"
    assert _cycle(fabrik) == []
    assert _state(fabrik, episode_id) == "entwarnung_gesendet"
    assert [n.severity for n in sender.sent] == ["stoerung"]


async def _run_loop_once(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, sender: Sender
) -> None:
    """Runs exactly one cycle of the real shadow loop around a seeded episode.

    `cycle` and `advance_zone_state` are replaced by no-ops: the episode is seeded by
    hand and must reach `_emergency_notices` untouched by the real shadow run.
    """
    import asyncio
    import types

    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            raise asyncio.CancelledError

    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(session_factory=fabrik, sending_allowed=False)
    )
    monkeypatch.setattr(app_modul.asyncio, "sleep", _sleep)
    monkeypatch.setattr(app_modul, "deliver", sender)
    monkeypatch.setattr(app_modul, "cycle", lambda *_a, **_k: None)
    monkeypatch.setattr(app_modul, "advance_zone_state", lambda *_a, **_k: None)

    with pytest.raises(asyncio.CancelledError):
        await app_modul._shadow_loop(fake_app)  # type: ignore[arg-type]
    for task in list(app_modul._running_notices):
        await task


@pytest.mark.anyio
async def test_the_shadow_loop_sends_the_emergency_notice_and_then_marks_it_gemeldet(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = Sender()
    episode_id = _seed(fabrik)

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert [n.key for n in sender.sent] == [f"notbetrieb:{episode_id}"]
    assert _state(fabrik, episode_id) == "gemeldet"
    assert app_modul._emergency_in_flight == set()


@pytest.mark.anyio
async def test_the_shadow_loop_with_the_notice_kind_switched_off_sends_nothing(
    fabrik: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from thermoctl.db.models.operations import Setting

    sender = Sender()
    episode_id = _seed(fabrik)
    with session_scope(fabrik) as session:
        row = session.get(Setting, 1)
        assert row is not None
        row.notify_sensor_faults = False

    await _run_loop_once(fabrik, monkeypatch, sender)

    assert sender.sent == []
    assert _state(fabrik, episode_id) == "gemeldet"
