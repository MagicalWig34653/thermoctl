"""Zuordnungskarte (Entwurf): Knoten-Fläche und zwei dünne Schreibendpunkte.

Entscheidungen liegen in `domain/mapping_map.py` (Passt das? Was steht schon?) und in
`domain/device_assignment.py` (Speichern). Die Seite zeigt nur an; die beiden POST-Endpunkte
antworten mit JSON, weil die Karte die Seite nicht neu laden soll, solange der Nutzer noch
zieht. Die bestehende Zuordnungsseite (`/zones/{id}/devices`) bleibt unverändert.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.mapping_map import (
    SAME_SIDE,
    TECHNICAL_REASON,
    MappingMap,
    build_mapping_map,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import templates
from thermoctl.web.guards import admin_ui_only
from thermoctl.web.shared.mapping_map import (
    MapOutcome,
    connect_ports,
    connection_title,
    disconnect_connection,
)

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
                "title": connection_title(c),
            }
            for c in graph.connections
        ],
    }


def _answer(outcome: MapOutcome) -> Response:
    if not outcome.ok:
        return _rejected(outcome.reason or "", outcome.status)
    body: dict[str, object] = {"ok": True}
    if outcome.message is not None:
        body["message"] = outcome.message
    return JSONResponse(body)


@router.post("/devices/map/connect")
async def connect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    return _answer(
        connect_ports(
            session, principal, str(form.get("from_id", "")), str(form.get("to_id", ""))
        )
    )


@router.post("/devices/map/disconnect")
async def disconnect(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    return _answer(disconnect_connection(session, principal, str(form.get("connection_id", ""))))
