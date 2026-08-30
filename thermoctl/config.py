import os
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
    # Der eigene Teilbaum: Hier veroeffentlicht thermoctl seinen Zustand und hoert auf
    # Befehle. Getrennt von `mqtt_base_topic`, das Zigbee2MQTT gehoert -- zwei Schreiber
    # in einem Teilbaum waeren der zuverlaessigste Weg zu einem Fehler, den niemand mehr
    # zuordnen kann.
    mqtt_praefix: str = "thermoctl"
    mqtt_ca_cert: str | None = None
    # Regionsabhaengig: iotx-eu, iotx-us, iotx-ap. Steht deshalb in der Konfiguration und
    # nicht im Quelltext (Grundsatz 1) — wer seine Geraete in einer anderen Region
    # angemeldet hat, traegt hier seine ein, statt den Adapter zu aendern.
    meross_api_base: str = "https://iotx-eu.meross.com"
    meross_email: str | None = None
    meross_password: SecretStr | None = None
    mcp_token: SecretStr | None = None

    # --- Passkeys (WebAuthn) ---------------------------------------------------------
    # Die Relying-Party-ID ist der nackte Hostname, unter dem die Oberflaeche erreichbar
    # ist — ohne Schema, ohne Port. Sie steht **ausschliesslich** hier und wird nie aus
    # der `Host`-Kopfzeile der Anfrage gebildet: Die ist vom Aufrufer gesetzt, und eine
    # Relying-Party-ID unter seiner Kontrolle hebt den Schutz auf, den WebAuthn gerade
    # geben soll.
    #
    # Ohne diese Angabe sind Passkeys abgeschaltet — nicht halb, sondern ganz: Die
    # Anmeldeseite bietet sie dann gar nicht erst an.
    passkey_rp_id: str | None = None
    passkey_rp_name: str = "thermoctl"
    # Die erlaubte Origin, gegen die die Antwort des Authenticators geprueft wird. Leer
    # gelassen gilt `https://<passkey_rp_id>`. Fuer die Entwicklung auf dem eigenen
    # Rechner braucht es hier `http://localhost:8000` — WebAuthn laesst http nur dort zu.
    passkey_origin: str | None = None
    notify_webhook: str | None = None
    notify_webhook_token: SecretStr | None = None

    def passkeys_moeglich(self) -> bool:
        """Ohne Relying-Party-ID gibt es keine Passkeys — und keine halben."""
        return bool(self.passkey_rp_id)

    def passkey_erlaubte_origin(self) -> str:
        """Die Origin, die der Authenticator gesehen haben muss."""
        if self.passkey_origin:
            return self.passkey_origin.rstrip("/")
        return f"https://{self.passkey_rp_id}"

    def sanitized_database_url(self) -> str:
        """Die Verbindungszeichenfolge ohne Zugangsdaten — fuer Logausgaben."""
        return make_url(self.database_url).render_as_string(hide_password=True)

    def sanitized_mqtt_connection(self) -> str:
        """Die MQTT-Verbindungsangaben ohne Passwort — fuer Logausgaben."""
        protokoll = "mqtts" if self.mqtt_tls else "mqtt"
        user = f"{self.mqtt_username}@" if self.mqtt_username else ""
        host = self.mqtt_host or "<nicht konfiguriert>"
        return f"{protokoll}://{user}{host}:{self.mqtt_port}"


@lru_cache
def get_settings() -> Settings:
    """Die Einstellungen dieses Prozesses.

    `THERMOCTL_ENV_FILE` waehlt die Datei -- leer heisst keine. Das braucht die
    Testsuite: Vorher las der Testlauf die `.env` des Entwicklers mit, und wer dort
    THERMOCTL_PASSKEY_RP_ID eintrug, sah drei Tests rot werden, die mit seiner Aenderung
    nichts zu tun hatten. Der gefaehrlichere Fall ist der umgekehrte -- eine Einstellung,
    die oertlich gesetzt ist und in der CI fehlt, laesst Tests gruen aussehen, die dort
    scheitern werden. Das ist der zweite Anlauf auf denselben Fehler; beim ersten wurden
    nur die zwei Pflichtvariablen gesetzt.

    Ausgewertet wird hier und nicht in `model_config`: Ein `os.environ`-Zugriff dort
    liefe beim **Import** des Moduls, also bevor irgendein Test etwas setzen kann.
    """
    datei = os.environ.get("THERMOCTL_ENV_FILE", ".env")
    return Settings(_env_file=datei or None)
