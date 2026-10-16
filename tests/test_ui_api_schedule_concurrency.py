"""Zwei gleichzeitige Zeitplan-Gesten mit derselben Version: genau eine gelingt.

Das braucht **echte** Transaktionen auf mehreren Verbindungen -- die übliche Testsitzung
(eine Verbindung, am Ende zurückgerollt) kann ein Überholen nicht zeigen. Deshalb eine eigene
Datenbank neben der Testdatenbank, mit festgeschriebenen Daten und einer Sitzung je Anfrage
wie im Betrieb.

Nur gegen MariaDB (REPEATABLE READ, die Anlage läuft darauf): SQLite serialisiert Schreiber
ohnehin über die Datei, und `FOR UPDATE` gibt es dort nicht -- dieser Lauf würde nichts
beweisen. Die Logik selbst (Sperre, Versionsprüfung, 409) läuft in den übrigen Zeitplan-
Tests auf beiden Datenbanken.
"""

import threading
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import TEST_DATABASE_URL
from tests.helpers import create_zone, source, user_with_permissions, zone_with_schedule
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import get_settings
from thermoctl.db.base import Base
from thermoctl.db.models.zone import SetpointMode
from thermoctl.web import ui_api_schedule

pytestmark = pytest.mark.skipif(
    make_url(TEST_DATABASE_URL).get_backend_name() == "sqlite",
    reason="Nur MariaDB (REPEATABLE READ) zeigt das Überholen; SQLite serialisiert Schreiber "
    "über die Datei und kennt kein FOR UPDATE.",
)

PERMISSIONS: list[tuple[str, int | None]] = [("zone.read", None), ("schedule.manage", None)]


@pytest.fixture
def race_engine() -> Iterator[Engine]:
    ziel = make_url(TEST_DATABASE_URL)
    name = f"{ziel.database}_race"
    server = create_engine(
        URL.create(
            ziel.drivername,
            username=ziel.username,
            password=ziel.password,
            host=ziel.host,
            port=ziel.port,
        ),
        future=True,
    )
    with server.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        connection.execute(text(f"CREATE DATABASE `{name}`"))
        connection.commit()
    engine = create_engine(ziel.set(database=name), future=True, pool_size=8)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with server.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
            connection.commit()
        server.dispose()


@pytest.fixture
def world(
    race_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Callable[[], TestClient], int, int, int]]:
    """(Client-Fabrik, Zone, Zielzone, Modus-Id); alles festgeschrieben."""
    settings = get_settings()
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", str(race_engine.url.render_as_string(False)))
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    factory = sessionmaker(race_engine, expire_on_commit=False)
    with factory() as setup:
        source(setup, "web")
        zone = zone_with_schedule(
            setup,
            "wohnzimmer",
            [(1, 420, "tag", Decimal("21.0")), (1, 1320, "nacht", Decimal("17.0"))],
        )
        target = create_zone(setup, "bad")
        user = user_with_permissions(setup, "race", PERMISSIONS)
        _row, secret = create_session(setup, user, 3600)
        mode = setup.query(SetpointMode).filter_by(code="tag").one().id
        setup.commit()
        ids = (zone.id, target.id, mode)
    app = create_app()

    def _session() -> Iterator[Session]:
        http_session = factory()
        try:
            yield http_session
            http_session.commit()
        except Exception:
            http_session.rollback()
            raise
        finally:
            http_session.close()

    app.dependency_overrides[get_session] = _session

    def make_client() -> TestClient:
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, secret)
        return client

    yield (make_client, *ids)
    get_settings.cache_clear()


def _headers(client: TestClient) -> dict[str, str]:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    return {CSRF_HEADER: csrf_token(cookie, get_settings().secret_key.get_secret_value())}


def _race(
    make_client: Callable[[], TestClient],
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    url: str,
    body: dict[str, Any],
) -> list[Any]:
    """Zwei Anfragen zugleich. Vor dem eigentlichen Schreiben (`gate`) wartet jede kurz auf
    die andere: Ohne Sperre erreichen beide diese Stelle mit derselben Version und schreiben
    beide; mit Sperre kommt die zweite gar nicht bis dorthin (die erste läuft nach der
    Wartezeit allein weiter)."""
    real = getattr(ui_api_schedule, gate)
    barrier = threading.Barrier(2, timeout=3)

    def gated(*args: Any, **kwargs: Any) -> Any:
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real(*args, **kwargs)

    monkeypatch.setattr(ui_api_schedule, gate, gated)
    answers: list[Any] = []

    def go() -> None:
        client = make_client()
        answers.append(client.post(url, json=body, headers=_headers(client)))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(answers) == 2
    return answers


def _one_wins(answers: list[Any], code: str) -> None:
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    loser = next(a for a in answers if a.status_code == 409)
    assert loser.json()["code"] == code


def test_two_paints_with_the_same_version_one_wins(
    world: tuple[Callable[[], TestClient], int, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_client, zone, _target, mode = world
    body = {"version": "0", "weekday": 2, "start_minute": 600, "end_minute": 720, "mode_id": mode}
    answers = _race(
        make_client, monkeypatch, "paint_schedule_interval", f"/ui-api/schedule/{zone}/paint", body
    )
    _one_wins(answers, "version_conflict")


def test_two_day_copies_with_the_same_version_one_wins(
    world: tuple[Callable[[], TestClient], int, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_client, zone, _target, _mode = world
    body = {"version": "0", "weekday": 1, "scope": "workdays"}
    answers = _race(
        make_client, monkeypatch, "copy_schedule_day", f"/ui-api/schedule/{zone}/copy-day", body
    )
    _one_wins(answers, "version_conflict")


def test_two_week_copies_with_the_same_versions_one_wins(
    world: tuple[Callable[[], TestClient], int, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_client, zone, target, _mode = world
    body = {"version": "0", "targets": [{"zone_id": target, "version": "0"}]}
    answers = _race(
        make_client, monkeypatch, "adopt_schedule", f"/ui-api/schedule/{zone}/copy-week", body
    )
    _one_wins(answers, "version_conflict")


def test_two_undos_with_the_same_token_one_wins(
    world: tuple[Callable[[], TestClient], int, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_client, zone, _target, mode = world
    client = make_client()
    painted = client.post(
        f"/ui-api/schedule/{zone}/paint",
        json={
            "version": "0",
            "weekday": 2,
            "start_minute": 600,
            "end_minute": 720,
            "mode_id": mode,
        },
        headers=_headers(client),
    )
    assert painted.status_code == 200, painted.text
    body = {"undo_token": painted.json()["undo_token"]}
    answers = _race(
        make_client, monkeypatch, "undo_schedule_gesture", f"/ui-api/schedule/{zone}/undo", body
    )
    _one_wins(answers, "undo_stale")
