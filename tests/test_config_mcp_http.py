"""Betriebsarten und öffentliche Adressen des MCP-HTTP-Zugangs."""

import pytest
from pydantic import ValidationError

from thermoctl.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_mcp_http_defaults_to_off_without_addresses() -> None:
    settings = _settings()
    assert settings.mcp_mode == "off"
    assert settings.mcp_path == "/mcp"
    assert settings.mcp_port is None
    assert settings.public_url is None
    assert settings.mcp_public_url is None
    settings.validate_mcp_http()
    assert settings.mcp_base_url() is None


def test_mcp_http_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THERMOCTL_MCP_MODE", "port")
    monkeypatch.setenv("THERMOCTL_MCP_PATH", "/agent")
    monkeypatch.setenv("THERMOCTL_MCP_PORT", "9234")
    monkeypatch.setenv("THERMOCTL_PUBLIC_URL", "https://web.example.invalid")
    monkeypatch.setenv("THERMOCTL_MCP_PUBLIC_URL", "https://mcp.example.invalid")
    settings = _settings()
    assert settings.mcp_mode == "port"
    assert settings.mcp_path == "/agent"
    assert settings.mcp_port == 9234
    assert settings.public_url == "https://web.example.invalid"
    assert settings.mcp_public_url == "https://mcp.example.invalid"
    settings.validate_mcp_http()


@pytest.mark.parametrize("mode", ["path", "port"])
def test_active_mcp_needs_web_url(mode: str) -> None:
    settings = _settings(mcp_mode=mode, mcp_port=9234, mcp_public_url="https://mcp.example.invalid")
    with pytest.raises(ValueError, match="THERMOCTL_PUBLIC_URL"):
        settings.validate_mcp_http()


def test_path_mode_builds_url_and_accepts_override() -> None:
    settings = _settings(
        mcp_mode="path", public_url="https://web.example.invalid", mcp_path="/agent"
    )
    settings.validate_mcp_http()
    assert settings.mcp_base_url() == "https://web.example.invalid/agent"

    overridden = _settings(
        mcp_mode="path",
        public_url="https://web.example.invalid",
        mcp_public_url="https://proxy.example.invalid/agent",
    )
    overridden.validate_mcp_http()
    assert overridden.mcp_base_url() == "https://proxy.example.invalid/agent"


@pytest.mark.parametrize("port", [None, 0, -1, 65536])
def test_port_mode_requires_valid_listener_port(port: int | None) -> None:
    settings = _settings(
        mcp_mode="port",
        public_url="https://web.example.invalid",
        mcp_public_url="https://mcp.example.invalid",
        mcp_port=port,
    )
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PORT"):
        settings.validate_mcp_http()


@pytest.mark.parametrize("port", [1, 65535])
def test_port_mode_accepts_port_limits_and_uses_mcp_url(port: int) -> None:
    settings = _settings(
        mcp_mode="port",
        public_url="https://web.example.invalid",
        mcp_public_url="https://mcp.example.invalid",
        mcp_port=port,
    )
    settings.validate_mcp_http()
    assert settings.mcp_base_url() == "https://mcp.example.invalid"


def test_port_mode_requires_own_public_url() -> None:
    settings = _settings(mcp_mode="port", public_url="https://web.example.invalid", mcp_port=9234)
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PUBLIC_URL"):
        settings.validate_mcp_http()


@pytest.mark.parametrize("url", [
    "ftp://web.example.invalid", "http://web.example.invalid", "http://localhost.evil.invalid",
    "https://web.example.invalid/", "https://web.example.invalid\nattack",
    "https://user:password@web.example.invalid", "https://", "https://web.example.invalid\rattack",
    "https://[::1",
])
@pytest.mark.parametrize("field", ["public_url", "mcp_public_url"])
def test_invalid_public_urls_are_rejected(field: str, url: str) -> None:
    values = {
        "mcp_mode": "path",
        "public_url": "https://web.example.invalid",
        "mcp_public_url": "https://mcp.example.invalid",
        field: url,
    }
    with pytest.raises(ValueError, match=f"THERMOCTL_{field.upper()}"):
        _settings(**values).validate_mcp_http()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_http_is_allowed_for_loopback_only(host: str) -> None:
    settings = _settings(mcp_mode="path", public_url=f"http://{host}:8000")
    settings.validate_mcp_http()
    assert settings.mcp_base_url() == f"http://{host}:8000/mcp"


