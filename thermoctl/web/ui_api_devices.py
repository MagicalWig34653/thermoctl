"""Geräte für die Svelte-Oberfläche (`/ui-api`): Überblick, Anlagenbild, Zuordnungskarte,
Zuordnung je Raum, Datenfluss, Bediengeräte, Schaltprotokoll.

Dünner Adapter (Grundsatz 6): Die Antworten kommen aus denselben Domänenfunktionen und
Lesemodellen wie zuvor die HTML-Oberfläche (`build_mapping_map`, `plant_diagram`, `build_data_flow`,
`build_device_survey`, `list_commands`, `seen_actions` ...); geschrieben wird mit denselben
Funktionen (`connect_ports`, `assign_device`, `set_thermostat_mode`, `swap_device`,
`set_binding`, `configure_channel` ...). Hier steht keine Fachregel.

Rechte werden in jedem Endpunkt neu geprüft, danach das Profil (Anlagensicht). Eine Zone,
die es nicht gibt, und eine, die der Principal nicht sehen darf, antworten mit demselben
404; sichtbar, aber ohne `device.manage` ist ein 403. Ein Fehlschlag löst eine Ausnahme aus,
damit die Transaktion zurückgerollt wird (`get_session`).

Fehler sind immer `{"detail": <deutscher Satz>, "code": <maschinenlesbar>}`, optional
`"field"`. Die Gründe der Domäne tragen den Code ihrer Ausnahmeklasse; Konflikte
(`already_assigned`, `actuator_in_other_zone`, `stale`) sind 409.
"""

from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import ActorSource, CommandOutcome, DeviceCapability, DeviceRole
from thermoctl.db.models.state import DeviceCommand
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import Forbidden, has_permission, visible_zones
from thermoctl.domain.controller import ControllerError, controller_zones, set_binding
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.domain.data_flow import build_data_flow
from thermoctl.domain.data_flow_mermaid import render_mermaid
from thermoctl.domain.device_assignment import (
    REQUIRED_CAPABILITY,
    ActuatorInAnotherZone,
    AssignmentAlreadyExists,
    CapabilityMissing,
    actuator_elsewhere_notice,
    assign_device,
    detach_device,
    set_temperature_source,
    set_thermostat_mode,
    swap_device,
)
from thermoctl.domain.device_commands import (
    MAX_LIMIT,
    SETBACK_SOURCE,
    CommandLogEntry,
    list_commands,
)
from thermoctl.domain.mapping_map import (
    KIND_LABELS,
    SAME_SIDE,
    TECHNICAL_REASON,
    MappingMap,
    Port,
    build_mapping_map,
    can_connect,
    lines_for_mode,
)
from thermoctl.domain.plant_diagram import DevicePicture, ZonePicture, plant_diagram
from thermoctl.domain.principal import Principal
from thermoctl.domain.solar_setback_log import setback_zone_names
from thermoctl.domain.thermostat_mode import MODE_DESCRIPTIONS, MODE_LABELS, MODES
from thermoctl.domain.time import local_day_start_utc
from thermoctl.web.shared.controllers import (
    controllers_context,
    managed_device,
    require_manageable_zone,
    require_readable_device,
)
from thermoctl.web.shared.device_assignment import assignment_context
from thermoctl.web.shared.device_commands import ENTRIES_PER_PAGE, SETBACK_OUTCOME_LABELS
from thermoctl.web.shared.device_survey import build_device_survey
from thermoctl.web.shared.mapping_map import MapOutcome, connect_ports, disconnect_connection
from thermoctl.web.ui_api_reports import _day, _invalid, _page, _timezone
from thermoctl.web.ui_api_rooms import (
    RoomApiError,
    _iso,
    _require_admin_ui,
    _RoomsRoute,
)

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

Json = dict[str, Any]

#: Gründe, die ein Konflikt mit dem Zustand sind (409), nicht eine ungültige Eingabe (422).
_CONFLICT_CODES = frozenset({"stale", "already_assigned", "actuator_in_other_zone"})


# -- Hilfen -----------------------------------------------------------------------------


def _forbidden() -> RoomApiError:
    return RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _not_found(what: str) -> RoomApiError:
    return RoomApiError(404, f"{what} nicht gefunden.", "not_found")


def _need(principal: Principal, permission: str) -> None:
    """Anlagenweites Recht (ohne Zone)."""
    if not has_permission(principal, permission):
        raise _forbidden()


