"""Die Umgebung des Live-Servers der Browsertests: ohne Schattenschleife, ohne Meross-Konto.

Der Server der Browsertests ist für die ganze Sitzung geteilt. Lief darin die Schattenschleife,
schrieb sie jede Minute eine Entscheidung je Zone dazu -- hinter die gesäten Zeilen der
Verlauf-Tests ("kein Messwert", Sollwert aus dem Zeitplan), sodass dieselben Tests im Gesamtlauf
rot wurden und einzeln grün blieben. Ausgelöst hatte das nicht MQTT, sondern `meross_email=""`:
Für die Anwendung ist eine leere Zeichenkette ein eingerichtetes Konto
(`credentials_configured`), also braucht sie die Schleife.
"""

import os

import pytest

from browser_tests.live_server import server_environment
from thermoctl.app import _shadow_loop_needed
from thermoctl.config import Settings
from thermoctl.integrations.meross import credentials_configured


def _settings_of(environment: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Die Einstellungen, die ein Prozess mit genau dieser Umgebung läse."""
    for key in [key for key in os.environ if key.startswith("THERMOCTL_")]:
        monkeypatch.delenv(key)
    for key, value in environment.items():
        if key.startswith("THERMOCTL_"):
            monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_the_browser_test_server_runs_no_shadow_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = server_environment("sqlite:///wegwerf.db", 8765, "")
    settings = _settings_of(environment, monkeypatch)
    assert settings.mqtt_enabled is False
    assert credentials_configured(settings) is False
    assert _shadow_loop_needed(settings) is False


def test_no_meross_variable_reaches_the_browser_test_server_not_even_an_empty_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Auch ein Konto im Entwicklerrechner darf nicht durchrutschen.
    monkeypatch.setenv("THERMOCTL_MEROSS_EMAIL", "entwickler@example.invalid")
    monkeypatch.setenv("THERMOCTL_MEROSS_PASSWORD", "geheim")
    monkeypatch.setenv("THERMOCTL_MQTT_ENABLED", "true")
    environment = server_environment("sqlite:///wegwerf.db", 8765, "/api/hassio_ingress/x")
    assert not [key for key in environment if "MEROSS" in key]
    assert environment["THERMOCTL_MQTT_ENABLED"] == "false"
    assert environment["THERMOCTL_ROOT_PATH"] == "/api/hassio_ingress/x"
    assert environment["THERMOCTL_ENV_FILE"] == ""
