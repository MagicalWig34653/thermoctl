"""Viele gleichzeitige Anmeldungen leeren den Datenbank-Pool nicht (Karte 7C9B8703).

Die Anmeldung läuft auf der Ereignisschleife -- derselben, die die Heizung regelt. Behielt sie
ihre Verbindung bis nach der Antwort (`get_session` committet erst dann, über einen Arbeitsthread),
so warteten die nächsten Anmeldungen **blockierend** auf den Pool, mitten auf der Schleife: Die
Commits, die Verbindungen zurückgegeben hätten, kamen nicht mehr an die Reihe. Der ganze Dienst
stand bis zur Zeitgrenze des Pools (30 s), die Anfragen endeten in 500.

Die Regel, die hier geprüft wird: Eine Anmeldung hält über keinen `await` hinweg eine Verbindung.
"""

import asyncio
import base64
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

import thermoctl.db.engine as engine_module
import thermoctl.web.shared.login as login
from thermoctl.app import create_app
from thermoctl.auth.passwords import hash_password
from thermoctl.config import Settings, get_settings
from thermoctl.db.engine import session_factory
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.passkey import PasskeyChallenge
from thermoctl.web.shared.login import FEHLVERSUCHE, attempt_login

FALSCH = "falsch-aber-lang"
RICHTIG = "passwort-lang-genug"
POOL_TIMEOUT_S = 3
NAME = "pool-konto"


@pytest.fixture(autouse=True)
def _clean_counters() -> Any:
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()
    yield
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()


@pytest.fixture
def small_pool(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Ein eigener Pool wie im Betrieb, absichtlich winzig: zwei Verbindungen, 3 s Zeitgrenze,
    auf beiden Datenbanken. Hier wird wirklich committet, deshalb räumt die Fixture auf."""
    create = engine_module.create_engine

    def tiny(url: str, **options: Any) -> Engine:
        options.update(pool_size=2, max_overflow=0, pool_timeout=POOL_TIMEOUT_S)
        return create(url, **options)

    monkeypatch.setattr(engine_module, "create_engine", tiny)
    own = engine_module.create_engine_from_settings(settings)
    monkeypatch.setattr(engine_module, "create_engine", create)
    with Session(own) as setup:
        setup.add(User(username=NAME, display_name=NAME, password_hash=hash_password(RICHTIG)))
        setup.commit()
    try:
        yield own
    finally:
        with Session(own) as teardown:
            teardown.execute(delete(Session_))
            teardown.execute(delete(AuditEvent))
            teardown.execute(delete(PasskeyChallenge))
            teardown.execute(delete(User).where(User.username == NAME))
            teardown.commit()
        own.dispose()


def _application(settings: Settings, pool: Engine, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Die Anwendung mit echten Sitzungen je Anfrage -- ohne die Übersteuerung der Tests."""
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    app = create_app()
    app.state.engine = pool
    app.state.session_factory = session_factory(pool)
    return app


def test_a_flood_of_different_names_is_answered_without_running_the_pool_dry(
    settings: Settings, small_pool: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E: die ganze Anwendung, echte Sitzungen je Anfrage, 20 Anmeldungen auf einmal."""
    app = _application(settings, small_pool, monkeypatch)

    async def short_wait(seconds: float) -> None:
        await asyncio.sleep(0.05)  # gibt die Schleife wirklich frei, wie die echte Wartezeit

    monkeypatch.setattr(login, "sleep", short_wait)

    async def scenario() -> tuple[list[int], float]:
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
        longest_stall = 0.0
        done = asyncio.Event()

        async def heartbeat() -> None:
            """Steht für die Regelschleife: Wie lange kam sonst niemand an die Reihe?"""
            nonlocal longest_stall
            loop = asyncio.get_running_loop()
            last = loop.time()
            while not done.is_set():
                await asyncio.sleep(0.01)
                longest_stall = max(longest_stall, loop.time() - last)
                last = loop.time()

        async def post(name: str, password: str) -> int:
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/ui-api/login", json={"username": name, "password": password}
                )
            return response.status_code

        beat = asyncio.create_task(heartbeat())
        names = [(f"erfunden-{number}", FALSCH) for number in range(20)]
        names[10] = names[11] = (NAME, RICHTIG)
        statuses = await asyncio.gather(*(post(*entry) for entry in names))
        done.set()
        await beat
        return list(statuses), longest_stall

    try:
        statuses, longest_stall = asyncio.run(asyncio.wait_for(scenario(), timeout=240))
    finally:
        get_settings.cache_clear()

    assert sorted(set(statuses)) == [200, 401], statuses
    assert statuses[10:12] == [200, 200]
    assert statuses.count(401) == 18
    assert longest_stall < POOL_TIMEOUT_S - 0.5
    assert small_pool.pool.checkedout() == 0  # type: ignore[attr-defined]


def _client_data(challenge: str) -> str:
    data = json.dumps({"type": "webauthn.get", "challenge": challenge}).encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/passkey/authentication/options", None, 200),
        # Nie ausgegebene Challenge: wird in der Datenbank nachgeschlagen und abgewiesen.
        (
            "/passkey/authentication/verify",
            {"response": {"clientDataJSON": _client_data("nie-ausgegeben")}},
            401,
        ),
    ],
)
def test_a_flood_of_passkey_logins_does_not_run_the_pool_dry(
    settings: Settings,
    small_pool: Engine,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    body: dict[str, Any] | None,
    status: int,
) -> None:
    """E2E: Die Passkey-Anmeldung steht jedem offen und hält dieselbe Regel."""
    monkeypatch.setenv("THERMOCTL_PASSKEY_RP_ID", "test")
    app = _application(settings, small_pool, monkeypatch)

    async def scenario() -> list[int]:
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)

        async def post() -> int:
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                return (await client.post(path, json=body)).status_code

        return list(await asyncio.gather(*(post() for _ in range(20))))

    try:
        statuses = asyncio.run(asyncio.wait_for(scenario(), timeout=240))
    finally:
        get_settings.cache_clear()

    assert statuses == [status] * 20
    assert small_pool.pool.checkedout() == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "password"),
    [("lino", RICHTIG), ("lino", FALSCH), ("erfunden", FALSCH)],
)
def test_a_login_returns_its_connection_before_it_returns(
    session: Session, user: User, name: str, password: str
) -> None:
    """Gelingt sie oder nicht: Nach `attempt_login` ist keine Transaktion mehr offen -- zwischen
    Antwort und Aufräumen der Anfrage liegt nichts mehr aus dem Pool."""
    request: Any = SimpleNamespace(headers={}, client=None)

    asyncio.run(attempt_login(request, session, name, password))

    assert not session.in_transaction()
