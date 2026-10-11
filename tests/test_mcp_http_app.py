"""HTTP-Weg und Betriebsarten des MCP-Servers."""

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import issue_token
from thermoctl.config import Settings
from thermoctl.mcp.http_app import create_mcp_asgi_app


class _SharedSession:
    def __init__(self, session: Session) -> None:
        self.session = session

    def __call__(self):
        return self

    def __getattr__(self, name: str):
        if name == "close":
            return lambda: None
        return getattr(self.session, name)


def _settings(mode: str = "path") -> Settings:
    values = {"mcp_mode": mode, "public_url": "https://thermo.example.invalid"}
    if mode == "port":
        values.update(mcp_port=9000, mcp_public_url="https://mcp.example.invalid:9443")
    return Settings(_env_file=None, database_url="sqlite://", secret_key="x" * 32, **values)


def _token(session: Session) -> str:
    owner = user_with_permissions(session, "http-mcp", [("zone.read", None)])
    _row, plaintext = issue_token(session, owner, "mcp", [("zone.read", None)], None)
    return plaintext


def _rpc(method: str, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}}


def test_path_http_protocol_and_oauth_metadata(session: Session) -> None:
    settings = _settings()
    app = create_mcp_asgi_app(settings, _SharedSession(session))
    headers = {
        "Authorization": f"Bearer {_token(session)}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
        "Host": "thermo.example.invalid",
    }
    with TestClient(app, base_url="https://thermo.example.invalid") as client:
        metadata = client.get("/.well-known/oauth-protected-resource")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == settings.mcp_base_url()
        response = client.post("/", json=_rpc("initialize"), headers=headers)
        assert response.status_code == 200
        response = client.post("/", json=_rpc("tools/list", 2), headers=headers)
        assert response.status_code == 200
        assert len(response.json()["result"]["tools"]) == 25
        no_token = client.post(
            "/", json=_rpc("tools/list"), headers={"Host": "thermo.example.invalid"}
        )
        assert no_token.status_code == 401
        assert (
            'resource_metadata="https://thermo.example.invalid/.well-known/oauth-protected-resource/mcp"'
            in no_token.headers["www-authenticate"]
        )
        assert (
            client.post(
                "/", json=_rpc("tools/list"), headers=headers | {"Host": "evil.invalid"}
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/", json=_rpc("tools/list"), headers=headers | {"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )


def test_port_metadata_at_root(session: Session) -> None:
    settings = _settings("port")
    app = create_mcp_asgi_app(settings, _SharedSession(session))
    with TestClient(app, base_url="https://mcp.example.invalid:9443") as client:
        assert (
            client.get("/.well-known/oauth-authorization-server").json()["issuer"]
            == settings.mcp_base_url()
        )
        assert (
            client.get("/.well-known/oauth-protected-resource").json()["resource"]
            == settings.mcp_base_url()
        )
        assert client.post("/", json=_rpc("initialize")).status_code == 401
        headers = {
            "Authorization": f"Bearer {_token(session)}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
            "Host": "mcp.example.invalid:9443",
        }
        response = client.post("/", json=_rpc("tools/list"), headers=headers)
        assert response.status_code == 200
        assert len(response.json()["result"]["tools"]) == 25


def test_web_mount_answers_exact_path_without_redirect(
    session: Session, engine, monkeypatch
) -> None:
    from thermoctl import app as web_app

    settings = _settings()
    monkeypatch.setattr(web_app, "get_settings", lambda: settings)
    monkeypatch.setattr(web_app, "create_engine_from_settings", lambda _settings: engine)
    monkeypatch.setattr(web_app, "session_factory", lambda _engine: _SharedSession(session))
    monkeypatch.setattr(web_app, "check_schema", lambda _engine: None)
    monkeypatch.setattr(web_app, "setup_needed", lambda _session: False)
    app = web_app.create_app()
    with TestClient(app, base_url="https://thermo.example.invalid") as client:
        metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == settings.mcp_base_url()
        response = client.post("/mcp", json=_rpc("tools/list"))
        assert response.status_code == 401
        assert "location" not in response.headers
        headers = {
            "Authorization": f"Bearer {_token(session)}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
            "Host": "thermo.example.invalid",
        }
        response = client.post("/mcp", json=_rpc("tools/list"), headers=headers)
        assert response.status_code == 200
        assert len(response.json()["result"]["tools"]) == 25


def test_off_mode_has_no_mcp_route(monkeypatch) -> None:
    from thermoctl import app as web_app

    monkeypatch.setattr(web_app, "get_settings", lambda: _settings("off"))
    app = web_app.create_app()
    client = TestClient(app)
    assert client.get("/mcp").status_code == 404
    assert client.post("/mcp").status_code == 404


def test_invalid_web_configuration_fails_before_start(monkeypatch) -> None:
    from thermoctl import app as web_app

    settings = _settings()
    settings.public_url = None
    monkeypatch.setattr(web_app, "get_settings", lambda: settings)
    import pytest

    with pytest.raises(ValueError, match="THERMOCTL_PUBLIC_URL"):
        web_app.create_app()


def test_concurrent_tool_calls_keep_their_token(tmp_path) -> None:
    import concurrent.futures

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session, sessionmaker

    from thermoctl.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=30000")

    Base.metadata.create_all(engine)
    settings = _settings("port")
    with Session(engine) as db:
        allowed = user_with_permissions(db, "http-allowed", [("zone.read", None)])
        denied = user_with_permissions(db, "http-denied", [])
        _allowed_row, allowed_plain = issue_token(
            db, allowed, "allowed", [("zone.read", None)], None
        )
        _denied_row, denied_plain = issue_token(db, denied, "denied", [], None)
        db.commit()
    app = create_mcp_asgi_app(settings, sessionmaker(engine))
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
        "Host": "mcp.example.invalid:9443",
    }
    with TestClient(app, base_url="https://mcp.example.invalid:9443") as client:

        def call(plaintext: str) -> dict:
            return client.post(
                "/",
                json={**_rpc("tools/call"), "params": {"name": "list_zones", "arguments": {}}},
                headers=headers | {"Authorization": f"Bearer {plaintext}"},
            ).json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(call, [allowed_plain, denied_plain]))
    assert not responses[0]["result"]["isError"]
    assert responses[1]["result"]["isError"]
    engine.dispose()


