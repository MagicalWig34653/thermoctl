import os
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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
    # Path prefix the interface is served under -- e.g. behind a reverse proxy that
    # publishes it below a path instead of at the domain root. The motivating case is
    # running as a Home Assistant add-on: Home Assistant's Ingress proxies the
    # interface under `/api/hassio_ingress/<random-token>/` and expects every
    # generated link, redirect, cookie and static asset to stay under that same
    # prefix. Read once at startup from configuration -- not from the request.
    #
    # This value is no longer applied to every request unconditionally (it was, up
    # to the point the add-on's port was also exposed directly to a reverse proxy --
    # see `docs/STATUS.md`). It is instead the one *trusted reference value* that
    # `thermoctl.app.create_app()`'s `resolve_root_path` middleware compares an
    # incoming request's `X-Ingress-Path` header against, request by request: the
    # prefix only applies when the header reproduces this value exactly. The header
    # itself stays untrusted input -- whoever reaches the service directly could set
    # it to anything, and trusting it unconditionally would let them steer where
    # every generated link, redirect and cookie points (an open redirect on every
    # page). Comparing it against a value this same process already learned from the
    # Supervisor at start-up (`docker/thermoctl_ingress.py`) closes that hole without
    # giving up Ingress support. See `thermoctl.app._ingress_header_prefix` for the
    # actual comparison and why it is exact, and why a request that comes in without
    # this header (or with the wrong value) is treated as "no prefix" rather than
    # trusting it or rejecting the request outright.
    # Empty means "served at the domain root", the default and by far the common case
    # (and also disables the header check above entirely -- an instance with nothing
    # configured has nothing to compare a header against and must never gain a
    # prefix from a request alone).
    root_path: str = ""
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
    mqtt_prefix: str = "thermoctl"
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

    @field_validator("root_path")
    @classmethod
    def _normalise_root_path(cls, value: str) -> str:
        """Normalises the configured prefix and rejects anything that isn't one.

        `""` and `"/"` both mean "no prefix" and collapse to `""`. Anything else must
        start with `/` (a bare path, not an absolute URL) and must not carry a
        trailing slash -- `request.scope["root_path"]` is conventionally without one,
        and FastAPI's own link generation assumes that. Also rejects a line break:
        this value can end up in log lines and, via `root_path`, in every `Location`
        header the service sends.
        """
        value = value.strip()
        if value in ("", "/"):
            return ""
        if "\n" in value or "\r" in value:
            raise ValueError("THERMOCTL_ROOT_PATH darf keine Zeilenumbrueche enthalten")
        if not value.startswith("/"):
            raise ValueError("THERMOCTL_ROOT_PATH muss mit '/' beginnen (oder leer sein)")
        return value.rstrip("/")

    def passkeys_available(self) -> bool:
        """Without a relying party id there are no passkeys — and none half-enabled."""
        return bool(self.passkey_rp_id)

    def passkey_allowed_origin(self) -> str:
        """The origin the authenticator must have seen."""
        if self.passkey_origin:
            return self.passkey_origin.rstrip("/")
        return f"https://{self.passkey_rp_id}"

    def sanitized_database_url(self) -> str:
        """The connection string without credentials — for log output."""
        return make_url(self.database_url).render_as_string(hide_password=True)

    def sanitized_mqtt_connection(self) -> str:
        """The MQTT connection details without the password — for log output."""
        log = "mqtts" if self.mqtt_tls else "mqtt"
        user = f"{self.mqtt_username}@" if self.mqtt_username else ""
        host = self.mqtt_host or "<nicht konfiguriert>"
        return f"{log}://{user}{host}:{self.mqtt_port}"


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
    file = os.environ.get("THERMOCTL_ENV_FILE", ".env")
    return Settings(_env_file=file or None)
