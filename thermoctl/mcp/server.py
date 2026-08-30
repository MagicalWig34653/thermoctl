from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.auth.tokens import resolve_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import principal_for_token, require, visible_zones
from thermoctl.domain.control import LIMITS, arm, settings
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import boost as domain_boost
from thermoctl.domain.schedule import (
    cancel_override as domain_cancel_override,
)
from thermoctl.domain.schedule import (
    create_override,
    resolved_setpoint,
)
from thermoctl.domain.schedule import (
    move_schedule_point as domain_move_schedule_point,
)
from thermoctl.domain.zone_settings import (
    PARAMETERS,
    control_parameters,
)
from thermoctl.domain.zone_settings import (
    set_parameter as domain_set_parameter,
)


class _McpServer(Protocol):
    def tool(
        self, name: str | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...

    def run(self, transport: str = "stdio", **kwargs: Any) -> None: ...


def _log_in(session: Session, plaintext: str) -> tuple[ApiToken, Principal]:
    token = resolve_token(session, plaintext)
    if token is None:
        raise PermissionError("Ungueltiges oder nicht mehr gueltiges MCP-Token")
    return token, principal_for_token(session, token)


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (
            entry
            for entry in visible_zones(session, principal, "zone.read")
            if entry.id == zone_id
        ),
        None,
    )
    if zone is None:
        raise LookupError("Zone nicht gefunden")
    require(principal, "zone.read", zone_id)
    return zone


def _dezimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def list_zones(session: Session, plaintext: str) -> list[dict[str, object]]:
    """Listet ausschliesslich die fuer das Token sichtbaren Zonen auf."""
    _token, principal = _log_in(session, plaintext)
    zones = visible_zones(session, principal, "zone.read")
    if not zones:
        require(principal, "zone.read")
    return [
        {
            "name": zone.name,
            "display_name": zone.display_name,
            "operating_mode": zone.operating_mode.code,
        }
        for zone in zones
    ]


