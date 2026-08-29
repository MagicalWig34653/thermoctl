import pytest
from pydantic import ValidationError

from thermoctl.config import Settings


def test_pflichtfelder_fehlen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THERMOCTL_DATABASE_URL", raising=False)
    monkeypatch.delenv("THERMOCTL_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_zu_kurzer_schluessel_wird_abgewiesen() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite://", secret_key="zu-kurz")


def test_werte_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite:///./x.db")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    s = Settings(_env_file=None)
    assert s.database_url == "sqlite:///./x.db"
    assert s.bind_port == 8000
    assert s.secure_cookies is False


def test_schluessel_erscheint_nicht_in_der_darstellung() -> None:
    s = Settings(_env_file=None, database_url="sqlite://", secret_key="b" * 32)
    assert "b" * 32 not in repr(s)
    assert "b" * 32 not in str(s.model_dump())


def test_datenbank_url_ohne_zugangsdaten() -> None:
    s = Settings(
        _env_file=None,
        database_url="mysql+pymysql://nutzer:geheim@host:3306/thermoctl",
        secret_key="c" * 32,
    )
    bereinigt = s.sanitized_database_url()
    assert "geheim" not in bereinigt
    assert "host:3306/thermoctl" in bereinigt


def test_mqtt_standardmaessig_deaktiviert() -> None:
    s = Settings(_env_file=None, database_url="sqlite://", secret_key="d" * 32)
    assert s.mqtt_enabled is False
    assert s.mqtt_port == 1883
    assert s.mqtt_base_topic == "zigbee2mqtt"


def test_mqtt_verbindungsangaben_enthalten_kein_passwort() -> None:
    s = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="e" * 32,
        mqtt_host="mqtt.example.invalid",
        mqtt_port=8883,
        mqtt_tls=True,
        mqtt_username="empfang",
        mqtt_password="auffaelliges-geheimnis",
    )
    bereinigt = s.sanitized_mqtt_connection()
    assert bereinigt == "mqtts://empfang@mqtt.example.invalid:8883"
    assert "auffaelliges-geheimnis" not in bereinigt


def test_benachrichtigungen_sind_standardmaessig_ohne_webhook() -> None:
    s = Settings(_env_file=None, database_url="sqlite://", secret_key="f" * 32)
    assert s.notify_webhook is None
    assert s.notify_webhook_token is None
