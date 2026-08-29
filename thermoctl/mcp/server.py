from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.auth.tokens import token_aufloesen
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.messwert import DeviceHealth
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.authz import principal_fuer_token, require, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    aufgeloester_sollwert,
    uebersteuerung_anlegen,
)
from thermoctl.domain.schedule import (
    uebersteuerung_aufheben as domaene_uebersteuerung_aufheben,
)


class _McpServer(Protocol):
    def tool(
        self, name: str | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...

    def run(self, transport: str = "stdio", **kwargs: Any) -> None: ...


def _anmelden(session: Session, klartext: str) -> tuple[ApiToken, Principal]:
    token = token_aufloesen(session, klartext)
    if token is None:
        raise PermissionError("Ungueltiges oder nicht mehr gueltiges MCP-Token")
    return token, principal_fuer_token(session, token)


def _sichtbare_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (
            eintrag
            for eintrag in visible_zones(session, principal, "zone.read")
            if eintrag.id == zone_id
        ),
        None,
    )
    if zone is None:
        raise LookupError("Zone nicht gefunden")
    require(principal, "zone.read", zone_id)
    return zone


def _dezimal(wert: Decimal | None) -> str | None:
    return None if wert is None else str(wert)


def _zeitpunkt(wert: datetime | None) -> str | None:
    return None if wert is None else wert.isoformat()


def zonen_auflisten(session: Session, klartext: str) -> list[dict[str, object]]:
    """Listet ausschliesslich die fuer das Token sichtbaren Zonen auf."""
    _token, principal = _anmelden(session, klartext)
    zonen = visible_zones(session, principal, "zone.read")
    if not zonen:
        require(principal, "zone.read")
    return [
        {
            "name": zone.name,
            "anzeigename": zone.display_name,
            "betriebsart": zone.operating_mode.code,
        }
        for zone in zonen
    ]


def zonenzustand(session: Session, klartext: str, zone_id: int) -> dict[str, object]:
    """Liefert den zuletzt abgeleiteten Zustand einer sichtbaren Zone."""
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    zeile = session.get(ZoneState, zone.id)
    if zeile is None:
        return {"temperatur_c": None, "messzeitpunkt": None, "sensorzustand": None}
    status = session.get(SensorStatus, zeile.sensor_status_id)
    return {
        "temperatur_c": _dezimal(zeile.temperature_c),
        "messzeitpunkt": _zeitpunkt(zeile.measured_at),
        "sensorzustand": None if status is None else status.code,
    }


def sollwert_erklaeren(
    session: Session, klartext: str, zone_id: int, jetzt: datetime | None = None
) -> dict[str, object]:
    """Reicht Wert und Begruendung der gemeinsamen Sollwertlogik durch."""
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    sollwert = aufgeloester_sollwert(session, zone, jetzt or utcnow())
    return {
        "temperatur_c": _dezimal(sollwert.temperature_c),
        "begruendung": sollwert.grund,
        "modus": sollwert.modus_code,
    }


def zeitplan_lesen(session: Session, klartext: str, zone_id: int) -> list[dict[str, object]]:
    """Liest die Schaltpunkte einer sichtbaren Zone samt Modusnamen."""
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    zeilen = session.execute(
        select(SchedulePoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == SchedulePoint.setpoint_mode_id)
        .where(SchedulePoint.zone_id == zone.id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    return [
        {
            "wochentag": punkt.weekday,
            "minute_im_tag": punkt.minute_of_day,
            "modus": modus.name,
        }
        for punkt, modus in zeilen
    ]


def sollwerte_lesen(session: Session, klartext: str, zone_id: int) -> list[dict[str, object]]:
    """Liest die in einer sichtbaren Zone gesetzten Sollwerte je Modus."""
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    zeilen = session.execute(
        select(ZoneSetpoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == ZoneSetpoint.setpoint_mode_id)
        .where(ZoneSetpoint.zone_id == zone.id)
        .order_by(SetpointMode.sort_order, SetpointMode.code)
    )
    return [
        {"modus": modus.name, "temperatur_c": _dezimal(sollwert.temperature_c)}
        for sollwert, modus in zeilen
    ]


def geraete_auflisten(session: Session, klartext: str) -> list[dict[str, object]]:
    """Listet Geraete samt Anbindung, Faehigkeiten und Lebenszeichen auf."""
    _token, principal = _anmelden(session, klartext)
    require(principal, "device.read")
    zeilen = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.external_id)
    ).all()
    faehigkeiten = session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ).all()
    nach_geraet: dict[int, list[str]] = {}
    for geraet_id, code in faehigkeiten:
        nach_geraet.setdefault(geraet_id, []).append(code)
    return [
        {
            "name": geraet.display_name,
            "anbindung": anbindung.code,
            "faehigkeiten": nach_geraet.get(geraet.id, []),
            "letzte_nachricht": None if gesund is None else _zeitpunkt(gesund.last_payload_at),
            "batterie_prozent": None if gesund is None else _dezimal(gesund.battery_percent),
        }
        for geraet, anbindung, gesund in zeilen
    ]


def schattenentscheidungen(
    session: Session, klartext: str, zone_id: int, anzahl: int = 10
) -> list[dict[str, object]]:
    """Liefert die juengsten begruendeten Schattenentscheidungen einer Zone."""
    if anzahl < 1 or anzahl > 100:
        raise ValueError("Anzahl muss zwischen 1 und 100 liegen")
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    zeilen = session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id == zone.id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(anzahl)
    )
    return [
        {
            "zeitpunkt": _zeitpunkt(zeile.decided_at),
            "ist_c": _dezimal(zeile.temperature_c),
            "soll_c": _dezimal(zeile.setpoint_c),
            "sollwert_begruendung": zeile.setpoint_reason,
            "wuerde_heizen": zeile.would_heat,
            "ergebnis": zeile.outcome_code,
            "begruendung": zeile.reason,
        }
        for zeile in zeilen
    ]


