"""Ein Fehler beim Aufbau des MCP-Teils darf den Start nicht verschlucken."""

import pytest

from thermoctl import app as web_app
from thermoctl.config import Settings
from thermoctl.mcp import http_app


def test_other_runtime_errors_while_building_mcp_stop_the_start(monkeypatch) -> None:
    """Nur das fehlende optionale Paket ist ein Grund, ohne MCP weiterzumachen.

    Jeder andere RuntimeError ist ein Fehler im Betrieb und muss laut scheitern, sonst
    liefe die Anlage still ohne den Zugang, den der Betreiber eingeschaltet hat.
    """
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="x" * 32,
        mcp_mode="path",
        public_url="https://thermo.example.invalid",
    )
    monkeypatch.setattr(web_app, "get_settings", lambda: settings)

    def broken(_settings, _factory):
        raise RuntimeError("etwas ganz anderes")

    monkeypatch.setattr(http_app, "create_mcp_asgi_app", broken)
    with pytest.raises(RuntimeError, match="etwas ganz anderes"):
        web_app.create_app()
