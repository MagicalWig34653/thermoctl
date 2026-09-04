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
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import Integration
from thermoctl.integrations.meross import credentials_configured


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
    state: str  # "running", "configured", "off", "missing"
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
        mqtt_state, mqtt_finding = (
            "off",
            "Abgeschaltet. Ohne sie kommen keine Messwerte an und die Anlage regelt nichts.",
        )
    elif settings.mqtt_host is None:
        mqtt_state, mqtt_finding = (
            "missing",
            "Eingeschaltet, aber ohne Broker-Adresse — so kommt keine Verbindung zustande.",
        )
    elif bridge_reachable is True:
        mqtt_state, mqtt_finding = ("running", "Die Brücke meldet sich.")
    elif bridge_reachable is False:
        mqtt_state, mqtt_finding = (
            "missing",
            "Verbunden, aber die Zigbee2MQTT-Brücke meldet sich nicht.",
        )
    else:
        mqtt_state, mqtt_finding = (
            "configured",
            "Eingerichtet; seit dem Start kam noch keine Zustandsmeldung der Brücke.",
        )

    items.append(
        Interface(
            "mqtt",
            "Zigbee2MQTT",
            "Empfängt Messwerte. Erst scharf und danach neu gestartet gehen Sollwerte "
            "an selbstregelnde Thermostatventile und Ein/Aus-Befehle an gewöhnliche "
            "Aktoren.",
            mqtt_state,
            mqtt_finding,
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
    # `hat_meross` braucht E-Mail **und** Passwort -- der Abgleich (services/
    # meross_discovery.py) kann sich mit nur der Hälfte gar nicht erst anmelden.
    email_hinterlegt = settings.meross_email is not None
    hat_meross = credentials_configured(settings)
    meross_integration = session.scalar(
        select(Integration).where(Integration.code == "meross")
    )
    # Ob je ein Abgleich gelang, steht nirgends als eigener Zeitstempel -- aber ein
    # Meross-Gerät kann nur über genau diesen Abgleich entstanden sein
    # (services/ingest.py legt ausschliesslich Zigbee2MQTT-Geräte an). Mindestens eine
    # Zeile hier ist also der Beleg, dass die Anmeldung mindestens einmal funktioniert
    # hat -- nicht bloss, dass Zugangsdaten eingetragen wurden.
    meross_geraet_gefunden = meross_integration is not None and (
        session.scalar(
            select(Device.id)
            .where(Device.integration_id == meross_integration.id)
            .limit(1)
        )
        is not None
    )
    if not email_hinterlegt and settings.meross_password is None:
        meross_state, meross_finding = "off", "Keine Zugangsdaten hinterlegt."
    elif not hat_meross:
        meross_state, meross_finding = (
            "missing",
            "Nur die E-Mail-Adresse oder nur das Passwort ist hinterlegt — zum "
            "Anmelden fehlt die jeweils andere Hälfte.",
        )
    elif meross_geraet_gefunden:
        meross_state, meross_finding = (
            "running",
            "Zugangsdaten hinterlegt, mindestens ein Gerät wurde schon gefunden.",
        )
    else:
        meross_state, meross_finding = (
            "configured",
            "Zugangsdaten hinterlegt; seit dem Start wurde noch kein Gerät gefunden — "
            "der erste Abgleich steht noch aus oder ist bisher fehlgeschlagen.",
        )
    items.append(
        Interface(
            "meross",
            "Meross",
            "Schaltsteckdosen als zweiter Aktortyp neben den Zigbee-Ventilen.",
            meross_state,
            meross_finding,
            [
                Detail("Konto", settings.meross_email or "—",
                       "Umgebung" if email_hinterlegt else "Standard", "THERMOCTL_MEROSS_EMAIL"),
                Detail("Passwort", _yes_no(settings.meross_password is not None),
                       "Umgebung" if settings.meross_password else "Standard",
                       "THERMOCTL_MEROSS_PASSWORD"),
                Detail("Wolke", settings.meross_api_base, "Standard",
                       "THERMOCTL_MEROSS_API_BASE"),
            ],
            hint=(
                "Geräteerkennung und Schaltweg sind gebaut und gegen ein echtes Konto "
                "geprüft: Anmeldung und Geräteliste über HTTP, das Schalten über MQTT. "
                "Der frühere HTTP-Pfad zum Schalten existiert nicht — die Wolke "
                "antwortet dort mit 404. Nachgemessen sind Anmeldung, Geräteliste und "
                "ein `SET Appliance.Control.ToggleX` gegen echte Geräte, bestätigt mit "
                "`SETACK` und einem hochgezählten `lmTime`. Meross-Steckdosen sind mit "
                "dem Regelkreis verdrahtet; Befehle gehen scharf und nach einem "
                "Neustart über diesen Schaltweg hinaus."
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
                "gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre. Die Entität "
                "„Regelung scharf“ zeigt nur den gespeicherten ersten Riegel. Erst nach "
                "einem Neustart gehen Sollwerte an selbstregelnde Thermostatventile "
                "und Ein/Aus-Befehle an gewöhnliche Aktoren."
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
