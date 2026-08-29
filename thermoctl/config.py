from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Einstellungen aus Umgebung oder .env.

    Hier steht ausschliesslich, was Secret ist oder vor der Datenbank gebraucht wird.
    Alles Fachliche kommt aus der Tabelle `setting`.
    """

    model_config = SettingsConfigDict(
        env_prefix="THERMOCTL_", env_file=".env", extra="ignore"
    )

    database_url: str
    secret_key: SecretStr = Field(min_length=32)
    bind_host: str = "0.0.0.0"  # noqa: S104 — im Container beabsichtigt
    bind_port: int = 8000
    log_level: str = "INFO"
    log_format: str = "json"
    secure_cookies: bool = False
    mqtt_enabled: bool = False
    mqtt_host: str | None = None
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_client_id: str = "thermoctl"
    mqtt_base_topic: str = "zigbee2mqtt"
    mqtt_ca_cert: str | None = None
    # Regionsabhaengig: iotx-eu, iotx-us, iotx-ap. Steht deshalb in der Konfiguration und
    # nicht im Quelltext (Grundsatz 1) — wer seine Geraete in einer anderen Region
    # angemeldet hat, traegt hier seine ein, statt den Adapter zu aendern.
    meross_api_base: str = "https://iotx-eu.meross.com"
    meross_email: str | None = None
    meross_password: SecretStr | None = None

    def sanitized_database_url(self) -> str:
        """Die Verbindungszeichenfolge ohne Zugangsdaten — fuer Logausgaben."""
        return make_url(self.database_url).render_as_string(hide_password=True)

    def sanitized_mqtt_connection(self) -> str:
        """Die MQTT-Verbindungsangaben ohne Passwort — fuer Logausgaben."""
        protokoll = "mqtts" if self.mqtt_tls else "mqtt"
        benutzer = f"{self.mqtt_username}@" if self.mqtt_username else ""
        host = self.mqtt_host or "<nicht konfiguriert>"
        return f"{protokoll}://{benutzer}{host}:{self.mqtt_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
