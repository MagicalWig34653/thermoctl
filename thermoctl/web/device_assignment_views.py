from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.controller import (
    ControllerError,
    seen_actions,
    set_binding,
)
from thermoctl.domain.device_assignment import (
    REQUIRED_CAPABILITY,
    AssignmentAlreadyExists,
    CapabilityMissing,
    assign_device,
    detach_device,
    set_self_regulating,
    set_temperature_source,
    swap_device,
)
from thermoctl.domain.plant_diagram import plant_diagram
from thermoctl.domain.principal import Principal
from thermoctl.web import templates
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


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


def _context(session: Session, zone: Zone, **extra: object) -> dict[str, object]:
    devices = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    roles = session.scalars(select(DeviceRole).order_by(DeviceRole.id)).all()
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
        (device, seen_actions(session, device))
        for assignment, device, role in assignments
        if role.code == "controller"
    ]

    return {
        "zone": zone,
        "capabilities": capabilities,
        # Welche Faehigkeit eine Stelle verlangt, entscheidet die Domaene -- die
        # Vorlage schreibt es nicht noch einmal hin. Die Aktor-Stelle nimmt seit den
        # Thermostatventilen `switch` **oder** `thermostat`; stand das in der Vorlage
        # weiter als einzelnes Wort, wies das Ziehen ein Ventil ab, das die Domaene
        # angenommen haette.
        "required_capabilities": {
            slot: " ".join(sorted(codes)) for slot, (codes, _) in REQUIRED_CAPABILITY.items()
        },
        "controllers": controllers,
        "commands": session.scalars(
            select(ControllerCommand).order_by(ControllerCommand.id)
        ).all(),
        # The same plant diagram as on /anlage, here for this one zone. It sits above
        # the forms because it answers the question you arrive with -- what's wired
        # up here and what's missing -- before you change anything.
        "picture": plant_diagram(session, [zone]).zones[0],
        "devices": devices,
        "roles": roles,
        "assignments": assignments,
        "temperature_source": temperature_source,
        "errors": {},
        **extra,
    }


def _response(session: Session, request: Request, zone: Zone, **extra: object) -> Response:
    return templates.TemplateResponse(
        request, "device_assignment.html", _context(session, zone, **extra)
    )


def _device(session: Session, raw_value: object, field: str) -> Device:
    try:
        device = session.get(Device, int(str(raw_value)))
    except ValueError:
        device = None
    if device is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiges Gerät im Feld {field}")
    return device


@router.get("/zones/{zone_id}/devices")
async def devices_of_the_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.read")
    return _response(
        session,
        request,
        zone,
        may_edit=has_permission(principal, "device.manage", zone.id),
    )


@router.post("/zones/{zone_id}/devices/assign")
async def assign_device_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    try:
        device = _device(session, form.get("device_id"), "device_id")
        role = session.get(DeviceRole, int(str(form.get("role_id", ""))))
    except (ValueError, HTTPException):
        return _response(
            session,
            request,
            zone,
            may_edit=True,
            errors={"assignment": "Bitte Gerät und Rolle auswählen."},
        )
    if role is None:
        return _response(
            session,
            request,
            zone,
            may_edit=True,
            errors={"assignment": "Bitte eine bekannte Rolle auswählen."},
        )
    try:
        assign_device(
            session, zone, device, role, actor_id=principal.user_id
        )
    except AssignmentAlreadyExists:
        return _response(
            session,
            request,
            zone,
            may_edit=True,
            errors={
                "assignment": "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet."
            },
        )
    except CapabilityMissing as exc:
        return _response(
            session, request, zone, may_edit=True, errors={"assignment": exc.notice}
        )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/devices/detach")
async def device_detach_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
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
    detach_device(session, zone, assignment, actor_id=principal.user_id)
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/devices/regulation")
async def device_regulation_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Switches a thermostatic valve between the two ways of running it.

    Physically consequential, so it is written down: in self-regulating mode thermoctl
    stops switching this valve. Hysteresis, minimum switching duration and the window
    contact then no longer act through an on/off command -- only through the setpoint
    that gets written. Whoever changes this should be able to find out later that they
    did, and when.
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

    set_self_regulating(
        session,
        zone,
        assignment,
        str(form.get("self_regulating", "")) == "yes",
        actor_id=principal.user_id,
    )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/devices/source")
async def temperature_source_set_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
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
            may_edit=True,
            errors={"temperature_source": "Bitte ein bekanntes Gerät auswählen."},
        )
    try:
        set_temperature_source(session, zone, device, actor_id=principal.user_id)
    except CapabilityMissing as exc:
        return _response(
            session, request, zone, may_edit=True, errors={"temperature_source": exc.notice}
        )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/devices/swap")
async def device_swap_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id, "device.manage")
    form = await request.form()
    try:
        old = _device(session, form.get("old_device_id"), "old_device_id")
        new_link = _device(session, form.get("new_device_id"), "new_device_id")
        swap_device(
            session, zone, old, new_link, actor_id=principal.user_id
        )
    except (HTTPException, ValueError, CapabilityMissing) as exc:
        notice = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _response(
            session,
            request,
            zone,
            may_edit=True,
            errors={"swap": notice},
        )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/devices/button")
async def bind_button(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
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
    action = str(form.get("action_code", "")).strip()
    if not action:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Aktion angegeben")
    command = str(form.get("command", "")).strip() or None

    step: Decimal | None = None
    raw_step = str(form.get("step_k", "")).strip().replace(",", ".")
    if raw_step:
        try:
            step = Decimal(raw_step)
        except InvalidOperation:
            return _response(
                session, request, zone, may_edit=True,
                errors={"button": "Die Schrittweite muss eine Zahl sein."},
            )

    try:
        set_binding(session, device, action, command, step)
    except ControllerError as exc:
        return _response(
            session, request, zone, may_edit=True, errors={"button": str(exc)}
        )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/devices"), status.HTTP_303_SEE_OTHER
    )
