from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class _Lookup(Base):
    """Gemeinsame Form aller Nachschlagetabellen: eine Kennung, ein Code, ein Klartext."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


class OperatingMode(_Lookup):
    """auto, manual, off. 'off' heisst Frostschutz, nicht stromlos."""

    __tablename__ = "operating_mode"


class Integration(_Lookup):
    """Wie ein Geraet erreicht wird: zigbee2mqtt, meross."""

    __tablename__ = "integration"


class DeviceCapability(_Lookup):
    """Was ein Geraet kann: temperature, switch, setpoint_display, contact, battery."""

    __tablename__ = "device_capability"


class DeviceRole(_Lookup):
    """Wozu ein Geraet in einer Zone dient: actuator, window_contact, controller."""

    __tablename__ = "device_role"


class SensorStatus(_Lookup):
    """Gueltigkeit der aktuell fuer eine Zone verwendeten Sensordaten."""

    __tablename__ = "sensor_status"


class ControllerCommand(_Lookup):
    """Was ein Bediengeraet ausloesen kann: waermer, kaelter, Boost, Betriebsart.

    Eine Nachschlagetabelle und keine Aufzaehlung im Code: Grundsatz 3 verbietet ENUM,
    und die Oberflaeche soll die moeglichen Befehle aus der Datenbank anbieten koennen,
    ohne eine zweite Liste im Quelltext pflegen zu muessen.
    """

    __tablename__ = "controller_command"


class ActorSource(_Lookup):
    """Ueber welchen Adapter etwas geschah: web, api, mcp, cli, system."""

    __tablename__ = "actor_source"


class Permission(Base):
    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    is_zone_scoped: Mapped[bool] = mapped_column(Boolean, nullable=False)


OPERATING_MODES = [("auto", "Automatik"), ("manual", "Manuell"), ("off", "Aus")]
INTEGRATIONS = [("zigbee2mqtt", "Zigbee2MQTT"), ("meross", "Meross")]
DEVICE_CAPABILITIES = [
    ("temperature", "Temperaturmessung"),
    ("switch", "Schaltausgang"),
    ("setpoint_display", "Sollwertanzeige"),
    ("contact", "Kontakt"),
    ("battery", "Batteriestand"),
    ("humidity", "Luftfeuchtigkeit"),
    ("illuminance", "Beleuchtungsstärke"),
    ("occupancy", "Anwesenheit"),
    ("link_quality", "Verbindungsqualität"),
    ("power", "Leistung"),
    ("energy", "Energie"),
    ("valve_position", "Ventilstellung"),
    ("setpoint", "Sollwert"),
    ("availability", "Erreichbarkeit"),
    ("soil_moisture", "Bodenfeuchte"),
    ("action", "Tastendruck"),
]
SENSOR_STATUS = [
    ("ok", "In Ordnung"),
    ("veraltet", "Veraltet"),
    ("keine_quelle", "Keine Quelle"),
]
DEVICE_ROLES = [
    ("actuator", "Aktor"),
    ("window_contact", "Fensterkontakt"),
    ("controller", "Bediengerät"),
]
# Bewusst klein gehalten: Was ein Tastendruck an der Wand ausloest, soll man in einem
# Satz erklaeren koennen. Alles Weitere gehoert an eine Stelle, an der man sieht, was man
# tut -- nicht auf einen Knopf, den jemand im Vorbeigehen drueckt.
CONTROLLER_COMMANDS = [
    ("setpoint_up", "Wärmer"),
    ("setpoint_down", "Kälter"),
    ("boost", "Nächste Schaltung vorziehen"),
    ("mode_off", "Betriebsart Aus"),
    ("mode_auto", "Betriebsart Automatik"),
]
ACTOR_SOURCES = [
    ("web", "Weboberfläche"),
    ("api", "REST-API"),
    ("mcp", "MCP"),
    ("cli", "Kommandozeile"),
    ("system", "System"),
]

# (code, beschreibung, zonenbezogen) — die Liste aus Abschnitt 2.6 der Spezifikation
#
# Die Beschreibung ist **sichtbarer Text**, keine Notiz: Sie steht auf der Gruppenseite
# neben jedem Kästchen und ist dort das Erste, was jemand liest. Deshalb mit Umlauten,
# anders als Kommentare und Docstrings in diesem Projekt.
PERMISSIONS: list[tuple[str, str, bool]] = [
    ("zone.read", "Zonen und ihren Zustand sehen", True),
    ("zone.manage", "Zonen anlegen, ändern, löschen", True),
    ("setpoint.write", "Sollwerte je Modus ändern", True),
    ("schedule.manage", "Zeitpläne ändern", True),
    ("override.create", "Übersteuern", True),
    ("override.cancel", "Fremde Übersteuerung aufheben", True),
    ("device.read", "Geräte und Zuordnungen sehen", True),
    ("device.manage", "Geräte zuordnen, tauschen, entfernen", True),
    ("mode.manage", "Sollwert-Modi anlegen und ändern", False),
    ("setting.manage", "Globale Einstellungen ändern", False),
    ("user.manage", "Benutzer verwalten", False),
    ("group.manage", "Gruppen und Rechte verwalten", False),
    ("token.self", "Eigene Tokens ausstellen und widerrufen", False),
    ("token.manage", "Fremde Tokens verwalten", False),
    ("audit.read", "Audit-Protokoll einsehen", False),
    # Eigenes Recht statt `setting.manage`: Scharfschalten ist die einzige Einstellung,
    # deren Umlegen unmittelbar ein Ventil bewegt. Wer Zeitzone und Aufbewahrungsdauer
    # pflegen darf, soll damit nicht nebenbei die Heizung scharf schalten koennen.
    ("control.arm", "Die Regelung scharf schalten", False),
]