@pytest.mark.parametrize("path", [
    "mcp", "/", "/mcp/", "/mcp\nattack", "/mcp\rattack", "/api", "/api/v1",
    "/static", "/static/css", "/.well-known", "/.well-known/oauth",
])
def test_invalid_or_reserved_mcp_path_is_rejected(path: str) -> None:
    settings = _settings(mcp_mode="path", public_url="https://web.example.invalid", mcp_path=path)
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PATH"):
        settings.validate_mcp_http()


@pytest.mark.parametrize("path, root_path", [
    ("/proxy", "/proxy"), ("/proxy/agent", "/proxy"), ("/proxy", "/proxy/agent")
])
def test_mcp_path_cannot_overlap_root_path(path: str, root_path: str) -> None:
    settings = _settings(
        mcp_mode="path", public_url="https://web.example.invalid",
        root_path=root_path, mcp_path=path
    )
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PATH.*THERMOCTL_ROOT_PATH"):
        settings.validate_mcp_http()


def test_distinct_mcp_and_root_paths_are_allowed() -> None:
    settings = _settings(
        mcp_mode="path", public_url="https://web.example.invalid", root_path="/proxy",
        mcp_path="/mcp"
    )
    settings.validate_mcp_http()


def test_unknown_mcp_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="mcp_mode"):
        _settings(mcp_mode="unknown")


@pytest.mark.parametrize("path", [
    "/a b", "/mcp?x", "/m#c", "/m/../api", "/%2e%2e/api", "/a//b", "/a/./b", "/mcp ", "/é",
    "/..", "/.", "/authorize", "/token", "/register", "/revoke", "/oauth", "/oauth/x",
    "/login", "/tokens",
])
def test_mcp_path_must_be_plain_and_unreserved(path: str) -> None:
    settings = _settings(mcp_mode="path", public_url="https://web.example.invalid", mcp_path=path)
    with pytest.raises(ValueError, match="THERMOCTL_MCP_PATH"):
        settings.validate_mcp_http()


def test_reserved_mcp_paths_cover_every_real_top_level_route() -> None:
    """Die Konstante in config.py wird gegen die echten Routen geprüft, nicht von Hand gepflegt."""
    from tests.helpers import alle_api_routen
    from thermoctl.app import create_app

    anwendung = create_app()
    namen = {"/" + r.path.strip("/").split("/")[0] for r in alle_api_routen(anwendung)}
    namen |= {"/" + getattr(r, "path", "").strip("/").split("/")[0] for r in anwendung.routes}
    namen.discard("/")
    assert "/login" in namen and "/tokens" in namen  # Sicherung gegen leeres Einsammeln
    for name in sorted(namen):
        settings = _settings(
            mcp_mode="path", public_url="https://web.example.invalid", mcp_path=name
        )
        with pytest.raises(ValueError, match="THERMOCTL_MCP_PATH"):
            settings.validate_mcp_http()


def test_invalid_mcp_path_does_not_block_port_mode() -> None:
    settings = _settings(
        mcp_mode="port", public_url="https://web.example.invalid",
        mcp_public_url="https://mcp.example.invalid", mcp_port=9234, mcp_path="/a b",
    )
    settings.validate_mcp_http()


@pytest.mark.parametrize("url", [
    " https://x.org", "https://x.org ", "https://x.org\t", "https://a b.example",
    "https://x.org:99999", "https://x.org:abc", "https://x.org:", "https://x.org?q=1",
    "https://x.org#f", "https://exämple.org", "https://例え.jp", "https://x.org/a/../b",
    "https://x.org/a;p", "https://x.org/a%20b", "https://x.org/a?",
])
@pytest.mark.parametrize("field", ["public_url", "mcp_public_url"])
def test_further_invalid_public_urls_are_rejected(field: str, url: str) -> None:
    values = {
        "mcp_mode": "path",
        "public_url": "https://web.example.invalid",
        "mcp_public_url": "https://mcp.example.invalid",
        field: url,
    }
    with pytest.raises(ValueError, match=f"THERMOCTL_{field.upper()}"):
        _settings(**values).validate_mcp_http()


def test_public_url_may_carry_a_path_prefix() -> None:
    """Hinter einem Pfad-Präfix (root_path, Ingress) ist die öffentliche Adresse mit Pfad."""
    settings = _settings(
        mcp_mode="path", public_url="https://web.example.invalid/praefix", root_path="/praefix"
    )
    settings.validate_mcp_http()
    assert settings.mcp_base_url() == "https://web.example.invalid/praefix/mcp"


def test_path_mode_without_any_url_has_no_base_url_and_fails_validation() -> None:
    settings = _settings(mcp_mode="path")
    assert settings.mcp_base_url() is None
    with pytest.raises(ValueError, match="THERMOCTL_PUBLIC_URL"):
        settings.validate_mcp_http()