def test_missing_sdk_only_removes_mcp_web_routes(monkeypatch, capsys) -> None:
    from thermoctl import app as web_app

    settings = _settings()
    monkeypatch.setattr(web_app, "get_settings", lambda: settings)
    from thermoctl.mcp import http_app

    def missing(_settings, _factory):
        raise RuntimeError("Das optionale MCP-Paket fehlt")

    monkeypatch.setattr(http_app, "create_mcp_asgi_app", missing)
    app = web_app.create_app()
    assert app.state.mcp_http_app is None
    assert TestClient(app).post("/mcp").status_code == 404
    assert "mcp.http.unavailable" in capsys.readouterr().out


def test_http_cli_needs_port_mode_and_does_not_read_stdio_token(monkeypatch) -> None:
    import sys

    import pytest

    from thermoctl.mcp import server

    monkeypatch.setattr(sys, "argv", ["thermoctl-mcp", "--http"])
    monkeypatch.setattr(server, "get_settings", lambda: _settings("path"))
    with pytest.raises(SystemExit, match="Modus path|Modus path|path läuft|im Modus path"):
        server.main()

    settings = _settings("port")
    settings.mcp_token = None
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "create_engine_from_settings", lambda _settings: object())
    monkeypatch.setattr(server, "session_factory", lambda _engine: object())
    import uvicorn

    called = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: called.update(host=host, port=port))

    # Das Test-Engine-Doppel muss für den finally-Block ebenfalls entsorgt werden können.
    class EngineStub:
        def dispose(self):
            pass

    monkeypatch.setattr(server, "create_engine_from_settings", lambda _settings: EngineStub())
    server.main()
    assert called == {"host": settings.bind_host, "port": settings.mcp_port}


def test_http_cli_rejects_invalid_port_configuration(monkeypatch) -> None:
    import sys

    import pytest

    from thermoctl.mcp import server

    settings = _settings("port")
    settings.mcp_port = None
    monkeypatch.setattr(sys, "argv", ["thermoctl-mcp", "--http"])
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PORT"):
        server.main()


