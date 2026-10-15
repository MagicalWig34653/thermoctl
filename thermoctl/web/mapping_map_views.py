"""Zuordnungskarte (Entwurf): Knoten-Fläche und zwei dünne Schreibendpunkte.

Entscheidungen liegen in `domain/mapping_map.py` (Passt das? Was steht schon?) und in
`domain/device_assignment.py` (Speichern). Die Seite zeigt nur an; die beiden POST-Endpunkte
antworten mit JSON, weil die Karte die Seite nicht neu laden soll, solange der Nutzer noch
zieht. Die bestehende Zuordnungsseite (`/zones/{id}/devices`) bleibt unverändert.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import Device, ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.device_assignment import (
    TEMPERATURE_SOURCE,
    ActuatorInAnotherZone,
    AssignmentAlreadyExists,
    CapabilityMissing,
    actuator_elsewhere_notice,
    assign_device,
    detach_device,
    set_temperature_source,
)
from thermoctl.domain.mapping_map import (
    SAME_SIDE,
    TECHNICAL_REASON,
    Connection,
    MappingMap,
    build_mapping_map,
    can_connect,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import templates
from thermoctl.web.guards import admin_ui_only

router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


def _rejected(reason: str, status_code: int = 422) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


@router.get("/devices/map")
async def mapping_map(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Gleiches Recht und gleicher Zonenfilter wie die Zuordnungsseite (`device.read`)."""
    zones = visible_zones(session, principal, "device.read")
    editable = {zone.id for zone in zones if has_permission(principal, "device.manage", zone.id)}
    graph = build_mapping_map(session, zones, editable)
    return templates.TemplateResponse(
        request,
        "mapping_map.html",
        {"graph": graph, "payload": _payload(graph), "may_edit": bool(editable)},
    )


def _payload(graph: MappingMap) -> dict[str, object]:
    """Was das Skript braucht: nur Kennungen und fertige Begründungen, keine Regeln."""
    return {
        "reasons": list(graph.reasons),
        "rejections": graph.rejections,
        "same_side": SAME_SIDE,
        "technical_reason": TECHNICAL_REASON,
        "single": {
            p.id: "Dieser Eingang nimmt nur eine Verbindung an."
            for p in graph.ports()
            if p.single and p.node_kind == "device"
        },
        "connections": [
            {
                "id": c.id,
                "out": c.out_port_id,
                "in": c.in_port_id,
                "editable": c.editable,
                "title": _title(c),
            }
            for c in graph.connections
        ],
    }


def _title(c: Connection) -> str:
    """Lesbarer Name einer Verbindung in Richtung des Datenflusses (Ausgang → Eingang)."""
    return f"{c.text} → {c.device_name}" if c.slot == "actuator" else f"{c.device_name} → {c.text}"


# Nach dem Lösen eines Aktors: thermoctl schickt dem Gerät keinen Ausschaltbefehl (`detach_device`
# löscht nur die Zuordnung, die Regelkette kennt das Gerät danach nicht mehr).
ACTUATOR_DETACHED_NOTE = (
    " Das Gerät wird nicht mehr von thermoctl geschaltet und behält seinen letzten Zustand."
)


def _editable_graph(
    session: Session, principal: Principal
) -> tuple[MappingMap, dict[int, Zone]]:
    zones = visible_zones(session, principal, "device.manage")
    graph = build_mapping_map(session, zones, {zone.id for zone in zones})
    return graph, {zone.id: zone for zone in zones}


