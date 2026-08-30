import os
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Settings from the environment or .env.

    This holds exclusively what is a secret or is needed before the database is
    available. Everything domain-specific comes from the `setting` table.
    """

    model_config = SettingsConfigDict(
        env_prefix="THERMOCTL_", env_file=".env", extra="ignore"
    )

    database_url: str
    secret_key: SecretStr = Field(min_length=32)
    bind_host: str = "0.0.0.0"  # noqa: S104 — intentional in the container
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
    # Our own subtree: this is where thermoctl publishes its state and listens for
    # commands. Kept separate from `mqtt_base_topic`, which belongs to Zigbee2MQTT --
    # two writers in one subtree would be the most reliable way to a bug nobody can
    # trace anymore.
    mqtt_praefix: str = "thermoctl"
    mqtt_ca_cert: str | None = None
    # Region-dependent: iotx-eu, iotx-us, iotx-ap. Lives in configuration and not in
    # source code for that reason (principle 1) — whoever registered their devices in
    # a different region enters theirs here instead of changing the adapter.
    meross_api_base: str = "https://iotx-eu.meross.com"
    meross_email: str | None = None
    meross_password: SecretStr | None = None
    mcp_token: SecretStr | None = None

    # --- Passkeys (WebAuthn) ---------------------------------------------------------
    # The relying party id is the bare hostname under which the interface is
    # reachable — no scheme, no port. It lives **exclusively** here and is never built
    # from the request's `Host` header: that is set by the caller, and a relying party
    # id under the caller's control would cancel out the very protection WebAuthn is
    # supposed to give.
    #
    # Without this setting, passkeys are disabled — not halfway, but entirely: the
    # login page then doesn't offer them at all.
    passkey_rp_id: str | None = None
    passkey_rp_name: str = "thermoctl"
    # The allowed origin against which the authenticator's response is checked. Left
    # empty, `https://<passkey_rp_id>` applies. For development on your own machine
    # you need `http://localhost:8000` here — WebAuthn only allows http there.
    passkey_origin: str | None = None
    notify_webhook: str | None = None
    notify_webhook_token: SecretStr | None = None

    def passkeys_moeglich(self) -> bool:
        """Without a relying party id there are no passkeys — and none half-enabled."""
        return bool(self.passkey_rp_id)

    def passkey_erlaubte_origin(self) -> str:
        """The origin the authenticator must have seen."""
        if self.passkey_origin:
            return self.passkey_origin.rstrip("/")
        return f"https://{self.passkey_rp_id}"

    def sanitized_database_url(self) -> str:
        """The connection string without credentials — for log output."""
        return make_url(self.database_url).render_as_string(hide_password=True)

    def sanitized_mqtt_connection(self) -> str:
        """The MQTT connection details without the password — for log output."""
        protokoll = "mqtts" if self.mqtt_tls else "mqtt"
        user = f"{self.mqtt_username}@" if self.mqtt_username else ""
        host = self.mqtt_host or "<nicht konfiguriert>"
        return f"{protokoll}://{user}{host}:{self.mqtt_port}"


@lru_cache
def get_settings() -> Settings:
    """This process's settings.

    `THERMOCTL_ENV_FILE` selects the file -- empty means none. The test suite needs
    this: previously, the test run also read the developer's `.env`, and whoever
    entered THERMOCTL_PASSKEY_RP_ID there saw three tests turn red that had nothing to
    do with their change. The more dangerous case is the reverse -- a setting that is
    set locally and missing in CI makes tests look green that will fail there. This is
    the second attempt at the same bug; the first only set the two required variables.

    Evaluated here and not in `model_config`: an `os.environ` access there would run
    at module **import** time, i.e. before any test can set anything.
    """
    datei = os.environ.get("THERMOCTL_ENV_FILE", ".env")
    return Settings(_env_file=datei or None)
