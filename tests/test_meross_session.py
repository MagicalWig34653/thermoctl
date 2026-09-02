"""Tests for the cached, periodically-refreshed Meross sign-in state.

No test here ever touches the network (CLAUDE.md) -- `sign_in` runs against a fake
`JsonTransport` double, the same convention `tests/test_meross.py` uses.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import create_settings
from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.operations import Setting
from thermoctl.integrations.meross_mqtt import AiomqttCommandTransport
from thermoctl.services.meross_session import (
    SESSION_TTL,
    MerossSessionCache,
    ensure_transport,
    invalidate,
)

NOW = datetime(2026, 9, 1, 12, 0)

_SIGN_IN_ANSWER: dict[str, Any] = {
    "apiStatus": 0,
    "data": {
        "token": "a-token",
        "key": "a-key",
        "userid": "4711",
        "mqttDomain": "mqtt-eu.meross.com",
    },
}


class _FakeJsonTransport:
    def __init__(self, *answers: Mapping[str, Any] | Exception) -> None:
        self.answers: list[Mapping[str, Any] | Exception] = list(answers)
        self.calls = 0

    async def post_json(
        self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else _SIGN_IN_ANSWER
        if isinstance(answer, Exception):
            raise answer
        return answer


def _settings_with_credentials() -> Settings:
    return Settings(meross_email="a@b.de", meross_password="geheim")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_without_credentials_nothing_is_attempted() -> None:
    transport = _FakeJsonTransport()
    settings = Settings(meross_email=None, meross_password=None)

    result = await ensure_transport(settings, transport, MerossSessionCache(), NOW)

    assert result is None
    assert transport.calls == 0


@pytest.mark.anyio
async def test_a_fresh_cache_signs_in_and_returns_a_working_transport() -> None:
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER)
    cache = MerossSessionCache()

    result = await ensure_transport(_settings_with_credentials(), transport, cache, NOW)

    assert isinstance(result, AiomqttCommandTransport)
    assert transport.calls == 1
    assert cache.connection is not None
    assert cache.expires_at == NOW + SESSION_TTL


@pytest.mark.anyio
async def test_a_cached_connection_is_reused_without_signing_in_again() -> None:
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER)
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)
    await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + timedelta(minutes=5)
    )

    assert transport.calls == 1


@pytest.mark.anyio
async def test_an_expired_cache_signs_in_again() -> None:
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER, _SIGN_IN_ANSWER)
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)
    await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + SESSION_TTL + timedelta(seconds=1)
    )

    assert transport.calls == 2


@pytest.mark.anyio
async def test_invalidate_forces_a_fresh_sign_in_before_the_ttl_expires() -> None:
    transport = _FakeJsonTransport(_SIGN_IN_ANSWER, _SIGN_IN_ANSWER)
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)
    invalidate(cache)
    await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + timedelta(seconds=1)
    )

    assert transport.calls == 2
    assert cache.invalid is False


@pytest.mark.anyio
async def test_a_rejected_sign_in_returns_none_and_caches_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gegenprobe for a rejected Meross sign-in: it ends as `None`, not an
    exception -- the caller (`services/publishing.py`) turns that into a `failed`
    command-log entry per device, it does not stop the cycle here."""
    transport = _FakeJsonTransport({"apiStatus": 1004, "info": "Wrong password"})
    cache = MerossSessionCache()

    with caplog.at_level("ERROR"):
        result = await ensure_transport(_settings_with_credentials(), transport, cache, NOW)

    assert result is None
    assert cache.connection is None
    assert "Meross" in caplog.text


@pytest.mark.anyio
async def test_meross_sign_in_runs_without_an_open_database_session(tmp_path: Any) -> None:
    """The property this module exists for: signing in must never happen while a
    database transaction is open (see the module docstring and CLAUDE.md's account
    of the 40 second SQLite lock). Checked the same way
    `tests/test_meross.py::test_meross_network_calls_run_without_an_open_database_session`
    checks it for the device-list reconciliation -- a concurrent writer commits while
    the network call is in flight, and the network call itself records whether any
    session was open at that moment."""
    database = tmp_path / "meross-session-lock.db"
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{database}"))
    Base.metadata.create_all(engine)
    writer_factory = session_factory(engine)
    with session_scope(writer_factory) as setup_session:
        create_settings(setup_session)

    open_sessions: set[int] = set()

    class _TrackedSession(Session):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            open_sessions.add(id(self))

        def close(self) -> None:
            open_sessions.discard(id(self))
            super().close()

    tracked_factory = sessionmaker(
        bind=engine, class_=_TrackedSession, expire_on_commit=False, future=True
    )

    network_saw_open_session = False

    class _WritingTransport(_FakeJsonTransport):
        async def post_json(
            self, url: str, body: Mapping[str, object], headers: Mapping[str, str]
        ) -> Mapping[str, object]:
            nonlocal network_saw_open_session
            network_saw_open_session |= bool(open_sessions)
            with session_scope(tracked_factory) as writer:
                writer.execute(
                    update(Setting).where(Setting.id == 1).values(timezone="Europe/Berlin")
                )
            return await super().post_json(url, body, headers)

    transport = _WritingTransport(_SIGN_IN_ANSWER)
    try:
        result = await ensure_transport(
            _settings_with_credentials(), transport, MerossSessionCache(), NOW
        )
    finally:
        engine.dispose()

    assert result is not None
    assert not open_sessions
    assert network_saw_open_session is False
    assert transport.calls == 1
