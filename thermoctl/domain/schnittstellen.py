"""Was von aussen an diese Anlage angebunden ist -- und ob es wirklich läuft.

Der Dienst spricht mit sechs Gegenstellen, und bis hierher musste man in `.env` nachsehen,
um zu wissen, welche davon eingerichtet sind. Zwei Fragen beantwortet keine `.env`:
ob eine Einrichtung auch *funktioniert*, und woher ein Wert eigentlich kommt.

**Warum es hier keine Schalter gibt.** Jede dieser Einstellungen wird beim Start aus der
Umgebung gelesen; die MQTT-Verbindung etwa wird im Lebenszyklus genau einmal aufgebaut. Ein
Schalter in der Oberflaeche wuerde also erst nach einem Neustart wirken -- und ein Schalter,
der aussieht, als haette er gewirkt, ist schlimmer als keiner. Die Seite sagt stattdessen,
welche Variable zu setzen ist. Was sich zur Laufzeit wirklich umlegen laesst, steht unter
Betrieb und Einstellungen und ist von hier verlinkt.

**Geheimnisse stehen nie hier.** Die Seite zeigt, *ob* ein Passwort oder Token gesetzt ist,
nie welches. Grundsatz 2.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.models.credential import ApiToken


@dataclass(frozen=True)
class Angabe:
    """Ein einzelner Wert einer Schnittstelle, samt Herkunft."""

    name: str
    wert: str
    quelle: str  # "Umgebung", "Standard" oder "Datenbank"
    variable: str | None = None


@dataclass(frozen=True)
class Schnittstelle:
    schluessel: str
    name: str
    zweck: str
    zustand: str  # "laeuft", "eingerichtet", "aus", "fehlt", "ungebaut"
    befund: str
    angaben: list[Angabe] = field(default_factory=list)
    hinweis: str | None = None
    weiter: tuple[str, str] | None = None  # (Beschriftung, Pfad)


def _ja_nein(gesetzt: bool) -> str:
    """Fuer Geheimnisse: ob etwas gesetzt ist, nie was."""
    return "hinterlegt" if gesetzt else "nicht hinterlegt"


def uebersicht(
    session: Session, einstellungen: Settings, bruecke_erreichbar: bool | None
) -> list[Schnittstelle]:
    """Der Zustand aller Gegenstellen, in der Reihenfolge, in der man sie einrichtet."""
    standard = Settings.model_fields

    def quelle(feldname: str, jetziger: object) -> str:
        vorgabe = standard[feldname].default
        return "Standard" if jetziger == vorgabe else "Umgebung"

    aktive_tokens = (
        session.scalar(
            select(ApiToken.id).where(ApiToken.revoked_at.is_(None)).limit(1)
        )
        is not None
    )

    liste: list[Schnittstelle] = []

    # --- Zigbee2MQTT ---------------------------------------------------------
    if not einstellungen.mqtt_enabled:
        mqtt_zustand, mqtt_befund = (
            "aus",
            "Abgeschaltet. Ohne sie kommen keine Messwerte an und die Anlage regelt nichts.",
        )
    elif einstellungen.mqtt_host is None:
        mqtt_zustand, mqtt_befund = (
            "fehlt",
            "Eingeschaltet, aber ohne Broker-Adresse — so kommt keine Verbindung zustande.",
        )
    elif bruecke_erreichbar is True:
        mqtt_zustand, mqtt_befund = ("laeuft", "Die Brücke meldet sich.")
    elif bruecke_erreichbar is False:
        mqtt_zustand, mqtt_befund = (
            "fehlt",
            "Verbunden, aber die Zigbee2MQTT-Brücke meldet sich nicht.",
        )
    else:
        mqtt_zustand, mqtt_befund = (
            "eingerichtet",
            "Eingerichtet; seit dem Start kam noch keine Zustandsmeldung der Brücke.",
        )

    liste.append(
        Schnittstelle(
            "mqtt",
            "Zigbee2MQTT",
            "Die Quelle aller Messwerte und der Weg zu den Ventilen.",
            mqtt_zustand,
            mqtt_befund,
            [
                Angabe("Eingeschaltet", "ja" if einstellungen.mqtt_enabled else "nein",
                       quelle("mqtt_enabled", einstellungen.mqtt_enabled),
                       "THERMOCTL_MQTT_ENABLED"),
                Angabe("Verbindung", einstellungen.sanitized_mqtt_connection(),
                       quelle("mqtt_host", einstellungen.mqtt_host), "THERMOCTL_MQTT_HOST"),
                Angabe("TLS", "ja" if einstellungen.mqtt_tls else "nein",
                       quelle("mqtt_tls", einstellungen.mqtt_tls), "THERMOCTL_MQTT_TLS"),
                Angabe("Basis-Topic", einstellungen.mqtt_base_topic,
                       quelle("mqtt_base_topic", einstellungen.mqtt_base_topic),
                       "THERMOCTL_MQTT_BASE_TOPIC"),
                Angabe("Passwort", _ja_nein(einstellungen.mqtt_password is not None),
                       "Umgebung" if einstellungen.mqtt_password else "Standard",
                       "THERMOCTL_MQTT_PASSWORD"),
            ],
            weiter=("Geräte ansehen", "/geraete"),
        )
    )

    # --- REST ----------------------------------------------------------------
    liste.append(
        Schnittstelle(
            "rest",
            "REST-Schnittstelle",
            "Für eigene Skripte und fremde Systeme. Immer da, aber ohne Token nutzlos.",
            "laeuft" if aktive_tokens else "eingerichtet",
            (
                "Erreichbar unter /api/v1; mindestens ein Token ist ausgestellt."
                if aktive_tokens
                else "Erreichbar unter /api/v1, aber es gibt kein gültiges Token — "
                "damit kommt niemand hinein."
            ),
            [Angabe("Beschreibung", "/docs", "Standard")],
            weiter=("Tokens verwalten", "/tokens"),
        )
    )

    # --- MCP -----------------------------------------------------------------
    hat_mcp = einstellungen.mcp_token is not None
    liste.append(
        Schnittstelle(
            "mcp",
            "MCP-Server",
            "Damit ein Sprachmodell die Anlage lesen und im Alltag bedienen kann.",
            "eingerichtet" if hat_mcp else "aus",
            (
                "Ein Token ist hinterlegt. Der Server läuft als eigener Prozess "
                "(thermoctl-mcp), nicht in diesem hier."
                if hat_mcp
                else "Kein Token hinterlegt — der MCP-Server startet ohne eines nicht."
            ),
            [
                Angabe("Token", _ja_nein(hat_mcp),
                       "Umgebung" if hat_mcp else "Standard", "THERMOCTL_MCP_TOKEN"),
            ],
            hinweis=(
                "Scharf schalten kann der MCP-Server bewusst nicht — nur zurücknehmen."
                if hat_mcp
                else None
            ),
        )
    )

    # --- Passkeys ------------------------------------------------------------
    liste.append(
        Schnittstelle(
            "passkeys",
            "Passkeys",
            "Anmeldung ohne Passwort, mit dem Gerät als Schlüssel.",
            "eingerichtet" if einstellungen.passkeys_moeglich() else "aus",
            (
                "Eingerichtet. Wer noch keinen hinterlegt hat, meldet sich weiter "
                "mit Passwort an."
                if einstellungen.passkeys_moeglich()
                else "Ohne die Kennung der Gegenstelle (RP-ID) bietet die Anmeldung "
                "keine Passkeys an."
            ),
            [
                Angabe("RP-ID", einstellungen.passkey_rp_id or "—",
                       "Umgebung" if einstellungen.passkey_rp_id else "Standard",
                       "THERMOCTL_PASSKEY_RP_ID"),
                Angabe("Erlaubter Ursprung", einstellungen.passkey_erlaubte_origin() or "—",
                       "Umgebung" if einstellungen.passkey_rp_id else "Standard",
                       "THERMOCTL_PASSKEY_ORIGIN"),
            ],
            weiter=("Eigene Passkeys", "/passkeys"),
        )
    )

    # --- Benachrichtigungen --------------------------------------------------
    hat_webhook = einstellungen.notify_webhook is not None
    liste.append(
        Schnittstelle(
            "benachrichtigung",
            "Benachrichtigungen",
            "Meldet Störungen an einen Webhook — ausgefallene Sensoren, stille Brücke.",
            "eingerichtet" if hat_webhook else "aus",
            (
                "Störungen gehen an den hinterlegten Webhook."
                if hat_webhook
                else "Kein Webhook hinterlegt. Störungen stehen dann nur im Protokoll "
                "und in der Oberfläche."
            ),
            [
                Angabe("Webhook", einstellungen.notify_webhook or "—",
                       "Umgebung" if hat_webhook else "Standard",
                       "THERMOCTL_NOTIFY_WEBHOOK"),
                Angabe("Token", _ja_nein(einstellungen.notify_webhook_token is not None),
                       "Umgebung" if einstellungen.notify_webhook_token else "Standard",
                       "THERMOCTL_NOTIFY_WEBHOOK_TOKEN"),
            ],
        )
    )

    # --- Meross --------------------------------------------------------------
    hat_meross = einstellungen.meross_email is not None
    liste.append(
        Schnittstelle(
            "meross",
            "Meross",
            "Schaltsteckdosen als zweiter Aktortyp neben den Zigbee-Ventilen.",
            "eingerichtet" if hat_meross else "aus",
            (
                "Zugangsdaten hinterlegt."
                if hat_meross
                else "Keine Zugangsdaten hinterlegt."
            ),
            [
                Angabe("Konto", einstellungen.meross_email or "—",
                       "Umgebung" if hat_meross else "Standard", "THERMOCTL_MEROSS_EMAIL"),
                Angabe("Passwort", _ja_nein(einstellungen.meross_password is not None),
                       "Umgebung" if einstellungen.meross_password else "Standard",
                       "THERMOCTL_MEROSS_PASSWORD"),
            ],
            hinweis=(
                "Der Adapter ist gebaut, sein Nutzlastaufbau ist aber eine begründete "
                "Annahme und lief nie gegen ein echtes Konto."
            ),
        )
    )

    # --- Home Assistant ------------------------------------------------------
    liste.append(
        Schnittstelle(
            "homeassistant",
            "Home Assistant",
            "Meldet jede Zone als Thermostat an, über die MQTT-Discovery.",
            "ungebaut",
            "Die Nutzlast ist gebaut und geprüft, das Senden noch nicht: Eine Zone, die "
            "sich in Home Assistant als Thermostat anmeldet, obwohl thermoctl im "
            "Trockenlauf niemanden schaltet, wäre eine Zusage, die niemand einlöst.",
            [],
            hinweis="Kommt mit dem Scharfschalten in Phase 4.",
            weiter=("Betriebszustand", "/steuerung"),
        )
    )

    return liste
