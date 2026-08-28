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

    def sanitized_database_url(self) -> str:
        """Die Verbindungszeichenfolge ohne Zugangsdaten — fuer Logausgaben."""
        return make_url(self.database_url).render_as_string(hide_password=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