@router.post("/devices/map/connect")
async def connect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Verbindet zwei Anschlüsse, sofern eine davon ein Zonen-Anschluss ist.

    `from_id` und `to_id` dürfen in beliebiger Reihenfolge stehen (Ziehen geht von jedem Ende
    aus); `can_connect` legt die Richtung fest. Zwei Prüfungen hintereinander, beide aus der
    Domäne: erst `can_connect`, dann die Speicherfunktion mit ihrer eigenen Fähigkeitsprüfung.
    Verbindungen zwischen zwei Geräten gibt es im Datenmodell nicht; solche lehnt der Endpunkt ab,
    statt still etwas anderes zu speichern.
    """
    form = await request.form()
    graph, zones = _editable_graph(session, principal)
    ports = {p.id: p for p in graph.ports()}
    first = ports.get(str(form.get("from_id", "")))
    second = ports.get(str(form.get("to_id", "")))
    if first is None or second is None:
        return _rejected("Anschluss nicht gefunden.", 404)
    zone_port = next((p for p in (first, second) if p.node_kind == "zone"), None)
    if zone_port is None:
        # Erst die Regeln: Unsinn wird als Unsinn begründet, nicht als „nicht gespeichert".
        verdict = can_connect(first, second, graph.connections)
        return _rejected(
            verdict.reason or "Verbindungen zwischen Geräten werden nicht gespeichert."
        )
    if zone_port.zone_id not in zones or zone_port.slot is None:
        return _rejected("Anschluss nicht gefunden.", 404)
    verdict = can_connect(first, second, graph.connections)
    if not verdict.ok or verdict.output is None or verdict.input is None:
        return _rejected(verdict.reason or "Diese Verbindung ist nicht möglich.")
    device_port = verdict.input if verdict.output is zone_port else verdict.output
    zone = zones[zone_port.zone_id]
    device = session.get(Device, device_port.device_id)
    if device is None:  # pragma: no cover - der Anschluss stammt aus derselben Sitzung
        return _rejected("Gerät nicht gefunden.", 404)
    try:
        if zone_port.slot == TEMPERATURE_SOURCE:
            set_temperature_source(session, zone, device, actor_id=principal.user_id)
        else:
            role = session.scalar(select(DeviceRole).where(DeviceRole.code == zone_port.slot))
            if role is None:
                return _rejected("Diese Rolle gibt es in der Anlage nicht.")
            assign_device(session, zone, device, role, actor_id=principal.user_id)
    except AssignmentAlreadyExists:
        # `can_connect` kennt jede Doppelzuordnung, die beim Aufbau der Karte schon stand; hier
        # landet, wer gleichzeitig mit einer anderen Anfrage dieselbe Zeile anlegt (-> 409).
        return _rejected("Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet.", 409)
    except ActuatorInAnotherZone as exc:
        # Maßgeblich ist die Domäne; `can_connect` zeigt dieselbe Regel nur vorab. Hier landet, wer
        # gleichzeitig mit einer anderen Zuordnung dasselbe Gerät belegt. Wie in der Karte: Namen
        # von Zonen, die der Betrachter nicht sehen darf, bleiben verborgen.
        visible = set(zones)
        where = [
            f"von Zone '{name}'" if zone_id in visible else "einer anderen Zone"
            for zone_id, name in exc.zones
        ]
        return _rejected(actuator_elsewhere_notice(device.display_name, where))
    except CapabilityMissing as exc:  # pragma: no cover - `can_connect` prüft dieselbe Fähigkeit
        return _rejected(exc.notice)
    return JSONResponse({"ok": True})


@router.post("/devices/map/disconnect")
async def disconnect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Löst eine gespeicherte Verbindung (Kennung aus `MappingMap.connections`)."""
    form = await request.form()
    graph, zones = _editable_graph(session, principal)
    connection = next(
        (c for c in graph.connections if c.id == str(form.get("connection_id", ""))), None
    )
    if connection is None or connection.zone_id not in zones:
        return _rejected("Verbindung nicht gefunden.", 404)
    zone = zones[connection.zone_id]
    if connection.slot == TEMPERATURE_SOURCE:
        set_temperature_source(session, zone, None, actor_id=principal.user_id)
    else:
        assignment = session.scalar(
            select(ZoneDevice)
            .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
            .where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_id == connection.device_id,
                DeviceRole.code == connection.slot,
            )
        )
        if assignment is None:  # pragma: no cover - die Verbindung stammt aus derselben Sitzung
            return _rejected("Verbindung nicht gefunden.", 404)
        detach_device(session, zone, assignment, actor_id=principal.user_id)
    note = ACTUATOR_DETACHED_NOTE if connection.slot == "actuator" else ""
    return JSONResponse({"ok": True, "message": f"Verbindung gelöst: {_title(connection)}.{note}"})
