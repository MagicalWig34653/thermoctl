from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.controller import (
    ControllerError,
    gesehene_aktionen,
    set_binding,
)
from thermoctl.domain.device_assignment import (
    AssignmentAlreadyExists,
    CapabilityMissing,
    assign_device,
    detach_device,
    set_temperature_source,
    swap_device,
)
from thermoctl.domain.plant_diagram import plant_diagram
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _visible_zone(
    session: Session, principal: Principal, zone_id: int, permission: str
) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, permission) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _kontext(session: Session, zone: Zone, **zusatz: object) -> dict[str, object]:
    devices = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    rollen = session.scalars(select(DeviceRole).order_by(DeviceRole.id)).all()
    assignments = session.execute(
        select(ZoneDevice, Device, DeviceRole)
        .join(Device, Device.id == ZoneDevice.device_id)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id == zone.id)
        .order_by(DeviceRole.id, ZoneDevice.sort_order, Device.display_name)
    ).all()
    temperature_source = (
        session.get(Device, zone.temperature_source_device_id)
        if zone.temperature_source_device_id is not None
        else None
    )
    capabilities: dict[int, list[str]] = {}
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        capabilities.setdefault(device_id, []).append(code)

    # Per controller in this zone: which buttons it has sent and what they do.
    # Without this list, someone would have to know what their model calls its
    # buttons -- `single_plus`, `button_1_single`, `up_open`, depending on manufacturer.
    controllers = [
        (device, gesehene_aktionen(session, device))
        for assignment, device, rolle in assignments
        if rolle.code == "controller"
    ]

    return {
        "zone": zone,
        "capabilities": capabilities,
        "controllers": controllers,
        "commands": session.scalars(
            select(ControllerCommand).order_by(ControllerCommand.id)
        ).all(),
        # The same plant diagram as on /anlage, here for this one zone. It sits above
        # the forms because it answers the question you arrive with -- what's wired
        # up here and what's missing -- before you change anything.
        "picture": plant_diagram(session, [zone]).zones[0],
        "devices": devices,
        "rollen": rollen,
        "assignments": assignments,
        "temperature_source": temperature_source,
        "errors": {},
        **zusatz,
    }


def _response(session: Session, request: Request, zone: Zone, **zusatz: object) -> Response:
    return templates.TemplateResponse(
        request, "device_assignment.html", _kontext(session, zone, **zusatz)
    )


def _device(session: Session, raw_value: object, feld: str) -> Device:
    try:
        device = session.get(Device, int(str(raw_value)))
    except ValueError:
        device = None
    if device is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiges Gerät im Feld {feld}")
    return device


@router.get("/zones/{zone_id}/devices")
async def devices_of_the_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.read")
    return _response(
        session,
        request,
        zone,
        darf_aendern=has_permission(principal, "device.manage", zone.id),
    )


@router.post("/zones/{zone_id}/devices/assign")
async def assign_device_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    try:
        device = _device(session, form.get("device_id"), "device_id")
        rolle = session.get(DeviceRole, int(str(form.get("role_id", ""))))
    except (ValueError, HTTPException):
        return _response(
            session,
            request,
            zone,
            darf_aendern=True,
            errors={"assignment": "Bitte Gerät und Rolle auswählen."},
        )
    if rolle is None:
        return _response(
            session,
            request,
            zone,
            darf_aendern=True,
            errors={"assignment": "Bitte eine bekannte Rolle auswählen."},
        )
    try:
        assign_device(
            session, zone, device, rolle, akteur_id=principal.user_id
        )
    except AssignmentAlreadyExists:
        return _response(
            session,
            request,
            zone,
            darf_aendern=True,
            errors={
                "assignment": "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet."
            },
        )
    except CapabilityMissing as exc:
        return _response(
            session, request, zone, darf_aendern=True, errors={"assignment": exc.notice}
        )
    return RedirectResponse(f"/zones/{zone.id}/devices", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/devices/detach")
async def device_detach_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Removes a binding. The id lives in the body, not in the path.

    Just like moving in the schedule: `hx-boost` reads a form's `action` once when
    processing the page, so a path rewritten later would have no effect. This lets
    the buttons in the table and dragging something out of the plant diagram use the
    same endpoint.
    """
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    try:
        assignment_id = int(str(form.get("assignment_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zuordnung nicht gefunden") from exc
    assignment = session.get(ZoneDevice, assignment_id)
    if assignment is None or assignment.zone_id != zone.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zuordnung nicht gefunden")
    detach_device(session, zone, assignment, akteur_id=principal.user_id)
    return RedirectResponse(f"/zones/{zone.id}/devices", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/devices/source")
async def temperature_source_set_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    raw_value = (await request.form()).get("device_id")
    try:
        device = None if not raw_value else _device(session, raw_value, "device_id")
    except HTTPException:
        return _response(
            session,
            request,
            zone,
            darf_aendern=True,
            errors={"temperature_source": "Bitte ein bekanntes Gerät auswählen."},
        )
    try:
        set_temperature_source(session, zone, device, akteur_id=principal.user_id)
    except CapabilityMissing as exc:
        return _response(
            session, request, zone, darf_aendern=True, errors={"temperature_source": exc.notice}
        )
    return RedirectResponse(f"/zones/{zone.id}/devices", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/devices/swap")
async def device_swap_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    try:
        altes = _device(session, form.get("old_device_id"), "old_device_id")
        neues = _device(session, form.get("new_device_id"), "new_device_id")
        swap_device(
            session, zone, altes, neues, akteur_id=principal.user_id
        )
    except (HTTPException, ValueError, CapabilityMissing) as exc:
        notice = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _response(
            session,
            request,
            zone,
            darf_aendern=True,
            errors={"swap": notice},
        )
    return RedirectResponse(f"/zones/{zone.id}/devices", status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/devices/button")
async def bind_button(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Binds a controller's button -- or deletes the binding.

    The action lives in the body and not in the path: it's a value the device has
    sent (`single_plus`, `button_1_single`, …), and Zigbee2MQTT decides what's
    allowed to appear in it. In the path it would first have to be encoded, and a
    slash in it would open a level nobody intended.
    """
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    device = _device(session, form.get("device_id"), "device_id")
    aktion = str(form.get("action_code", "")).strip()
    if not aktion:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Aktion angegeben")
    command = str(form.get("command", "")).strip() or None

    step: Decimal | None = None
    raw_step = str(form.get("step_k", "")).strip().replace(",", ".")
    if raw_step:
        try:
            step = Decimal(raw_step)
        except InvalidOperation:
            return _response(
                session, request, zone, darf_aendern=True,
                errors={"button": "Die Schrittweite muss eine Zahl sein."},
            )

    try:
        set_binding(session, device, aktion, command, step)
    except ControllerError as exc:
        return _response(
            session, request, zone, darf_aendern=True, errors={"button": str(exc)}
        )
    return RedirectResponse(f"/zones/{zone.id}/devices", status.HTTP_303_SEE_OTHER)
