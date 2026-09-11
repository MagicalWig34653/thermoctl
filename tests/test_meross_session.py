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
    BACKOFF_INITIAL,
    BACKOFF_MAX,
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
async def test_a_rejected_sign_in_backs_off_the_very_next_cycle() -> None:
    """The fault this whole change exists to fix: a rejection used to leave the cache
    empty, so the next cycle (32 seconds later by default) tried again immediately and
    failed the same way -- sixteen such attempts in eight minutes are what actually put
    a real account into `apiStatus=1301, Beyond Login Limit`. No attempt may be made
    again before the backoff set by the rejection has passed, even one second later."""
    # A rate-limit rejection (not a permanent one), so the escalating climb applies
    # rather than jumping straight to `BACKOFF_MAX` -- see the next test for that case.
    transport = _FakeJsonTransport({"apiStatus": 1301, "info": "Beyond Login Limit"})
    cache = MerossSessionCache()

    result = await ensure_transport(_settings_with_credentials(), transport, cache, NOW)

    assert result is None
    assert transport.calls == 1

    result_again = await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + timedelta(seconds=1)
    )

    assert result_again is None
    # No second sign-in attempt was made -- the backoff alone answered `None`.
    assert transport.calls == 1


@pytest.mark.anyio
async def test_the_backoff_lifts_once_it_has_actually_passed() -> None:
    """The gegenprobe for the previous test: the backoff is temporary, not a
    permanent lockout of its own -- once `BACKOFF_INITIAL` has actually elapsed, the
    next cycle is free to try again."""
    transport = _FakeJsonTransport(
        {"apiStatus": 1301, "info": "Beyond Login Limit"}, _SIGN_IN_ANSWER
    )
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)
    assert transport.calls == 1

    result = await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + BACKOFF_INITIAL
    )

    assert isinstance(result, AiomqttCommandTransport)
    assert transport.calls == 2


@pytest.mark.anyio
async def test_a_successful_sign_in_resets_the_backoff() -> None:
    """A working sign-in is proof the account and the cloud-side limit are fine again
    -- a rejection two rounds ago must not keep escalating a backoff nothing has been
    rejected against since."""
    transport = _FakeJsonTransport(
        {"apiStatus": 1301, "info": "Beyond Login Limit"},
        _SIGN_IN_ANSWER,
        {"apiStatus": 1301, "info": "Beyond Login Limit"},
    )
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)
    await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + BACKOFF_INITIAL
    )
    assert cache.next_backoff == BACKOFF_INITIAL

    # A third rejection, right after the successful sign-in -- if the backoff had not
    # been reset, this would already be climbing from a higher step than the first
    # rejection did. `invalidate()` forces a fresh attempt regardless of `SESSION_TTL`
    # so the just-established connection does not simply get reused instead.
    invalidate(cache)
    await ensure_transport(
        _settings_with_credentials(), transport, cache, NOW + BACKOFF_INITIAL
    )
    assert cache.next_backoff == BACKOFF_INITIAL * 2


@pytest.mark.anyio
async def test_a_permanent_login_failure_jumps_straight_to_the_backoff_ceiling() -> None:
    """Wrong credentials do not get less wrong while the process waits -- climbing
    towards `BACKOFF_MAX` one rejection at a time gains nothing a wrong password will
    ever benefit from, so this jumps there on the very first rejection instead."""
    transport = _FakeJsonTransport({"apiStatus": 1004, "info": "Wrong password"})
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)

    assert cache.retry_after == NOW + BACKOFF_MAX
    assert cache.next_backoff == BACKOFF_MAX


@pytest.mark.anyio
async def test_the_rejection_reason_is_remembered_for_the_operator_to_see() -> None:
    """Principle 5: the operator must be able to see why, not just that it failed --
    without going looking in the container log for it."""
    transport = _FakeJsonTransport({"apiStatus": 1301, "info": "Beyond Login Limit"})
    cache = MerossSessionCache()

    await ensure_transport(_settings_with_credentials(), transport, cache, NOW)

    assert cache.last_rejection is not None
    assert "1301" in cache.last_rejection
    assert "Beyond Login Limit" in cache.last_rejection


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
