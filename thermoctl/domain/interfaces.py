"""What is connected to this plant from the outside -- and whether it is really running.

The service talks to six external endpoints, and up to now you had to check `.env` to
know which of them are configured. No `.env` answers two questions: whether a
configuration actually *works*, and where a value actually comes from.

**Why there are no switches here.** Each of these settings is read from the environment
at startup; the MQTT connection, for instance, is established exactly once during the
service's lifecycle. A switch in the interface would therefore only take effect after a
restart -- and a switch that looks like it worked is worse than no switch at all. The
page instead states which variable to set. Whatever can genuinely be flipped at runtime
lives under Operation and Settings, and is linked from here.

**Secrets never appear here.** The page shows *whether* a password or token is set,
never which one. Principle 2.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.models.credential import ApiToken


@dataclass(frozen=True)
class Detail:
    """A single value of an interface, together with its origin."""

    name: str
    value: str
    source: str  # "Umgebung", "Standard" oder "Datenbank"
    variable: str | None = None


@dataclass(frozen=True)
class Interface:
    key: str
    name: str
    purpose: str
    state: str  # "running", "configured", "off", "missing", "not_built"
    finding: str
    details: list[Detail] = field(default_factory=list)
    hint: str | None = None
    more: tuple[str, str] | None = None  # (Beschriftung, Pfad)


def _yes_no(set_value: bool) -> str:
    """For secrets: whether something is set, never what."""
    return "hinterlegt" if set_value else "nicht hinterlegt"


def overview(
    session: Session, settings: Settings, bridge_reachable: bool | None
) -> list[Interface]:
    """The state of all endpoints, in the order in which they get configured."""
    default = Settings.model_fields

    def source(field_name: str, current: object) -> str:
        default_value = default[field_name].default
        return "Standard" if current == default_value else "Umgebung"

    active_tokens = (
        session.scalar(
            select(ApiToken.id).where(ApiToken.revoked_at.is_(None)).limit(1)
        )
        is not None
    )

    items: list[Interface] = []

    # --- Zigbee2MQTT ---------------------------------------------------------
    if not settings.mqtt_enabled:
        mqtt_state, mqtt_befund = (
            "off",
            "Abgeschaltet. Ohne sie kommen keine Messwerte an und die Anlage regelt nichts.",
        )
    elif settings.mqtt_host is None:
        mqtt_state, mqtt_befund = (
            "missing",
            "Eingeschaltet, aber ohne Broker-Adresse — so kommt keine Verbindung zustande.",
        )
    elif bridge_reachable is True:
        mqtt_state, mqtt_befund = ("running", "Die Brücke meldet sich.")
    elif bridge_reachable is False:
        mqtt_state, mqtt_befund = (
            "missing",
            "Verbunden, aber die Zigbee2MQTT-Brücke meldet sich nicht.",
        )
    else:
        mqtt_state, mqtt_befund = (
            "configured",
            "Eingerichtet; seit dem Start kam noch keine Zustandsmeldung der Brücke.",
        )

    items.append(
        Interface(
            "mqtt",
            "Zigbee2MQTT",
            "Die Quelle aller Messwerte und der Weg zu den Ventilen.",
            mqtt_state,
            mqtt_befund,
            [
                Detail("Eingeschaltet", "ja" if settings.mqtt_enabled else "nein",
                       source("mqtt_enabled", settings.mqtt_enabled),
                       "THERMOCTL_MQTT_ENABLED"),
                Detail("Verbindung", settings.sanitized_mqtt_connection(),
                       source("mqtt_host", settings.mqtt_host), "THERMOCTL_MQTT_HOST"),
                Detail("TLS", "ja" if settings.mqtt_tls else "nein",
                       source("mqtt_tls", settings.mqtt_tls), "THERMOCTL_MQTT_TLS"),
                Detail("Basis-Topic", settings.mqtt_base_topic,
                       source("mqtt_base_topic", settings.mqtt_base_topic),
                       "THERMOCTL_MQTT_BASE_TOPIC"),
                Detail("Passwort", _yes_no(settings.mqtt_password is not None),
                       "Umgebung" if settings.mqtt_password else "Standard",
                       "THERMOCTL_MQTT_PASSWORD"),
            ],
            more=("Geräte ansehen", "/devices"),
        )
    )

    # --- REST ----------------------------------------------------------------
    items.append(
        Interface(
            "rest",
            "REST-Schnittstelle",
            "Für eigene Skripte und fremde Systeme. Immer da, aber ohne Token nutzlos.",
            "running" if active_tokens else "configured",
            (
                "Erreichbar unter /api/v1; mindestens ein Token ist ausgestellt."
                if active_tokens
                else "Erreichbar unter /api/v1, aber es gibt kein gültiges Token — "
                "damit kommt niemand hinein."
            ),
            [Detail("Beschreibung", "/docs", "Standard")],
            more=("Tokens verwalten", "/tokens"),
        )
    )

    # --- MCP -----------------------------------------------------------------
    hat_mcp = settings.mcp_token is not None
    items.append(
        Interface(
            "mcp",
            "MCP-Server",
            "Damit ein Sprachmodell die Anlage lesen und im Alltag bedienen kann.",
            "configured" if hat_mcp else "off",
            (
                "Ein Token ist hinterlegt. Der Server läuft als eigener Prozess "
                "(thermoctl-mcp), nicht in diesem hier."
                if hat_mcp
                else "Kein Token hinterlegt — der MCP-Server startet ohne eines nicht."
            ),
            [
                Detail("Token", _yes_no(hat_mcp),
                       "Umgebung" if hat_mcp else "Standard", "THERMOCTL_MCP_TOKEN"),
            ],
            hint=(
                "Scharf schalten kann der MCP-Server bewusst nicht — nur zurücknehmen."
                if hat_mcp
                else None
            ),
        )
    )

    # --- Passkeys ------------------------------------------------------------
    items.append(
        Interface(
            "passkeys",
            "Passkeys",
            "Anmeldung ohne Passwort, mit dem Gerät als Schlüssel.",
            "configured" if settings.passkeys_available() else "off",
            (
                "Eingerichtet. Wer noch keinen hinterlegt hat, meldet sich weiter "
                "mit Passwort an."
                if settings.passkeys_available()
                else "Ohne die Kennung der Gegenstelle (RP-ID) bietet die Anmeldung "
                "keine Passkeys an."
            ),
            [
                Detail("RP-ID", settings.passkey_rp_id or "—",
                       "Umgebung" if settings.passkey_rp_id else "Standard",
                       "THERMOCTL_PASSKEY_RP_ID"),
                Detail("Erlaubter Ursprung", settings.passkey_allowed_origin() or "—",
                       "Umgebung" if settings.passkey_rp_id else "Standard",
                       "THERMOCTL_PASSKEY_ORIGIN"),
            ],
            more=("Eigene Passkeys", "/passkeys"),
        )
    )

    # --- Notifications ---------------------------------------------------------
    hat_webhook = settings.notify_webhook is not None
    items.append(
        Interface(
            "benachrichtigung",
            "Benachrichtigungen",
            "Meldet Störungen an einen Webhook — ausgefallene Sensoren, stille Brücke.",
            "configured" if hat_webhook else "off",
            (
                "Störungen gehen an den hinterlegten Webhook."
                if hat_webhook
                else "Kein Webhook hinterlegt. Störungen stehen dann nur im Protokoll "
                "und in der Oberfläche."
            ),
            [
                Detail("Webhook", settings.notify_webhook or "—",
                       "Umgebung" if hat_webhook else "Standard",
                       "THERMOCTL_NOTIFY_WEBHOOK"),
                Detail("Token", _yes_no(settings.notify_webhook_token is not None),
                       "Umgebung" if settings.notify_webhook_token else "Standard",
                       "THERMOCTL_NOTIFY_WEBHOOK_TOKEN"),
            ],
        )
    )

    # --- Meross --------------------------------------------------------------
    hat_meross = settings.meross_email is not None
    items.append(
        Interface(
            "meross",
            "Meross",
            "Schaltsteckdosen als zweiter Aktortyp neben den Zigbee-Ventilen.",
            "configured" if hat_meross else "off",
            (
                "Zugangsdaten hinterlegt."
                if hat_meross
                else "Keine Zugangsdaten hinterlegt."
            ),
            [
                Detail("Konto", settings.meross_email or "—",
                       "Umgebung" if hat_meross else "Standard", "THERMOCTL_MEROSS_EMAIL"),
                Detail("Passwort", _yes_no(settings.meross_password is not None),
                       "Umgebung" if settings.meross_password else "Standard",
                       "THERMOCTL_MEROSS_PASSWORD"),
            ],
            hint=(
                "Der Adapter ist gebaut, sein Nutzlastaufbau ist aber eine begründete "
                "Annahme und lief nie gegen ein echtes Konto."
            ),
        )
    )

    # --- Home Assistant ------------------------------------------------------
    items.append(
        Interface(
            "homeassistant",
            "Home Assistant",
            "Meldet jede Zone als eigenes Gerät an, über die MQTT-Discovery: Thermostat, "
            "Boost, Solltemperatur je Modus und die Regelparameter.",
            "running" if settings.mqtt_enabled else "off",
            (
                "Läuft und nimmt Befehle entgegen — auch im Trockenlauf. Eine "
                "Zustandsmeldung bewegt nichts, und eine Anbindung, die man erst nach "
                "dem Scharfschalten ausprobieren kann, ließe sich genau dann nicht mehr "
                "gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre. Ob wirklich "
                "geschaltet wird, sagt dort die Entität „Regelung scharf“."
                if settings.mqtt_enabled
                else "Ohne MQTT gibt es keinen Weg zu Home Assistant."
            ),
            [
                Detail(
                    "Discovery-Präfix",
                    "homeassistant",
                    "Standard",
                ),
                Detail(
                    "Eigenes Präfix",
                    settings.mqtt_prefix,
                    source("mqtt_prefix", settings.mqtt_prefix),
                    "THERMOCTL_MQTT_PREFIX",
                ),
            ],
            more=("Betriebszustand", "/control"),
        )
    )

    return items
