import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from tests.helpers import source, user_with_permissions
from thermoctl.app import create_app
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.passwords import hash_password
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings
from thermoctl.db.models.identity import AccessGroup, User, UserAccessGroup
from thermoctl.db.models.lookup import ACTOR_SOURCES, PERMISSIONS, ActorSource, Permission

TEST_DATABASE_URL = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "sqlite:///./test.db")


def _migrationsdatenbank_url(basis_url: str) -> str:
    """Derives the database for the migration tests from ``TEST_DATABASE_URL``.

    The migration tests run ``alembic upgrade``/``downgrade`` against their **own**
    database, separate from the ``engine`` fixture: otherwise ``Base.metadata.create_all()``
    would create the same tables Alembic also wants to create, and the migration would fail
    on a table that already exists. Deriving it from ``TEST_DATABASE_URL`` instead of a
    second configuration ensures the migration tests never unknowingly run against a
    different database than the rest of the suite.
    """
    url = make_url(basis_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            # An in-memory database belongs to exactly one process anyway. The
            # migration tests run as their own subprocess and therefore get
            # their own, empty database — a derived URL would be moot here.
            return basis_url
        pfad = Path(url.database)
        new_path = pfad.with_name(f"{pfad.stem}-migrations{pfad.suffix}")
        return url.set(database=str(new_path)).render_as_string(hide_password=False)
    return url.set(database=f"{url.database}_migrations").render_as_string(hide_password=False)


MIGRATIONS_DATABASE_URL = _migrationsdatenbank_url(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, secret_key="t" * 32
    )


@pytest.fixture(scope="session", autouse=True)
def _environment_for_the_whole_session(settings: Settings) -> Iterator[None]:
    """Sets the required variables for the entire run and cuts off the `.env` file.

    Every test that calls `create_app()` needs them — `Settings` requires
    `database_url` and `secret_key`. The ``client`` fixture has so far set them
    itself, but the guards in `test_endpunktabdeckung.py` and `test_csrf.py`
    enumerate routes without building a client.

    This worked so far because `get_settings` caches, and by chance a valid
    entry from the previous test still sat in the cache. Once the guard was
    sorted to the end of the run, none was left there any more — and locally
    it still did not show up, because the project directory has a `.env`
    file that pydantic reads on its own. CI has none. A test must not depend
    on who ran before it and which files happen to lie around.

    The second attempt at the same bug: back then the two required variables
    were set, but the `.env` file kept being read. Anyone who later entered
    something there -- a passkey id, say -- saw tests turn red that had
    nothing to do with their change. The more dangerous case is the reverse:
    a setting that is set locally and missing in CI makes tests look green
    that will fail there. `THERMOCTL_ENV_FILE=""` cuts off the file; the
    suite only ever sees what it sets itself.
    """
    marker = pytest.MonkeyPatch()
    marker.setenv("THERMOCTL_ENV_FILE", "")
    marker.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    marker.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    yield
    marker.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrations_database_url() -> Iterator[str]:
    """Makes sure the migrations database exists, and returns its URL.

    Under MariaDB, the schema for the migration tests does not yet exist
    before the first run — it is created here itself via
    ``CREATE DATABASE IF NOT EXISTS``. Under SQLite, the file URL creates the
    database automatically on the first connection, so there is nothing to
    prepare here.
    """
    ziel_url = make_url(MIGRATIONS_DATABASE_URL)
    if ziel_url.get_backend_name() != "sqlite":
        server_url = URL.create(
            ziel_url.drivername,
            username=ziel_url.username,
            password=ziel_url.password,
            host=ziel_url.host,
            port=ziel_url.port,
        )
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as db_connection:
                db_connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{ziel_url.database}`"))
                db_connection.commit()
        finally:
            server_werk.dispose()

    yield MIGRATIONS_DATABASE_URL

    # Symmetric to the `engine` fixture, which removes its tables again: if the
    # migrations database were left lying around, the next run would run
    # against an old schema state and fail on something that has nothing to
    # do with the code.
    if ziel_url.get_backend_name() == "sqlite":
        if ziel_url.database and ziel_url.database != ":memory:":
            Path(ziel_url.database).unlink(missing_ok=True)
    else:
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as db_connection:
                db_connection.execute(text(f"DROP DATABASE IF EXISTS `{ziel_url.database}`"))
                db_connection.commit()
        finally:
            server_werk.dispose()


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    werk = create_engine_from_settings(settings)
    Base.metadata.drop_all(werk)
    Base.metadata.create_all(werk)
    # The audit sources belong to the schema just like the permissions: the migration
    # creates them in every real database. Previously each test created the one it
    # happened to need, and anyone who forgot one hit an IntegrityError on
    # `audit_event.source_id` -- a message that names the cause nowhere. Since the
    # source is passed through from the adapter (web, api, mcp), almost every
    # writing test needs more than one.
    #
    # Unlike the permissions, this is fine to seed here: no test creates an
    # ActorSource by hand, so there is no UNIQUE collision like with `Permission`.
    with Session(werk) as http_session:
        existing = {q.code for q in http_session.query(ActorSource)}
        for code, label in ACTOR_SOURCES:
            if code not in existing:
                http_session.add(ActorSource(code=code, label=label))
        http_session.commit()
    yield werk
    Base.metadata.drop_all(werk)
    werk.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Every test runs in a transaction that is rolled back afterward.

    The session joins the outer transaction via a savepoint
    (``join_transaction_mode="create_savepoint"``). If a test deliberately
    triggers an error (e.g. an ``IntegrityError`` on a constraint violation)
    and the session rolls back because of it, that only affects the
    savepoint — the outer transaction survives and can still be rolled back
    in teardown.

    Limit of this isolation: data changes are rolled back, not the counter
    for auto-increment keys — and under MariaDB, DDL triggers an implicit
    commit. Tests must therefore not rely on specific id values and must not
    make schema changes. The cost of this approach is a deliberate choice:
    building the schema anew for every test would be unbearably slow under
    MariaDB.

    This way, all tests share one schema without affecting each other —
    under MariaDB, rebuilding it per test would otherwise be noticeably slow.
    """
    db_connection = engine.connect()
    transaktion = db_connection.begin()
    http_session = Session(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield http_session
    finally:
        http_session.close()
        transaktion.rollback()
        db_connection.close()


@pytest.fixture(autouse=True)
def _permissions_for_setup_wizard(
    request: pytest.FixtureRequest, session: Session
) -> None:
    """Seeds the permission table within the test transaction of ``tests/test_setup.py``.

    In production, every code from `Permission` already exists through the
    migration `3685e30419a4_nachschlagetabellen`, before the setup wizard
    ever runs. `Base.metadata.create_all()` in the ``engine`` fixture, by
    contrast, only creates the schema, no reference data — without these
    rows, `einrichtung_durchfuehren()` would fail with a `KeyError`, because
    it assigns existing permissions to the example groups instead of
    creating them itself.

    Deliberately not seedable in the session-wide ``engine`` fixture: there,
    the rows would be visible for the entire test session and would make
    `test_lookup.py`'s `test_permission_knows_its_scope` fail on the UNIQUE
    constraint on `code`, since it deliberately creates a fresh
    `Permission("zone.read")`. Hence this happens per test here, limited to
    `test_setup.py`, and through the same session as the test itself — the
    rows disappear again with its rollback.
    """
    if request.node.fspath.basename != "test_setup.py":
        return
    existing = {p.code for p in session.query(Permission)}
    for code, description, zone_scoped in PERMISSIONS:
        if code not in existing:
            session.add(Permission(code=code, description=description,
                                   is_zone_scoped=zone_scoped))
    session.flush()


@pytest.fixture
def client(
    settings: Settings, session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Builds the app against the test database and runs requests in the
    same per-test, rolled-back transaction as the ``session`` fixture —
    otherwise a test would not see what an operation triggered over HTTP
    had written.
    """
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    app = create_app()

    def _session_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def client_with_prefix(
    settings: Settings, session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Like ``client``, but the app is built as if served behind a reverse-proxy
    prefix (``THERMOCTL_ROOT_PATH``) -- the Home Assistant Ingress case.

    Requests against this client are still made against the *bare*, un-prefixed
    paths (``/login``, not ``/api/hassio_ingress/.../login``): that mirrors how
    Ingress actually reaches the container -- Home Assistant strips its own prefix
    before proxying the request on, so the path the app receives never carries it.
    The prefix only has to show up in what the app *generates*: redirects, cookie
    scope, and every link a rendered page carries -- which is exactly what the tests
    using this fixture check.
    """
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    monkeypatch.setenv("THERMOCTL_ROOT_PATH", "/api/hassio_ingress/A1b2C3d4e5")
    get_settings.cache_clear()
    app = create_app()

    def _session_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def client_als(
    client: TestClient, session: Session
) -> Callable[[list[tuple[str, int | None]]], TestClient]:
    counter = 0

    def _client_als(permissions: list[tuple[str, int | None]]) -> TestClient:
        nonlocal counter
        counter += 1
        user_record = user_with_permissions(session, f"web-{counter}", permissions)
        _http_session, secret = create_session(session, user_record, 3600)
        client.cookies.set(COOKIE_NAME, secret)
        return client

    return _client_als


@pytest.fixture
def user(session: Session) -> User:
    """Creates the user ``lino`` with a hashed password and the *Verwaltung* group."""
    source(session, "web")
    user_record = User(
        username="lino",
        display_name="Lino",
        password_hash=hash_password("passwort-lang-genug"),
    )
    session.add(user_record)
    session.flush()
    group = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(group)
    session.flush()
    session.add(UserAccessGroup(user_id=user_record.id, access_group_id=group.id))
    session.flush()
    return user_record

@pytest.fixture(autouse=True)
def _without_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The login throttle does not actually sleep while tests run.

    The throttling itself stays active and is checked by
    `test_failed_attempts_are_increasingly_delayed` — that test replaces
    `sleep` itself and therefore never sees this fixture at all. Without
    it, every login in every test costs real seconds: the suite's runtime
    went from two seconds to thirty-three because of this, and a slow suite
    gets run less often.
    """
    async def ohne_wartezeit(seconds: float) -> None:
        return None

    monkeypatch.setattr("thermoctl.web.auth_views.sleep", ohne_wartezeit)

@pytest.fixture
def angemeldeter_client(
    client_als: Callable[[list[tuple[str, int | None]]], TestClient],
) -> TestClient:
    """A client with every permission, for the smoke test across all pages.

    Deliberately with the full permission set: the smoke test asks whether a
    page exists at all and responds without an error — whether it checks
    permissions correctly belongs in that view's own tests.
    """
    return client_als(
        [
            ("zone.read", None),
            ("zone.manage", None),
            ("device.read", None),
            ("device.manage", None),
            ("user.manage", None),
            ("group.manage", None),
            ("token.self", None),
            ("token.manage", None),
            ("audit.read", None),
            ("setting.manage", None),
            ("mode.manage", None),
            ("setpoint.write", None),
            ("schedule.manage", None),
            ("control.arm", None),
        ]
    )

def pytest_configure(config: pytest.Config) -> None:
    """Collection point for the endpoints actually called during the run —
    evaluated by tests/test_endpunktabdeckung.py."""
    config._called_endpoints = set()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _record_endpoints(request: pytest.FixtureRequest) -> Iterator[None]:
    """Records every HTTP call a test makes through the TestClient."""
    from starlette.testclient import TestClient as _TestClient

    original = _TestClient.request
    gesammelt = request.config._called_endpoints  # type: ignore[attr-defined]

    def aufzeichnend(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        pfad = str(url).split("?")[0]
        for prefix in ("http://testserver", "https://testserver"):
            if pfad.startswith(prefix):
                pfad = pfad[len(prefix) :]
        gesammelt.add((str(method).upper(), pfad or "/"))
        return original(self, method, url, *args, **kwargs)

    _TestClient.request = aufzeichnend  # type: ignore[method-assign]
    try:
        yield
    finally:
        _TestClient.request = original  # type: ignore[method-assign]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Moves endpoint coverage to the end of the run.

    It evaluates the record of every HTTP call made during the run. Sorted
    by filename, it would run in the middle of the run — it would then only
    see what had been called up to that point, and report everything later
    as unchecked. As long as the guard ran into nothing anyway because of
    FastAPI's nested routers, this went unnoticed.
    """
    items.sort(key=lambda item: item.fspath.basename == "test_endpoint_coverage.py")