def uebersteuern(
    session: Session,
    klartext: str,
    zone_id: int,
    temperatur_c: Decimal,
    endet_am: datetime | None = None,
) -> dict[str, object]:
    """Legt ueber die gemeinsame Domaenenfunktion eine Uebersteuerung an."""
    token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    require(principal, "override.create", zone_id)
    eintrag = uebersteuerung_anlegen(
        session,
        zone,
        temperatur_c,
        endet_am,
        user_id=principal.user_id,
        token_id=token.id,
    )
    return {
        "zone": zone.name,
        "temperatur_c": _dezimal(eintrag.temperature_c),
        "endet_am": _zeitpunkt(eintrag.ends_at),
    }


def uebersteuerung_aufheben(session: Session, klartext: str, zone_id: int) -> dict[str, object]:
    """Hebt die aktive Uebersteuerung ueber die gemeinsame Domaenenfunktion auf."""
    _token, principal = _anmelden(session, klartext)
    zone = _sichtbare_zone(session, principal, zone_id)
    require(principal, "override.cancel", zone_id)
    aufgehoben = domaene_uebersteuerung_aufheben(session, zone)
    return {"zone": zone.name, "aufgehoben": aufgehoben is not None}


def _mcp_server_klasse() -> Callable[[str], _McpServer]:
    try:
        # Zwei Pfade, weil die beiden verbreiteten MCP-Fassungen die Serverklasse an
        # unterschiedlichen Stellen fuehren. Welcher greift, haengt an der installierten
        # Fassung — die Abdeckung sieht deshalb immer nur einen der beiden.
        try:
            modul = import_module("mcp.server.mcpserver")
            return cast(Callable[[str], _McpServer], modul.MCPServer)  # pragma: no cover
        except ModuleNotFoundError:
            modul = import_module("mcp.server.fastmcp")
            return cast(Callable[[str], _McpServer], modul.FastMCP)  # pragma: no cover
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Das optionale MCP-Paket fehlt. Installation: pip install 'thermoctl[mcp]'"
        ) from exc


def _werkzeuge_registrieren(
    server: _McpServer, factory: sessionmaker[Session], klartext: str
) -> None:
    @server.tool(name="zonen_auflisten")
    def mcp_zonen_auflisten() -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return zonen_auflisten(session, klartext)

    @server.tool(name="zonenzustand")
    def mcp_zonenzustand(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return zonenzustand(session, klartext, zone_id)

    @server.tool(name="sollwert_erklaeren")
    def mcp_sollwert_erklaeren(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return sollwert_erklaeren(session, klartext, zone_id)

    @server.tool(name="zeitplan_lesen")
    def mcp_zeitplan_lesen(zone_id: int) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return zeitplan_lesen(session, klartext, zone_id)

    @server.tool(name="sollwerte_lesen")
    def mcp_sollwerte_lesen(zone_id: int) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return sollwerte_lesen(session, klartext, zone_id)

    @server.tool(name="geraete_auflisten")
    def mcp_geraete_auflisten() -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return geraete_auflisten(session, klartext)

    @server.tool(name="schattenentscheidungen")
    def mcp_schattenentscheidungen(zone_id: int, anzahl: int = 10) -> list[dict[str, object]]:
        with session_scope(factory) as session:
            return schattenentscheidungen(session, klartext, zone_id, anzahl)

    @server.tool(name="uebersteuern")
    def mcp_uebersteuern(
        zone_id: int, temperatur_c: Decimal, endet_am: datetime | None = None
    ) -> dict[str, object]:
        with session_scope(factory) as session:
            return uebersteuern(session, klartext, zone_id, temperatur_c, endet_am)

    @server.tool(name="uebersteuerung_aufheben")
    def mcp_uebersteuerung_aufheben(zone_id: int) -> dict[str, object]:
        with session_scope(factory) as session:
            return uebersteuerung_aufheben(session, klartext, zone_id)


def main() -> None:
    """Startet den authentifizierten MCP-Server ueber stdio."""
    einstellungen = get_settings()
    if einstellungen.mcp_token is None:
        raise SystemExit("THERMOCTL_MCP_TOKEN fehlt; der MCP-Server startet nicht ohne Anmeldung.")
    # Ab hier laeuft der Prozess bis zum Abbruch als stdio-Server. Das ist Verdrahtung,
    # kein Verhalten: Jeder einzelne Bestandteil ist geprueft — die Tokenpruefung darueber,
    # die Registrierung in `test_registrierte_mcp_werkzeuge_rufen_die_adapterfunktionen_auf`
    # und jedes Werkzeug einzeln. Ein Test dieser Zeilen muesste einen echten stdio-Server
    # starten und wieder abwuergen und pruefte damit die Bibliothek, nicht uns.
    klartext = einstellungen.mcp_token.get_secret_value()  # pragma: no cover
    server_klasse = _mcp_server_klasse()  # pragma: no cover
    engine = create_engine_from_settings(einstellungen)  # pragma: no cover
    factory = session_factory(engine)  # pragma: no cover
    server = server_klasse("thermoctl")  # pragma: no cover
    _werkzeuge_registrieren(server, factory, klartext)  # pragma: no cover
    try:  # pragma: no cover
        server.run(transport="stdio")
    finally:
        engine.dispose()  # pragma: no cover