def _main_with(monkeypatch, **env: str) -> None:
    from thermoctl.config import get_settings

    for key in (
        "THERMOCTL_MCP_MODE",
        "THERMOCTL_MCP_PORT",
        "THERMOCTL_PUBLIC_URL",
        "THERMOCTL_MCP_PUBLIC_URL",
        "THERMOCTL_MCP_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["thermoctl-mcp", "--http"])
    get_settings.cache_clear()


def test_http_main_refuses_other_modes(monkeypatch) -> None:
    from thermoctl.mcp import server

    _main_with(
        monkeypatch, THERMOCTL_MCP_MODE="path", THERMOCTL_PUBLIC_URL="https://t.example.invalid"
    )
    with pytest.raises(SystemExit, match="THERMOCTL_MCP_MODE=port"):
        server.main()


def test_http_main_refuses_incomplete_port_configuration(monkeypatch) -> None:
    from thermoctl.mcp import server

    _main_with(
        monkeypatch, THERMOCTL_MCP_MODE="port", THERMOCTL_PUBLIC_URL="https://t.example.invalid"
    )
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PORT"):
        server.main()


def test_http_main_serves_the_app_on_the_configured_port(monkeypatch) -> None:
    from thermoctl.mcp import server

    _main_with(
        monkeypatch,
        THERMOCTL_MCP_MODE="port",
        THERMOCTL_PUBLIC_URL="https://t.example.invalid",
        THERMOCTL_MCP_PORT="9123",
        THERMOCTL_MCP_PUBLIC_URL="https://mcp.example.invalid",
        THERMOCTL_BIND_HOST="127.0.0.1",
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    server.main()
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9123)
    assert seen["app"] is not None


@pytest.mark.parametrize(
    "url,accepted,rejected",
    [
        (
            "https://mcp.example.invalid:443",
            ("mcp.example.invalid", "mcp.example.invalid:443"),
            "evil.invalid",
        ),
        (
            "https://mcp.example.invalid:9443",
            ("mcp.example.invalid:9443",),
            "mcp.example.invalid",
        ),
        (
            "http://mcp.example.invalid:80",
            ("mcp.example.invalid", "mcp.example.invalid:80"),
            "evil.invalid",
        ),
    ],
)
def test_transport_hosts_follow_public_port(
    session: Session, url: str, accepted: tuple[str, ...], rejected: str
) -> None:
    settings = _settings("port")
    settings.mcp_public_url = url
    app = create_mcp_asgi_app(settings, _SharedSession(session))
    headers = {
        "Authorization": f"Bearer {_token(session)}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    with TestClient(app, base_url=url) as client:
        for host in accepted:
            response = client.post("/", json=_rpc("initialize"), headers=headers | {"Host": host})
            assert response.status_code == 200
        response = client.post("/", json=_rpc("initialize"), headers=headers | {"Host": rejected})
        assert response.status_code == 421


def test_http_main_configures_structured_logging(monkeypatch) -> None:
    import json
    import logging
    import sys

    import uvicorn

    from thermoctl.logging import JsonFormatter
    from thermoctl.mcp import server

    settings = _settings("port")
    settings.log_format = "json"
    settings.log_level = "INFO"
    monkeypatch.setattr(sys, "argv", ["thermoctl-mcp", "--http"])
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    class EngineStub:
        def dispose(self):
            pass

    monkeypatch.setattr(server, "create_engine_from_settings", lambda _settings: EngineStub())
    monkeypatch.setattr(server, "session_factory", lambda _engine: object())
    monkeypatch.setattr("thermoctl.mcp.http_app.create_mcp_asgi_app", lambda _s, _f: object())
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    try:
        root.handlers.clear()
        root.setLevel(logging.WARNING)
        server.main()
        assert root.level == logging.INFO
        assert any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers)
        record = logging.LogRecord("thermoctl.mcp", logging.INFO, __file__, 1, "mcp.tool", (), None)
        record.event = "mcp.tool"
        assert json.loads(root.handlers[0].formatter.format(record))["event"] == "mcp.tool"
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)


def test_dockerfile_exposes_web_port() -> None:
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parent.parent / "docker" / "Dockerfile").read_text()
    assert "EXPOSE 8000" in dockerfile.splitlines()


def test_http_app_requires_public_url(session: Session) -> None:
    with pytest.raises(ValueError, match="öffentliche MCP-Adresse"):
        create_mcp_asgi_app(_settings("off"), _SharedSession(session))


@pytest.mark.parametrize("mode", ["path", "port"])
def test_challenge_metadata_uses_resource_path(session: Session, mode: str) -> None:
    settings = _settings(mode)
    if mode == "path":
        settings.public_url = "https://thermo.example.invalid/praefix"
    else:
        settings.mcp_public_url = "https://mcp.example.invalid/praefix/mcp"
    app = create_mcp_asgi_app(settings, _SharedSession(session))
    with TestClient(app) as client:
        response = client.post("/", json=_rpc("initialize"))
    assert response.status_code == 401
    expected = "https://"
    expected += "thermo.example.invalid" if mode == "path" else "mcp.example.invalid"
    expected += "/.well-known/oauth-protected-resource/praefix/mcp"
    assert f'resource_metadata="{expected}"' in response.headers["www-authenticate"]
