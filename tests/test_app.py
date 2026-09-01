import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from thermoctl.app import create_app
from thermoctl.logging import request_id_var


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def client_with_user(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    """Like ``client``, but with a schema and a created user.

    The ``client`` fixture above builds the app against a schema-less
    in-memory database -- that is enough for /healthz, static files, and the
    OpenAPI description, none of which touch the database. The login form
    now does: without a single user it redirects to setup, so anyone wanting
    to look at it needs both.
    """
    from sqlalchemy.orm import Session

    from thermoctl.auth.passwords import hash_password
    from thermoctl.config import get_settings
    from thermoctl.db.base import Base
    from thermoctl.db.models.identity import User

    monkeypatch.setenv("THERMOCTL_DATABASE_URL", f"sqlite:///{tmp_path / 'anmeldeseite.db'}")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    get_settings.cache_clear()
    app = create_app()
    Base.metadata.create_all(app.state.engine)
    with Session(app.state.engine) as http_session:
        http_session.add(
            User(
                username="lino",
                display_name="Lino",
                password_hash=hash_password("passwort-lang-genug"),
            )
        )
        http_session.commit()
    yield TestClient(app)
    get_settings.cache_clear()


def test_healthz_responds(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "vorgegeben"})
    assert response.headers["X-Request-ID"] == "vorgegeben"


def test_a_too_long_request_id_is_replaced(client: TestClient) -> None:
    too_long = "a" * 65
    response = client.get("/healthz", headers={"X-Request-ID": too_long})
    assert response.headers["X-Request-ID"] != too_long
    assert response.headers["X-Request-ID"]


def test_a_request_id_with_a_line_break_is_replaced(client: TestClient) -> None:
    with_line_break = "boese\nInjizierte-Zeile: ja"
    response = client.get("/healthz", headers={"X-Request-ID": with_line_break})
    assert "\n" not in response.headers["X-Request-ID"]
    assert response.headers["X-Request-ID"] != with_line_break


def test_a_request_id_with_special_characters_is_replaced(client: TestClient) -> None:
    with_special_characters = "abc$def!"
    response = client.get("/healthz", headers={"X-Request-ID": with_special_characters})
    assert response.headers["X-Request-ID"] != with_special_characters


def test_forbidden_is_answered_globally_as_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global handler for `Forbidden` is the requirement from the closing review:

    A route that does not translate a permission denial into an
    HTTPException itself should still return 403 instead of 500."""
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings
    from thermoctl.domain.authz import Forbidden

    get_settings.cache_clear()
    app = create_app()

    @app.get("/wirft-forbidden")
    async def raises_forbidden() -> None:
        raise Forbidden("Recht fehlt")

    testclient = TestClient(app, raise_server_exceptions=False)
    response = testclient.get("/wirft-forbidden")
    assert response.status_code == 403
    assert "Recht fehlt" in response.text


def test_static_files_are_served(client: TestClient) -> None:
    response = client.get("/static/vendor/bootstrap/bootstrap.min.css")
    assert response.status_code == 200
    response = client.get("/static/vendor/htmx/htmx.min.js")
    assert response.status_code == 200


def test_the_login_page_includes_the_stylesheet(client_with_user: TestClient) -> None:
    response = client_with_user.get("/login")
    assert "/static/vendor/bootstrap/bootstrap.min.css" in response.text


def test_the_login_page_contains_no_navigation_bar(client_with_user: TestClient) -> None:
    response = client_with_user.get("/login")
    assert response.status_code == 200
    assert "<nav" not in response.text


def test_the_header_bar_carries_blur_for_every_browser() -> None:
    """Safari still needs the prefix to this day; without it the bar is
    simply transparent there and the text underneath is hard to read.

    Now lives in the stylesheet instead of as a style attribute in the
    template -- styling does not belong in markup, and as an attribute it
    could neither be overridden nor adapted for the dark scheme.
    """
    from pathlib import Path

    stylesheet = (
        Path(__file__).parent.parent / "thermoctl" / "web" / "static" / "thermoctl.css"
    ).read_text(encoding="utf-8")
    assert "backdrop-filter: blur(" in stylesheet
    assert "-webkit-backdrop-filter: blur(" in stylesheet


def test_lifespan_creates_a_setup_token_when_setup_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lifespan handler only runs on a real start (`with TestClient(...)`),
    not when the app is merely constructed -- which is why this test
    exercises it deliberately through the `with` block instead of the
    `client` fixture. `configure_logging()` replaces the root handlers at
    startup (including `caplog`'s), so the actual log output is checked here
    via `capsys` instead of `caplog`."""
    db_path = tmp_path / "lifespan.db"
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings
    from thermoctl.db.base import Base
    from thermoctl.db.engine import create_engine_from_settings

    get_settings.cache_clear()
    preexisting_engine = create_engine_from_settings(get_settings())
    Base.metadata.create_all(preexisting_engine)
    preexisting_engine.dispose()

    with TestClient(create_app()):
        pass
    output = capsys.readouterr().out
    assert "Einrichtung erforderlich" in output