def zone_state(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Liefert den zuletzt abgeleiteten Zustand einer sichtbaren Zone."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    zeile = session.get(ZoneState, zone.id)
    if zeile is None:
        return {"temperature_c": None, "measured_at": None, "sensor_state": None}
    status = session.get(SensorStatus, zeile.sensor_status_id)
    return {
        "temperature_c": _dezimal(zeile.temperature_c),
        "measured_at": _moment(zeile.measured_at),
        "sensor_state": None if status is None else status.code,
    }


def explain_setpoint(
    session: Session, plaintext: str, zone_id: int, now: datetime | None = None
) -> dict[str, object]:
    """Reicht Wert und Begruendung der gemeinsamen Sollwertlogik durch."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    setpoint = resolved_setpoint(session, zone, now or utcnow())
    return {
        "temperature_c": _dezimal(setpoint.temperature_c),
        "reason": setpoint.grund,
        "mode": setpoint.mode_code,
    }


def read_schedule(session: Session, plaintext: str, zone_id: int) -> list[dict[str, object]]:
    """Liest die Schaltpunkte einer sichtbaren Zone samt Modusnamen."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    zeilen = session.execute(
        select(SchedulePoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == SchedulePoint.setpoint_mode_id)
        .where(SchedulePoint.zone_id == zone.id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    return [
        {
            "weekday": point.weekday,
            "minute_of_day": point.minute_of_day,
            "mode": mode.name,
        }
        for point, mode in zeilen
    ]


def read_setpoints(session: Session, plaintext: str, zone_id: int) -> list[dict[str, object]]:
    """Liest die in einer sichtbaren Zone gesetzten Sollwerte je Modus."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    zeilen = session.execute(
        select(ZoneSetpoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == ZoneSetpoint.setpoint_mode_id)
        .where(ZoneSetpoint.zone_id == zone.id)
        .order_by(SetpointMode.sort_order, SetpointMode.code)
    )
    return [
        {"mode": mode.name, "temperature_c": _dezimal(setpoint.temperature_c)}
        for setpoint, mode in zeilen
    ]


def list_devices(session: Session, plaintext: str) -> list[dict[str, object]]:
    """Listet Geraete samt Anbindung, Faehigkeiten und Lebenszeichen auf."""
    _token, principal = _log_in(session, plaintext)
    require(principal, "device.read")
    zeilen = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.external_id)
    ).all()
    capabilities = session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ).all()
    by_device: dict[int, list[str]] = {}
    for device_id, code in capabilities:
        by_device.setdefault(device_id, []).append(code)
    return [
        {
            "name": device.display_name,
            "integration": integration.code,
            "capabilities": by_device.get(device.id, []),
            "letzte_nachricht": None if gesund is None else _moment(gesund.last_payload_at),
            "batterie_prozent": None if gesund is None else _dezimal(gesund.battery_percent),
        }
        for device, integration, gesund in zeilen
    ]


def shadow_decisions(
    session: Session, plaintext: str, zone_id: int, count: int = 10
) -> list[dict[str, object]]:
    """Liefert die juengsten begruendeten Schattenentscheidungen einer Zone."""
    if count < 1 or count > 100:
        raise ValueError("Anzahl muss zwischen 1 und 100 liegen")
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    zeilen = session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id == zone.id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(count)
    )
    return [
        {
            "moment": _moment(zeile.decided_at),
            "ist_c": _dezimal(zeile.temperature_c),
            "soll_c": _dezimal(zeile.setpoint_c),
            "sollwert_begruendung": zeile.setpoint_reason,
            "would_heat": zeile.would_heat,
            "outcome": zeile.outcome_code,
            "reason": zeile.reason,
        }
        for zeile in zeilen
    ]


def override_zone(
    session: Session,
    plaintext: str,
    zone_id: int,
    temperature_c: Decimal,
    endet_am: datetime | None = None,
) -> dict[str, object]:
    """Legt ueber die gemeinsame Domaenenfunktion eine Uebersteuerung an."""
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.create", zone_id)
    entry = create_override(
        session,
        zone,
        temperature_c,
        endet_am,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "temperature_c": _dezimal(entry.temperature_c),
        "ends_at": _moment(entry.ends_at),
    }


def cancel_override(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Hebt die aktive Uebersteuerung ueber die gemeinsame Domaenenfunktion auf."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.cancel", zone_id)
    aufgehoben = domain_cancel_override(session, zone)
    return {"zone": zone.name, "cancelled": aufgehoben is not None}


def boost(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Zieht die naechste Schaltung vor -- ueber die gemeinsame Domaenenfunktion.

    Dasselbe Recht wie eine Uebersteuerung, weil es eine ist: nur eine, deren Wert und
    Ende der Zeitplan bestimmt statt der Aufrufer. Fuer ein Sprachmodell ist das die
    verlaesslichere Form von "mach es hier waermer" -- es muss weder eine Temperatur
    noch eine Dauer raten, und nach dem Schaltpunkt raeumt sich der Eingriff selbst weg.
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.create", zone_id)
    result = domain_boost(
        session,
        zone,
        utcnow(),
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "mode": result.mode_code,
        "temperature_c": _dezimal(result.temperature),
        "valid_until": _moment(result.bis),
    }


def read_control_parameters(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Die wirksamen Regelparameter einer Zone, samt ihrer Grenzen.

    Die Grenzen stehen mit in der Antwort, weil ein Sprachmodell sie sonst raten muesste:
    Ohne sie waere jeder Schreibversuch ein Versuch, und "0,05 Kelvin Hysterese" saehe
    fuer ein Modell so plausibel aus wie "0,5".
    """
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    wirksam = control_parameters(session, zone)
    return {
        "zone": zone.name,
        "parameter": [
            {
                "name": beschreibung.name,
                "label": beschreibung.label,
                "unit": beschreibung.einheit,
                "value": str(getattr(wirksam, beschreibung.name)),
                "own_value": getattr(zone, beschreibung.name) is not None,
                "minimum": str(beschreibung.minimum),
                "maximum": str(beschreibung.maximum),
            }
            for beschreibung in PARAMETERS
        ],
    }


def set_control_parameters(
    session: Session, plaintext: str, zone_id: int, name: str, value: Decimal
) -> dict[str, object]:
    """Setzt **einen** Regelparameter und laesst die uebrigen, wie sie sind.

    `zone.manage`, nicht `override.create`: Ein Regelparameter wirkt dauerhaft und auf
    jede kuenftige Entscheidung, eine Uebersteuerung nur bis zum naechsten Schaltpunkt.
    """
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.manage", zone_id)
    gesetzt = domain_set_parameter(
        session, zone, name, value, user_id=principal.user_id, source="mcp"
    )
    return {"zone": zone.name, "name": name, "value": _dezimal(gesetzt)}


def read_control(session: Session, plaintext: str) -> dict[str, object]:
    """Der Betriebszustand der Anlage samt der Vorgaben, von denen jede Zone erbt.

    Die wichtigste Frage, die ein Assistent ueber diese Anlage stellen kann, ist
    "schaltet sie gerade wirklich?" -- eine Entscheidung im Schattenbetrieb ist eine
    Behauptung, eine im scharfen Betrieb bewegt ein Ventil.
    """
    _token, principal = _log_in(session, plaintext)
    require(principal, "zone.read")
    zeile = settings(session)
    return {
        "armed": zeile.control_armed,
        "timezone": zeile.timezone,
        **{feld: str(getattr(zeile, feld)) for feld in LIMITS},
    }


def force_dry_run(
    session: Session, plaintext: str, reason: str = ""
) -> dict[str, object]:
    """Nimmt die Regelung in den Trockenlauf zurueck.

    **Nur diese Richtung.** Scharfschalten gibt es hier bewusst nicht, obwohl REST und
    Oberflaeche es koennen: Der MCP-Server spricht fuer ein Sprachmodell, und die
    Begruendung, die die Domaene beim Scharfschalten verlangt, ist fuer ein Modell keine
    Huerde -- sie ist genau die Sorte Text, die es muehelos erzeugt. Die Sperre waere
    damit eine Formalie statt einer Entscheidung. Zurueck in den Trockenlauf ist
    dagegen immer die sichere Richtung und soll jedem offenstehen, der die Anlage
    bedienen darf. Wer die Anlage scharf schalten will, tut das in der Oberflaeche oder
    ueber die REST-Schnittstelle, wo ein Mensch am Knopf steht.
    Nachzulesen in docs/offene-entscheidungen.md.
    """
    token, principal = _log_in(session, plaintext)
    require(principal, "control.arm")
    changed = arm(
        session,
        False,
        reason=reason,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {"armed": False, "changed": changed}


def move_schedule_point(
    session: Session, plaintext: str, zone_id: int, point_id: int, weekday: int, minute: int
) -> dict[str, object]:
    """Setzt einen Zeitplanpunkt auf einen anderen Zeitpunkt.

    Dieselbe Domaenenfunktion wie das Ziehen in der Wochenansicht -- der Punkt behaelt
    seine Kennung, und das Audit-Protokoll zeigt eine Verschiebung statt eines Loeschens
    mit anschliessendem Anlegen.
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "schedule.manage", zone_id)
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone.id:
        raise ValueError("Zeitplanpunkt nicht gefunden")
    domain_move_schedule_point(
        session,
        zone,
        point,
        weekday=weekday,
        minute=minute,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "point_id": point.id,
        "weekday": point.weekday,
        "minute": point.minute_of_day,
    }


def _mcp_server_class() -> Callable[[str], _McpServer]:
    try:
        # Zwei Pfade, weil die beiden verbreiteten MCP-Fassungen die Serverklasse an
        # unterschiedlichen Stellen fuehren. Welcher greift, haengt an der installierten
        # Fassung — die Abdeckung sieht deshalb immer nur einen der beiden.
        try:
            module = import_module("mcp.server.mcpserver")
            return cast(Callable[[str], _McpServer], module.MCPServer)  # pragma: no cover
        except ModuleNotFoundError:
            module = import_module("mcp.server.fastmcp")
            return cast(Callable[[str], _McpServer], module.FastMCP)  # pragma: no cover
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Das optionale MCP-Paket fehlt. Installation: pip install 'thermoctl[mcp]'"
        ) from exc


def _register_tools(
    server: _McpServer, factory: sessionmaker[Session], plaintext: str
) -> None:
    @server.tool(name="list_zones")
    def mcp_list_zones() -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return list_zones(session, plaintext)

    @server.tool(name="zone_state")
    def mcp_zone_state(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return zone_state(session, plaintext, zone_id)

    @server.tool(name="explain_setpoint")
    def mcp_explain_setpoint(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return explain_setpoint(session, plaintext, zone_id)

    @server.tool(name="read_schedule")
    def mcp_read_schedule(zone_id: int) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return read_schedule(session, plaintext, zone_id)

    @server.tool(name="read_setpoints")
    def mcp_read_setpoints(zone_id: int) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return read_setpoints(session, plaintext, zone_id)

    @server.tool(name="list_devices")
    def mcp_list_devices() -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return list_devices(session, plaintext)

    @server.tool(name="shadow_decisions")
    def mcp_shadow_decisions(zone_id: int, count: int = 10) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return shadow_decisions(session, plaintext, zone_id, count)

    @server.tool(name="override")
    def mcp_override(
        zone_id: int, temperature_c: Decimal, endet_am: datetime | None = None
    ) -> dict[str, object]:
        with session_scope(factory) as session:
            return override_zone(session, plaintext, zone_id, temperature_c, endet_am)

    @server.tool(name="cancel_override")
    def mcp_cancel_override(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return cancel_override(session, plaintext, zone_id)

    @server.tool(name="boost")
    def mcp_boost(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return boost(session, plaintext, zone_id)

    @server.tool(name="read_control_parameters")
    def mcp_read_control_parameters(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return read_control_parameters(session, plaintext, zone_id)

    @server.tool(name="set_control_parameters")
    def mcp_set_control_parameters(zone_id: int, name: str, value: Decimal) -> dict[str, object]:
        with session_scope(factory) as session:
            return set_control_parameters(session, plaintext, zone_id, name, value)

    @server.tool(name="read_control")
    def mcp_read_control() -> dict[str, object]:
        with session_scope(factory) as session:
            return read_control(session, plaintext)

    @server.tool(name="force_dry_run")
    def mcp_force_dry_run(reason: str = "") -> dict[str, object]:
        with session_scope(factory) as session:
            return force_dry_run(session, plaintext, reason)

    @server.tool(name="move_schedule_point")
    def mcp_move_schedule_point(
        zone_id: int, point_id: int, weekday: int, minute: int
    ) -> dict[str, object]:
        with session_scope(factory) as session:
            return move_schedule_point(
                session, plaintext, zone_id, point_id, weekday, minute
            )


def main() -> None:
    """Startet den authentifizierten MCP-Server ueber stdio."""
    settings = get_settings()
    if settings.mcp_token is None:
        raise SystemExit("THERMOCTL_MCP_TOKEN fehlt; der MCP-Server startet nicht ohne Anmeldung.")
    # Ab hier laeuft der Prozess bis zum Abbruch als stdio-Server. Das ist Verdrahtung,
    # kein Verhalten: Jeder einzelne Bestandteil ist geprueft — die Tokenpruefung darueber,
    # die Registrierung in `test_registrierte_mcp_werkzeuge_rufen_die_adapterfunktionen_auf`
    # und jedes Werkzeug einzeln. Ein Test dieser Zeilen muesste einen echten stdio-Server
    # starten und wieder abwuergen und pruefte damit die Bibliothek, nicht uns.
    plaintext = settings.mcp_token.get_secret_value()  # pragma: no cover
    server_class = _mcp_server_class()  # pragma: no cover
    engine = create_engine_from_settings(settings)  # pragma: no cover
    factory = session_factory(engine)  # pragma: no cover
    server = server_class("thermoctl")  # pragma: no cover
    _register_tools(server, factory, plaintext)  # pragma: no cover
    try:  # pragma: no cover
        server.run(transport="stdio")
    finally:
        engine.dispose()  # pragma: no cover