def _need_zones(session: Session, principal: Principal, permission: str) -> list[Zone]:
    """Die Zonen mit diesem Recht; ohne irgendein Recht ein 403 statt einer leeren Liste."""
    zones = visible_zones(session, principal, permission)
    if not zones and not has_permission(principal, permission):
        raise _forbidden()
    return zones


def _zone_for(session: Session, principal: Principal, zone_id: int, permission: str) -> Zone:
    """Unbekannt und nicht sichtbar (`device.read`/`zone.manage`) sind ununterscheidbar."""
    zone = next((z for z in visible_zones(session, principal, permission) if z.id == zone_id), None)
    if zone is None:
        raise _not_found("Raum")
    return zone


def _manage_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Sichtbar mit `device.read` (sonst 404), dann `device.manage` je Zone (sonst 403)."""
    _require_admin_ui(principal)
    zone = _zone_for(session, principal, zone_id, "device.read")
    if not has_permission(principal, "device.manage", zone.id):
        raise _forbidden()
    return zone


def _device(session: Session, device_id: int) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise _not_found("Gerät")
    return device


def _fail(outcome: MapOutcome) -> RoomApiError:
    code = outcome.code or "connection_rejected"
    status = 409 if code in _CONFLICT_CODES else outcome.status
    return RoomApiError(status, outcome.reason or "", code)


def _hidden_device_ids(session: Session, principal: Principal) -> set[int]:
    """Geräte, die der Betrachter nicht sehen darf: solche, die nur in Zonen hängen, für die
    er kein `device.read` hat. Mit anlagenweitem `device.read` ist nichts verborgen; ein Gerät
    ohne jede Zone (noch nirgends zugeordnet) verrät nichts über einen fremden Raum und
    bleibt sichtbar -- sonst ließe es sich in der Karte nie erstmals zuordnen."""
    if has_permission(principal, "device.read"):
        return set()
    visible = {z.id for z in visible_zones(session, principal, "device.read")}
    mine: set[int] = set()
    elsewhere: set[int] = set()
    for zone_id, device_id in session.execute(select(ZoneDevice.zone_id, ZoneDevice.device_id)):
        (mine if zone_id in visible else elsewhere).add(device_id)
    for zone_id, device_id in session.execute(
        select(Zone.id, Zone.temperature_source_device_id).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        (mine if zone_id in visible else elsewhere).add(device_id)
    return elsewhere - mine


def _actuator_conflict(
    session: Session, principal: Principal, device: Device, exc: ActuatorInAnotherZone
) -> RoomApiError:
    """Wie die Karte: Namen von Zonen, die der Betrachter nicht sehen darf, bleiben verborgen."""
    visible = {z.id for z in visible_zones(session, principal, "device.read")}
    where = [
        f"von Zone '{name}'" if zone_id in visible else "einer anderen Zone"
        for zone_id, name in exc.zones
    ]
    return RoomApiError(
        409, actuator_elsewhere_notice(device.display_name, where), "actuator_in_other_zone"
    )


def _decimal(value: str | None, field: str) -> Decimal | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise _invalid("Bitte eine Zahl angeben.", field) from None
    if not number.is_finite():
        raise _invalid("Bitte eine Zahl angeben.", field)
    return number


# -- Geräteüberblick und Anlagenbild (R076, R077) ----------------------------------------


@router.get("/devices")
def show_devices(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Alle Geräte mit Befunden, Auffälliges zuerst (wie die HTML-Seite; `device.read`)."""
    _require_admin_ui(principal)
    _need(principal, "device.read")
    survey, defaults = build_device_survey(session)
    return {
        "timezone": defaults.timezone if defaults is not None else None,
        "without_zone": sum(1 for entry in survey if not entry.zones),
        "devices": [
            {
                "id": entry.device_id,
                "name": entry.name,
                "model": entry.model,
                "integration": entry.integration,
                "is_group": entry.ist_group,
                "capabilities": entry.capabilities,
                "quiet_capabilities": entry.quiet_capabilities,
                "zones": entry.zones,
                "last_heard": _iso(entry.last_heard),
                "battery_percent": format(entry.battery, "f")
                if entry.battery is not None
                else None,
                "radio_quality": entry.radio_quality,
                "is_fine": entry.is_fine,
                "severity": entry.severity,
                "findings": [{"kind": f.kind, "text": f.text} for f in entry.findings],
            }
            for entry in survey
        ],
    }


def _picture(picture: DevicePicture) -> Json:
    return asdict(picture)