@pytest.mark.filterwarnings(
    # Scoped narrowly to this one test, not globally: it deliberately lets
    # an exception run through the application. Starlette's error path only
    # releases the in-memory connection once the garbage collector picks it
    # up -- after the test has ended. The application closes its engine
    # properly (see the finally block below); the warning says nothing about
    # the code here.
    "ignore:unclosed database:ResourceWarning"
)
def test_the_request_id_is_reset_after_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    @app.get("/wirft-ausnahme")
    async def raises_an_exception() -> None:
        raise RuntimeError("absichtlicher Fehler fuer den Test")

    initial_value = request_id_var.get()
    testclient = TestClient(app, raise_server_exceptions=False)
    try:
        response = testclient.get("/wirft-ausnahme")
        assert response.status_code == 500
        assert request_id_var.get() == initial_value
    finally:
        # This test builds its own application including its own engine.
        # Without closing it, a database connection stays open and the suite
        # reports a ResourceWarning -- a warning nobody reads any more after
        # the third time.
        testclient.close()
        app.state.engine.dispose()


def test_a_warning_on_a_network_bind_without_secure_cookies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning is the only place this shows up before anything happens."""
    from thermoctl.app import _warn_if_reachable_unprotected
    from thermoctl.config import Settings

    def _settings(host: str, secure: bool) -> Settings:
        return Settings(
            _env_file=None, database_url="sqlite://", secret_key="s" * 32,
            bind_host=host, secure_cookies=secure,
        )

    with caplog.at_level(logging.WARNING, logger="thermoctl.app"):
        caplog.clear()
        _warn_if_reachable_unprotected(_settings("0.0.0.0", False))  # noqa: S104
        assert "SECURE_COOKIES" in caplog.text

        caplog.clear()
        _warn_if_reachable_unprotected(_settings("127.0.0.1", False))
        assert caplog.text == "", "Bound locally is no reason for a warning."

        caplog.clear()
        _warn_if_reachable_unprotected(_settings("0.0.0.0", True))  # noqa: S104
        assert caplog.text == "", "With secure_cookies everything is fine."


def test_swagger_ui_depends_on_no_foreign_address(client: TestClient) -> None:
    """The bundled build pulls its files from a CDN, and the icon from
    `fastapi.tiangolo.com`.

    Both contradict what already applies to Bootstrap and HTMX
    (static/HERKUNFT.md): on a home network without internet access the page
    would stay blank, and every request would tell a third party when
    someone opens the heating control.
    """
    import re

    response = client.get("/docs")
    assert response.status_code == 200
    resources = re.findall(r'(?:src|href)="([^"]+)"', response.text)
    assert resources, "The page includes nothing at all — then the test checks nothing."
    foreign = [r for r in resources if not r.startswith("/")]
    assert not foreign, "These resources come from outside: " + ", ".join(foreign)
    for path in resources:
        assert client.get(path).status_code == 200, path


def test_redoc_is_disabled(client: TestClient) -> None:
    """Removed outright: the same CDN problem, and /docs covers the same description."""
    assert client.get("/redoc").status_code == 404


def test_openapi_knows_the_token_as_a_security_scheme(client: TestClient) -> None:
    """Otherwise there is no login button in the interface (Swagger UI).

    Previously `authorization` appeared on every path as an optional header
    parameter — you would have had to type "Bearer <token>" by hand for
    every single call, and nothing indicated it was the same token.
    """
    description = client.get("/openapi.json").json()
    schemes = description["components"]["securitySchemes"]
    assert schemes == {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "description": "API-Token, ausgestellt unter /tokens",
        }
    }
    zones = description["paths"]["/api/v1/zones"]["get"]
    assert zones["security"] == [{"HTTPBearer": []}]
    assert "parameters" not in zones, (
        "The token header must not also appear as an ordinary parameter."
    )


def test_openapi_describes_only_the_interface(client: TestClient) -> None:
    """The description is the contract of the REST interface, not a dump of
    every route.

    Without this separation, /docs would list the HTML form paths alongside
    every real endpoint in the interface — and clicking 'Try it out' on
    `POST /benutzer/{id}/aktiv` would really deactivate a user.
    """
    paths = client.get("/openapi.json").json()["paths"]
    foreign = sorted(p for p in paths if not p.startswith("/api/") and p != "/healthz")
    assert not foreign, "These paths do not belong in the description: " + ", ".join(foreign)
    assert any(p.startswith("/api/") for p in paths), "It contains nothing at all."


def test_openapi_explains_both_control_gates_and_the_missing_actuator(
    client: TestClient,
) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/control/armed"]["put"]
    description = " ".join(operation["description"].split())
    assert "first stage" in description
    assert "not released until a restart" in description
    assert "do not reach an actuator" in description


def test_starting_against_an_empty_database_reports_the_missing_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case that made it into production: database file moved, service
    started, sixty lines of traceback with `no such table: user` at its
    core. The container image migrates in its entrypoint, a local `uvicorn`
    start does not -- that is exactly where it happens. What is checked is
    the log line, not just the exception: the exception alone tells the
    operator nothing once it disappears under the traceback."""
    from thermoctl.db.schema_state import COMMAND, SchemaMismatch

    monkeypatch.setenv("THERMOCTL_DATABASE_URL", f"sqlite:///{tmp_path / 'leer.db'}")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(SchemaMismatch), TestClient(create_app()):
            pass
        output = capsys.readouterr().out
        assert COMMAND in output
        assert "no such table" not in output
    finally:
        get_settings.cache_clear()
