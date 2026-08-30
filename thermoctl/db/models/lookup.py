from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class _Lookup(Base):
    """Common shape of all lookup tables: an id, a code, a plain-text label."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


class OperatingMode(_Lookup):
    """auto, manual, off. 'off' means frost protection, not powered off."""

    __tablename__ = "operating_mode"


class Integration(_Lookup):
    """How a device is reached: zigbee2mqtt, meross."""

    __tablename__ = "integration"


class DeviceCapability(_Lookup):
    """What a device can do: temperature, switch, setpoint_display, contact, battery."""

    __tablename__ = "device_capability"


class DeviceRole(_Lookup):
    """What a device is used for in a zone: actuator, window_contact, controller."""

    __tablename__ = "device_role"


class SensorStatus(_Lookup):
    """Validity of the sensor data currently used for a zone."""

    __tablename__ = "sensor_status"


class ControllerCommand(_Lookup):
    """What a controller can trigger: warmer, colder, boost, operating mode.

    A lookup table and not an enumeration in code: Principle 3 forbids ENUM, and the
    interface should be able to offer the possible commands from the database, without
    having to maintain a second list in the source code.
    """

    __tablename__ = "controller_command"


class ChannelKind(_Lookup):
    """Source or effect of a configured controller channel."""

    __tablename__ = "channel_kind"


class ActorSource(_Lookup):
    """Via which adapter something happened: web, api, mcp, cli, system."""

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
# Deliberately kept small: what a button press on the wall triggers should be
# explainable in one sentence. Everything beyond that belongs at a place where you can
# see what you are doing -- not on a button someone presses in passing.
CONTROLLER_COMMANDS = [
    ("setpoint_up", "Wärmer"),
    ("setpoint_down", "Kälter"),
    ("boost", "Nächste Schaltung vorziehen"),
    ("mode_off", "Betriebsart Aus"),
    ("mode_auto", "Betriebsart Automatik"),
]
CHANNEL_KINDS = [
    ("sensor_temperature", "Temperatur eines Geräts"),
    ("zone_temperature", "Ist-Temperatur der Zone"),
    ("zone_setpoint", "Sollwert der Zone"),
    ("fixed", "Fester Wert"),
    ("operating_mode", "Betriebsart der Zone"),
]
ACTOR_SOURCES = [
    ("web", "Weboberfläche"),
    ("api", "REST-API"),
    ("mcp", "MCP"),
    ("cli", "Kommandozeile"),
    ("system", "System"),
]
# "kiosk" is deliberately NOT listed here, even though it is a fully real actor
# source from here on (see the migration `d07073d9abdf`). The very first migration,
# `3685e30419a4_nachschlagetabellen.py`, imports this constant *live* instead of
# embedding a frozen literal list the way every migration after it does (see the
# comment in `8b2d6e8a7f10_schema_schattenbetrieb.py` for why that discipline
# exists) -- so growing `ACTOR_SOURCES` here would make that decade-old migration
# insert "kiosk" too, colliding with the dedicated migration that is supposed to be
# the only place that row comes from. Tests that need it call
# `tests.helpers.source(session, "kiosk")`, the same way they already do for every
# other source.

# (code, description, zone-scoped) — the list from section 2.6 of the specification
#
# The description is **visible text**, not a note: it appears on the group page next
# to each checkbox and is the first thing anyone reads there. Hence with umlauts,
# unlike comments and docstrings in this project.
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
    # A dedicated permission instead of `setting.manage`: arming is the only setting
    # whose flip immediately moves a valve. Whoever is allowed to maintain time zone and
    # retention period should not incidentally be able to arm the heating with it.
    ("control.arm", "Die Regelung scharf schalten", False),
]