@router.get("/plant")
def show_plant(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Welches Gerät tut was, wo -- `device.read`, Zonen nach `zone.read`."""
    _require_admin_ui(principal)
    _need(principal, "device.read")
    diagram = plant_diagram(session, visible_zones(session, principal, "zone.read"))
    return {
        "bridge": getattr(request.app.state, "bridge_reachable", None),
        "zones": [
            {
                "zone_id": zone.zone_id,
                "name": zone.name,
                "temperature_source": (
                    _picture(zone.temperature_source) if zone.temperature_source else None
                ),
                "window_contacts": [_picture(d) for d in zone.window_contacts],
                "actuators": [_picture(d) for d in zone.actuators],
                "controllers": [_picture(d) for d in zone.controllers],
                "defects": zone.defects,
            }
            for zone in diagram.zones
        ],
        "without_zone": [_picture(d) for d in diagram.without_zone],
    }


# -- Zuordnungskarte (R078-R080) ---------------------------------------------------------

SINGLE_REASON = "Dieser Eingang nimmt nur eine Verbindung an."


def _port(port: Port) -> Json:
    return {
        "id": port.id,
        "node_id": port.node_id,
        "direction": port.direction,
        "kind": port.kind,
        "label": port.label,
        "tooltip": port.tooltip,
        "capability": port.capability,
        "accepts": sorted(port.accepts),
        "accepts_text": port.accepts_text,
        "zone_id": port.zone_id,
        "slot": port.slot,
        "device_id": port.device_id,
        "property_name": port.property_name,
        "technical": port.technical,
        "single": port.single,
        "blocked_reason": port.blocked_reason,
        "editable": port.editable,
        "valve_channel": port.valve_channel,
        "requires_actual": port.requires_actual,
        # Nur die Formulierungen, nie die Kennungen: unsichtbare Zonen bleiben verborgen.
        "actuator_elsewhere": [phrase for _zone_id, phrase in port.actuator_zones],
    }


def _map_json(session: Session, graph: MappingMap, zones: list[Zone], editable: set[int]) -> Json:
    zone_ids = [z.id for z in zones]
    valve_rows = session.execute(
        select(ZoneDevice.id, ZoneDevice.zone_id, ZoneDevice.device_id, ZoneDevice.thermostat_mode)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .join(DeviceCapabilityLink, DeviceCapabilityLink.device_id == ZoneDevice.device_id)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .where(
            ZoneDevice.zone_id.in_(zone_ids),
            DeviceRole.code == "actuator",
            DeviceCapability.code == "thermostat",
        )
        .order_by(ZoneDevice.zone_id, ZoneDevice.id)
    ).all()
    return {
        "may_edit": bool(editable),
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "ports": [_port(p) for p in node.ports],
            }
            for node in graph.nodes
        ],
        "connections": [
            {
                "id": c.id,
                "out_port": c.out_port_id,
                "in_port": c.in_port_id,
                "zone_port": c.zone_port_id,
                "device_id": c.device_id,
                "device_name": c.device_name,
                "text": c.text,
                "zone_id": c.zone_id,
                "slot": c.slot,
                "editable": c.editable,
                "from_zone": c.from_zone,
                "title": f"{c.text} → {c.device_name}"
                if c.from_zone
                else f"{c.device_name} → {c.text}",
            }
            for c in graph.connections
        ],
        # Die Tabelle der Ablehnungen: `rejections[von][nach]` ist ein Index in `reasons`.
        "reasons": list(graph.reasons),
        "rejections": graph.rejections,
        "same_side": SAME_SIDE,
        "technical_reason": TECHNICAL_REASON,
        "single_reason": SINGLE_REASON,
        "kind_labels": KIND_LABELS,
        "valve_modes": [
            {
                "code": mode,
                "label": MODE_LABELS[mode],
                "description": MODE_DESCRIPTIONS[mode],
                "lines": sorted(lines_for_mode(mode)),
            }
            for mode in MODES
        ],
        "valve_assignments": [
            {
                "assignment_id": row_id,
                "zone_id": zone_id,
                "device_id": device_id,
                "mode": mode,
                "mode_label": MODE_LABELS.get(mode, mode),
            }
            for row_id, zone_id, device_id, mode in valve_rows
        ],
    }


def _without_hidden(model: Json, hidden: set[int]) -> Json:
    """Knoten, Linien, Namen und Ablehnungstabelle ohne die verborgenen Geräte."""
    if not hidden:
        return model
    hidden_nodes = {f"dev-{device_id}" for device_id in hidden}
    nodes = [n for n in model["nodes"] if n["id"] not in hidden_nodes]
    port_ids = {p["id"] for n in nodes for p in n["ports"]}
    connections = [c for c in model["connections"] if c["device_id"] not in hidden]
    old_reasons: list[str] = model["reasons"]
    kept: dict[int, int] = {}
    rejections: dict[str, dict[str, int]] = {}
    for first, rejected in model["rejections"].items():
        if first not in port_ids:
            continue
        rejections[first] = {
            second: kept.setdefault(index, len(kept))
            for second, index in rejected.items()
            if second in port_ids
        }
    return {
        **model,
        "nodes": nodes,
        "connections": connections,
        "rejections": rejections,
        "reasons": [old_reasons[index] for index in sorted(kept, key=lambda i: kept[i])],
        "valve_assignments": [
            a for a in model["valve_assignments"] if a["device_id"] not in hidden
        ],
    }


@router.get("/devices/map")
def show_map(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Die Zuordnungskarte als Modell: Knoten, Anschlüsse, Linien, Gründe der Ablehnung."""
    _require_admin_ui(principal)
    zones = _need_zones(session, principal, "device.read")
    editable = {z.id for z in zones if has_permission(principal, "device.manage", z.id)}
    graph = build_mapping_map(session, zones, editable)
    return _without_hidden(
        _map_json(session, graph, zones, editable), _hidden_device_ids(session, principal)
    )


@router.get("/devices/map/check")
def check_connection(
    from_id: str,
    to_id: str,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Ob zwei Anschlüsse zusammenpassen (`can_connect`), samt Begründung -- ohne zu speichern."""
    _require_admin_ui(principal)
    zones = _need_zones(session, principal, "device.read")
    graph = build_mapping_map(session, zones, set())
    hidden = _hidden_device_ids(session, principal)
    ports = {p.id: p for p in graph.ports() if p.device_id not in hidden}
    first, second = ports.get(from_id), ports.get(to_id)
    if first is None or second is None:
        raise _not_found("Anschluss")
    verdict = can_connect(first, second, graph.connections)
    return {
        "ok": verdict.ok,
        "reason": verdict.reason,
        "out_port": verdict.output.id if verdict.output is not None else None,
        "in_port": verdict.input.id if verdict.input is not None else None,
    }


class ConnectIn(BaseModel):
    from_id: str
    to_id: str


class DisconnectIn(BaseModel):
    connection_id: str


def _need_manageable(session: Session, principal: Principal) -> None:
    _require_admin_ui(principal)
    _need_zones(session, principal, "device.manage")


@router.post("/devices/map/connect")
def connect(
    body: ConnectIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Verbindung ziehen; Quelle und Betriebsart folgen aus den Linien (Domäne)."""
    _need_manageable(session, principal)
    outcome = connect_ports(session, principal, body.from_id, body.to_id)
    if not outcome.ok:
        raise _fail(outcome)
    return {"ok": True, "mode": outcome.mode}


@router.post("/devices/map/disconnect")
def disconnect(
    body: DisconnectIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Verbindung lösen; bei einer von mehreren Ventillinien ändert sich die Betriebsart."""
    _need_manageable(session, principal)
    outcome = disconnect_connection(session, principal, body.connection_id)
    if not outcome.ok:
        raise _fail(outcome)
    return {"ok": True, "mode": outcome.mode, "message": outcome.message}


# -- Zuordnung je Raum (R081-R087) -------------------------------------------------------


def _named(row: Any) -> Json:
    return {"id": row.id, "code": row.code, "label": row.label}


def _zone_devices(session: Session, principal: Principal, zone: Zone) -> Json:
    """Das Lesemodell der Raumseite -- derselbe Kontext wie die HTML-Seite."""
    context = assignment_context(session, zone)
    capabilities: dict[int, list[str]] = context["capabilities"]  # type: ignore[assignment]
    picture = cast("ZonePicture", context["picture"])
    source = context["temperature_source"]
    hidden = _hidden_device_ids(session, principal)
    return {
        "zone": {"id": zone.id, "name": zone.display_name},
        "may_edit": has_permission(principal, "device.manage", zone.id),
        "temperature_source": (
            {"id": source.id, "name": source.display_name} if source is not None else None  # type: ignore[attr-defined]
        ),
        "assignments": [
            {
                "id": assignment.id,
                "device": {"id": device.id, "name": device.display_name},
                "role": _named(role),
                "sort_order": assignment.sort_order,
                "thermostat_mode": assignment.thermostat_mode,
                "thermostat_mode_label": MODE_LABELS.get(assignment.thermostat_mode),
                "is_thermostat": "thermostat" in capabilities.get(device.id, []),
            }
            for assignment, device, role in context["assignments"]  # type: ignore[attr-defined]
        ],
        "devices": [
            {
                "id": device.id,
                "name": device.display_name,
                "capabilities": capabilities.get(device.id, []),
            }
            for device in context["devices"]  # type: ignore[attr-defined]
            if device.id not in hidden
        ],
        "roles": [_named(role) for role in context["roles"]],  # type: ignore[attr-defined]
        "required_capabilities": {
            slot: sorted(codes) for slot, (codes, _why) in REQUIRED_CAPABILITY.items()
        },
        "thermostat_modes": [
            {"code": code, "label": MODE_LABELS[code], "description": MODE_DESCRIPTIONS[code]}
            for code in MODES
        ],
        "picture": {
            "temperature_source": (
                _picture(picture.temperature_source) if picture.temperature_source else None
            ),
            "window_contacts": [_picture(d) for d in picture.window_contacts],
            "actuators": [_picture(d) for d in picture.actuators],
            "controllers": [_picture(d) for d in picture.controllers],
            "defects": picture.defects,
        },
        "controllers": [
            {"device": {"id": device.id, "name": device.display_name}, "bindings": _bindings(seen)}
            for device, seen in context["controllers"]  # type: ignore[attr-defined]
        ],
        "commands": [_named(c) for c in context["commands"]],  # type: ignore[attr-defined]
    }


def _bindings(seen: list[Any]) -> list[Json]:
    return [
        {
            "action": b.action,
            "command_code": b.command_code,
            "command_name": b.command_name,
            "step_k": format(b.step_k, "f") if b.step_k is not None else None,
            "last_seen": _iso(b.last_seen),
        }
        for b in seen
    ]


@router.get("/zones/data-flow")
def show_data_flow(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Datenfluss aller Zonen (`zone.manage`, Zonenfilter wie die HTML-Seite)."""
    _require_admin_ui(principal)
    zones = _need_zones(session, principal, "zone.manage")
    return {"zones": [_flow(session, zone) for zone in zones]}


@router.get("/zones/{zone_id}/data-flow")
def show_zone_data_flow(
    zone_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    _require_admin_ui(principal)
    return _flow(session, _zone_for(session, principal, zone_id, "zone.manage"))


def _flow(session: Session, zone: Zone) -> Json:
    """Strukturierte Knoten und Kanten; Mermaid-Quelle und Kantentexte als Rückfall."""
    graph = build_data_flow(session, zone, utcnow())
    return {
        "zone": {"id": zone.id, "name": zone.display_name},
        "without_devices": graph.without_devices,
        "nodes": [asdict(node) for node in graph.nodes],
        "edges": [{**asdict(edge), "text": graph.edge_text(edge)} for edge in graph.edges],
        "mermaid": render_mermaid(graph),
    }


@router.get("/zones/{zone_id}/devices")
def show_zone_devices(
    zone_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    _require_admin_ui(principal)
    zone = _zone_for(session, principal, zone_id, "device.read")
    return _zone_devices(session, principal, zone)


class AssignIn(BaseModel):
    device_id: int
    role_code: str


class DetachIn(BaseModel):
    assignment_id: int


class RegulationIn(BaseModel):
    assignment_id: int
    thermostat_mode: str


class SourceIn(BaseModel):
    device_id: int | None = None


class SwapIn(BaseModel):
    old_device_id: int
    new_device_id: int


class ButtonIn(BaseModel):
    device_id: int
    action_code: str
    command: str | None = None
    step_k: str | None = None


def _assignment(session: Session, zone: Zone, assignment_id: int) -> ZoneDevice:
    assignment = session.get(ZoneDevice, assignment_id)
    if assignment is None or assignment.zone_id != zone.id:
        raise _not_found("Zuordnung")
    return assignment


@router.post("/zones/{zone_id}/devices/assign")
def assign(
    zone_id: int,
    body: AssignIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Gerät zuordnen: mehrere Sensoren/Fensterkontakte, ein Aktor nur in einer Zone."""
    zone = _manage_zone(session, principal, zone_id)
    device = _device(session, body.device_id)
    role = session.scalar(select(DeviceRole).where(DeviceRole.code == body.role_code))
    if role is None:
        raise RoomApiError(422, "Bitte eine bekannte Rolle auswählen.", "unknown_role", "role_code")
    try:
        assign_device(session, zone, device, role, actor_id=principal.user_id)
    except AssignmentAlreadyExists:
        raise RoomApiError(
            409,
            "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet.",
            "already_assigned",
        ) from None
    except ActuatorInAnotherZone as exc:
        raise _actuator_conflict(session, principal, device, exc) from None
    except CapabilityMissing as exc:
        raise RoomApiError(422, exc.notice, "capability_missing") from None
    return _zone_devices(session, principal, zone)


@router.post("/zones/{zone_id}/devices/detach")
def detach(
    zone_id: int,
    body: DetachIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    zone = _manage_zone(session, principal, zone_id)
    detach_device(
        session, zone, _assignment(session, zone, body.assignment_id), actor_id=principal.user_id
    )
    return _zone_devices(session, principal, zone)


@router.post("/zones/{zone_id}/devices/regulation")
def regulation(
    zone_id: int,
    body: RegulationIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Betriebsart eines Thermostatventils (`controlled`, `self_regulating`, `reading_only`)."""
    zone = _manage_zone(session, principal, zone_id)
    assignment = _assignment(session, zone, body.assignment_id)
    if body.thermostat_mode not in MODES:
        raise RoomApiError(422, "Unbekannte Betriebsart.", "unknown_mode", "thermostat_mode")
    try:
        set_thermostat_mode(
            session, zone, assignment, body.thermostat_mode, actor_id=principal.user_id
        )
    except CapabilityMissing as exc:
        raise RoomApiError(422, exc.notice, "capability_missing") from None
    return _zone_devices(session, principal, zone)


@router.post("/zones/{zone_id}/devices/source")
def temperature_source(
    zone_id: int,
    body: SourceIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Messquelle der Zone setzen; `device_id: null` löst sie."""
    zone = _manage_zone(session, principal, zone_id)
    device = _device(session, body.device_id) if body.device_id is not None else None
    try:
        set_temperature_source(session, zone, device, actor_id=principal.user_id)
    except CapabilityMissing as exc:
        raise RoomApiError(422, exc.notice, "capability_missing") from None
    return _zone_devices(session, principal, zone)


@router.post("/zones/{zone_id}/devices/swap")
def swap(
    zone_id: int,
    body: SwapIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Gerät austauschen: übernimmt alle Rollen des alten Geräts in dieser Zone."""
    zone = _manage_zone(session, principal, zone_id)
    old = _device(session, body.old_device_id)
    new = _device(session, body.new_device_id)
    try:
        swap_device(session, zone, old, new, actor_id=principal.user_id)
    except CapabilityMissing as exc:
        raise RoomApiError(422, exc.notice, "capability_missing") from None
    except ActuatorInAnotherZone as exc:
        raise _actuator_conflict(session, principal, new, exc) from None
    except ValueError as exc:
        raise RoomApiError(422, str(exc), "swap_invalid") from None
    return _zone_devices(session, principal, zone)


def _bind(session: Session, principal: Principal, device: Device, body: ButtonIn) -> None:
    """Tastenbelegung setzen oder (ohne Befehl) löschen; gilt in jeder Zone des Geräts."""
    for zone in controller_zones(session, device):
        if not has_permission(principal, "device.manage", zone.id):
            raise _forbidden()
    action = body.action_code.strip()
    if not action:
        raise _invalid("Keine Aktion angegeben.", "action_code")
    step = _decimal(body.step_k, "step_k")
    try:
        set_binding(session, device, action, (body.command or "").strip() or None, step)
    except ControllerError as exc:
        raise RoomApiError(422, str(exc), "binding_invalid") from None


@router.post("/zones/{zone_id}/devices/button")
def zone_button(
    zone_id: int,
    body: ButtonIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Taste eines Bediengeräts dieser Zone belegen.

    Strenger als die HTML-Ansicht: Das Gerät muss in dieser Zone Bediengerät sein, und
    `device.manage` wird für jede Zone verlangt, in der es hängt (die Belegung gilt dort überall).
    """
    zone = _manage_zone(session, principal, zone_id)
    device = _device(session, body.device_id)
    if zone.id not in {z.id for z in controller_zones(session, device)}:
        raise _not_found("Bediengerät")
    _bind(session, principal, device, body)
    return _zone_devices(session, principal, zone)


# -- Bediengeräte (R088-R090) ------------------------------------------------------------


def _controllers_json(session: Session, principal: Principal) -> Json:
    context = controllers_context(session, principal)
    readable = {z.id: z for z in context["zones"]}  # type: ignore[attr-defined]
    manageable: set[int] = context["manageable_ids"]  # type: ignore[assignment]
    manageable_devices: set[int] = context["manageable_device_ids"]  # type: ignore[assignment]
    properties = context["properties"]
    values = context["property_values"]
    channels = context["channels"]
    kinds = context["kinds"]
    out: list[Json] = []
    hidden = _hidden_device_ids(session, principal)
    now = utcnow()
    for device in context["controllers"]:  # type: ignore[attr-defined]
        out.append(
            {
                "id": device.id,
                "name": device.display_name,
                "manageable": device.id in manageable_devices,
                # Nur Zonen, die der Betrachter lesen darf -- keine Namen fremder Zonen.
                "zones": [
                    {"id": z.id, "name": z.display_name}
                    for z in controller_zones(session, device)
                    if z.id in readable
                ],
                "properties": [
                    {
                        "id": prop.id,
                        "name": prop.name,
                        "value_type": prop.value_type,
                        "unit": prop.unit,
                        "min_value": _num(prop.min_value),
                        "max_value": _num(prop.max_value),
                        "readable": prop.is_readable,
                        "writable": prop.is_writable,
                        "values": values.get(prop.id, []),  # type: ignore[attr-defined]
                        # Letzter vom Gerät gemeldeter Wert samt Zeitpunkt (UTC mit Zone) und
                        # Alter; die Geräte stammen aus `controllers_context`, also nur aus
                        # lesbaren Zonen. Die Anzeige rechnet in der Anlagenzeit (`timezone`).
                        "last_value": _last_value(prop),
                        "last_value_at": _iso(prop.last_value_at),
                        "last_value_age_seconds": _age_seconds(prop.last_value_at, now),
                    }
                    for prop in properties.get(device.id, [])  # type: ignore[attr-defined]
                ],
                "channels": [
                    {
                        "property_name": channel.property_name,
                        "direction": channel.direction,
                        "kind": kinds[channel.kind_id].code,  # type: ignore[index]
                        "zone_id": channel.zone_id,
                        "source_device_id": channel.source_device_id,
                        "fixed_text": channel.fixed_text,
                        "fixed_number": _num(channel.fixed_number),
                    }
                    for (device_id, _name), channel in channels.items()  # type: ignore[attr-defined]
                    if device_id == device.id
                ],
                "bindings": _bindings(context["bindings"][device.id]),  # type: ignore[index]
            }
        )
    return {
        "controllers": out,
        "timezone": _timezone(session)[0],
        "zones": [
            {"id": z.id, "name": z.display_name, "manageable": z.id in manageable}
            for z in readable.values()
        ],
        "source_devices": [
            {"id": d.id, "name": d.display_name}
            for d in context["devices"]  # type: ignore[attr-defined]
            if d.id not in hidden
        ],
        "kinds": [_named(kind) for kind in kinds.values()],  # type: ignore[attr-defined]
        "commands": [_named(c) for c in context["commands"]],  # type: ignore[attr-defined]
    }


def _num(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _last_value(prop: DeviceProperty) -> str | None:
    """Der zuletzt gemeldete Wert als Text: die Zahl ohne überzählige Nullen, sonst der Text."""
    if prop.last_value_number is not None:
        return format(prop.last_value_number.normalize(), "f")
    return prop.last_value_text


def _age_seconds(moment: datetime | None, now: datetime) -> int | None:
    """Alter in ganzen Sekunden (nie negativ, auch bei leicht vorgehender Gerätezeit)."""
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds()))


@router.get("/controllers")
def show_controllers(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Bediengeräte mit Merkmalen, Kanälen und gesehenen Tasten (`device.read`, Zonenfilter)."""
    _require_admin_ui(principal)
    _need_zones(session, principal, "device.read")
    return _controllers_json(session, principal)


class ChannelIn(BaseModel):
    device_id: int
    property_name: str
    direction: str
    kind: str
    zone_id: int | None = None
    source_device_id: int | None = None
    fixed_text: str | None = None
    fixed_number: str | None = None


def _controller_device(session: Session, principal: Principal, device_id: int) -> Device:
    try:
        return managed_device(session, principal, device_id)
    except HTTPException:
        raise _not_found("Bediengerät") from None


@router.post("/controllers/channel")
def set_channel(
    body: ChannelIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Kanal eines Bediengeräts konfigurieren; Zielzone braucht `device.manage`."""
    _require_admin_ui(principal)
    _need_zones(session, principal, "device.read")
    device = _controller_device(session, principal, body.device_id)
    try:
        if body.zone_id is not None:
            require_manageable_zone(session, principal, body.zone_id)
        if body.source_device_id is not None:
            require_readable_device(session, principal, body.source_device_id)
    except Forbidden:
        raise _forbidden() from None
    except HTTPException:
        raise _not_found("Quellgerät") from None
    number = _decimal(body.fixed_number, "fixed_number")
    try:
        configure_channel(
            session,
            device,
            body.property_name,
            body.direction,
            body.kind,
            zone_id=body.zone_id,
            source_device_id=body.source_device_id,
            fixed_text=(body.fixed_text or "").strip() or None,
            fixed_number=number,
        )
    except ControllerChannelError as exc:
        raise RoomApiError(422, str(exc), "channel_invalid") from None
    return _controllers_json(session, principal)


@router.post("/controllers/button")
def set_button(
    body: ButtonIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Json:
    """Taste belegen: Aktion, Befehl (Raum, Richtung, Wert), Schrittweite."""
    _require_admin_ui(principal)
    _need_zones(session, principal, "device.read")
    device = _controller_device(session, principal, body.device_id)
    _bind(session, principal, device, body)
    return _controllers_json(session, principal)


# -- Schaltprotokoll (R072) --------------------------------------------------------------


def _command(entry: CommandLogEntry) -> Json:
    return {
        "id": entry.id,
        "sent_at": _iso(entry.sent_at),
        "kind": entry.entry_kind,
        "source": entry.source,
        "zone_name": entry.zone_name,
        "device_name": entry.device_name,
        "command": entry.command,
        "payload": entry.payload,
        "outcome": entry.outcome,
        "error": entry.error,
        "reason": entry.reason,
        "simulated": entry.simulated,
    }


@router.get("/device-commands")
def show_device_commands(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    zone: str = "",
    outcome: str = "",
    page: str = "1",
) -> Json:
    """Jeder gesendete oder zurückgehaltene Befehl an einen Aktor (`audit.read`)."""
    _require_admin_ui(principal)
    _need(principal, "audit.read")
    from_day, to_day = _day(from_date, "from_date"), _day(to_date, "to_date")
    if from_day is not None and to_day is not None and to_day < from_day:
        raise _invalid("Das Bis-Datum darf nicht vor dem Von-Datum liegen.", "to_date")
    number = _page(page)
    timezone_name, _settings = _timezone(session)
    # Lokale Kalendertage der Anlage, nicht UTC-Mitternacht (Wanduhr des Betreibers).
    from_at = local_day_start_utc(from_day, timezone_name) if from_day is not None else None
    to_at = (
        local_day_start_utc(to_day + timedelta(days=1), timezone_name) - timedelta(microseconds=1)
        if to_day is not None
        else None
    )
    fetched = list_commands(
        session,
        zone_name=zone or None,
        from_at=from_at,
        to_at=to_at,
        outcome=outcome or None,
        limit=min(ENTRIES_PER_PAGE + 1, MAX_LIMIT),
        offset=(number - 1) * ENTRIES_PER_PAGE,
    )
    outcomes = session.execute(
        select(CommandOutcome.code, CommandOutcome.label).order_by(CommandOutcome.label)
    ).all()
    outcome_labels = {code: label for code, label in outcomes}
    outcome_labels.update(SETBACK_OUTCOME_LABELS)
    source_labels = {
        code: label for code, label in session.execute(select(ActorSource.code, ActorSource.label))
    }
    source_labels.setdefault("regelung", "Regelung (Notbetrieb)")
    source_labels.setdefault(SETBACK_SOURCE, "Regelung (Sonnenabsenkung)")
    zone_names = sorted(
        {
            *session.scalars(select(DeviceCommand.zone_name).distinct()),
            *setback_zone_names(session),
        },
        key=lambda name: (name.casefold(), name),
    )
    return {
        "timezone": timezone_name,
        "page": number,
        "page_size": ENTRIES_PER_PAGE,
        "has_more": len(fetched) > ENTRIES_PER_PAGE,
        "entries": [_command(entry) for entry in fetched[:ENTRIES_PER_PAGE]],
        "zone_names": zone_names,
        "outcomes": [{"code": code, "label": label} for code, label in outcomes],
        "outcome_labels": outcome_labels,
        "source_labels": source_labels,
    }
