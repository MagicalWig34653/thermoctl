# ruff: noqa: E501
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import (
    ControllerChannel,
    Device,
    DeviceProperty,
    DevicePropertyValue,
    ZoneDevice,
)
from thermoctl.db.models.lookup import ChannelKind, ControllerCommand, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.controller import ControllerError, seen_actions, set_binding
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _zones(session: Session, principal: Principal, permission: str) -> list[Zone]:
    return list(visible_zones(session, principal, permission))


def _controllers(session: Session, zone_ids: list[int]) -> list[Device]:
    if not zone_ids:
        return []
    return list(session.scalars(select(Device).join(ZoneDevice).join(DeviceRole).where(
        ZoneDevice.zone_id.in_(zone_ids), DeviceRole.code == "controller"
    ).order_by(Device.display_name).distinct()))


def _context(session: Session, principal: Principal, **extra: object) -> dict[str, object]:
    readable_zones = _zones(session, principal, "device.read")
    manageable_ids = {zone.id for zone in _zones(session, principal, "device.manage")}
    controllers = _controllers(session, [zone.id for zone in readable_zones])
    properties: dict[int, list[DeviceProperty]] = {}
    values: dict[int, list[str]] = {}
    channels: dict[tuple[int, str], ControllerChannel] = {}
    for property_model in session.scalars(select(DeviceProperty).where(DeviceProperty.device_id.in_([d.id for d in controllers])).order_by(DeviceProperty.name)):
        properties.setdefault(property_model.device_id, []).append(property_model)
        values[property_model.id] = list(session.scalars(select(DevicePropertyValue.value).where(DevicePropertyValue.property_id == property_model.id).order_by(DevicePropertyValue.sort_order)))
    for channel in session.scalars(select(ControllerChannel).where(ControllerChannel.device_id.in_([d.id for d in controllers]))):
        channels[(channel.device_id, channel.property_name)] = channel
    return {
        "controllers": controllers, "properties": properties, "property_values": values,
        "channels": channels, "zones": readable_zones, "manageable_ids": manageable_ids,
        "devices": session.scalars(select(Device).order_by(Device.display_name)).all(),
        "kinds": {kind.id: kind for kind in session.scalars(select(ChannelKind))},
        "commands": session.scalars(select(ControllerCommand).order_by(ControllerCommand.id)).all(),
        "bindings": {device.id: seen_actions(session, device) for device in controllers},
        "errors": {}, **extra,
    }


@router.get("/controllers")
async def controllers(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    return templates.TemplateResponse(request, "controllers.html", _context(session, principal))


def _managed_device(session: Session, principal: Principal, device_id: int) -> Device:
    zones = _zones(session, principal, "device.manage")
    device = session.scalar(select(Device).join(ZoneDevice).join(DeviceRole).where(
        Device.id == device_id, ZoneDevice.zone_id.in_([z.id for z in zones]), DeviceRole.code == "controller"))
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bediengerät nicht gefunden")
    return device


@router.post("/controllers/channel")
async def channel_set(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    form = await request.form()
    try:
        device = _managed_device(session, principal, int(str(form.get("device_id", ""))))
        raw_number = str(form.get("fixed_number", "")).replace(",", ".").strip()
        configure_channel(
            session, device, str(form.get("property_name", "")), str(form.get("direction", "")), str(form.get("kind", "")),
            zone_id=int(str(form["zone_id"])) if form.get("zone_id") else None,
            source_device_id=int(str(form["source_device_id"])) if form.get("source_device_id") else None,
            fixed_text=str(form.get("fixed_text", "")).strip() or None,
            fixed_number=Decimal(raw_number) if raw_number else None,
        )
    except (ValueError, InvalidOperation, ControllerChannelError) as exc:
        return templates.TemplateResponse(request, "controllers.html", _context(session, principal, errors={"channel": str(exc)}), status_code=400)
    return RedirectResponse("/controllers", status.HTTP_303_SEE_OTHER)


@router.post("/controllers/button")
async def button_set(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    form = await request.form()
    device = _managed_device(session, principal, int(str(form.get("device_id", ""))))
    action = str(form.get("action_code", "")).strip()
    if not action:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Aktion angegeben")
    raw_step = str(form.get("step_k", "")).replace(",", ".").strip()
    try:
        set_binding(session, device, action, str(form.get("command", "")).strip() or None, Decimal(raw_step) if raw_step else None)
    except (InvalidOperation, ControllerError) as exc:
        return templates.TemplateResponse(request, "controllers.html", _context(session, principal, errors={"button": str(exc)}), status_code=400)
    return RedirectResponse("/controllers", status.HTTP_303_SEE_OTHER)
