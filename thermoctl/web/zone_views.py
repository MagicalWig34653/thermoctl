from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, require, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.zones import (
    ZoneNameTaken,
    create_zone,
    delete_zone,
    update_zone,
    zone_dependencies,
)
from thermoctl.web import templates
from thermoctl.web.forms import FormError, form_again

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, "zone.manage") if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _choice_values(session: Session) -> dict[str, object]:
    operating_modes = session.scalars(select(OperatingMode).order_by(OperatingMode.id)).all()
    devices = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    return {
        "operating_modes": [(kind.id, kind.label) for kind in operating_modes],
        "devices": [(device.id, device.display_name) for device in devices],
    }


def _form_values(form: object) -> dict[str, str]:
    get = form.get  # type: ignore[attr-defined]
    return {
        "name": str(get("name", "")).strip(),
        "display_name": str(get("display_name", "")).strip(),
        "operating_mode": str(get("operating_mode", "")),
        "sort_order": str(get("sort_order", "0")).strip(),
        "temperature_source_device_id": str(get("temperature_source_device_id", "")),
    }


def _checked_values(
    session: Session, values: dict[str, str]
) -> tuple[str, str, int, int, int | None]:
    if not values["name"]:
        raise FormError("name", "Bitte einen technischen Namen eingeben.")
    if not values["display_name"]:
        raise FormError("display_name", "Bitte einen Anzeigenamen eingeben.")
    try:
        operating_mode_id = int(values["operating_mode"])
    except ValueError as exc:
        raise FormError("operating_mode", "Bitte eine Betriebsart auswählen.") from exc
    if session.get(OperatingMode, operating_mode_id) is None:
        raise FormError("operating_mode", "Diese Betriebsart ist nicht bekannt.")
    try:
        sort_order = int(values["sort_order"])
    except ValueError as exc:
        raise FormError("sort_order", "Bitte eine ganze Zahl eingeben.") from exc
    device_id = None
    if values["temperature_source_device_id"]:
        try:
            device_id = int(values["temperature_source_device_id"])
        except ValueError as exc:
            raise FormError(
                "temperature_source_device_id", "Bitte ein bekanntes Gerät auswählen."
            ) from exc
        if session.get(Device, device_id) is None:
            raise FormError(
                "temperature_source_device_id", "Dieses Gerät ist nicht bekannt."
            )
    return values["name"], values["display_name"], operating_mode_id, sort_order, device_id


def _form_again(
    request: Request,
    session: Session,
    values: dict[str, str],
    errors: FormError,
    *,
    zone: Zone | None,
) -> Response:
    return form_again(
        request,
        "zone_form.html",
        values,
        errors,
        zone=zone,
        **_choice_values(session),
    )


@router.get("/zones")
async def zone_list_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zones = visible_zones(session, principal, "zone.read")
    return templates.TemplateResponse(
        request,
        "zones.html",
        {
            "zones": zones,
            "may_create": has_permission(principal, "zone.manage"),
            "may_edit": {
                zone.id for zone in zones if has_permission(principal, "zone.manage", zone.id)
            },
        },
    )


@router.get("/zones/new")
async def zone_new(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    return templates.TemplateResponse(
        request,
        "zone_form.html",
        {"zone": None, "values": {"sort_order": "0"}, "errors": {}, **_choice_values(session)},
    )


@router.post("/zones")
async def create_zone_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    values = _form_values(await request.form())
    try:
        name, display_name, kind_id, order_value, device_id = _checked_values(session, values)
        create_zone(
            session,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=kind_id,
            sort_order=order_value,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken:
        return _form_again(
            request,
            session,
            values,
            FormError("name", "Dieser Name ist bereits vergeben."),
            zone=None,
        )
    except FormError as exc:
        return _form_again(request, session, values, exc, zone=None)
    return RedirectResponse("/zones", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zones/{zone_id}")
async def edit_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    values = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode": str(zone.operating_mode_id),
        "sort_order": str(zone.sort_order),
        "temperature_source_device_id": str(zone.temperature_source_device_id or ""),
    }
    return templates.TemplateResponse(
        request,
        "zone_form.html",
        {"zone": zone, "values": values, "errors": {}, **_choice_values(session)},
    )


@router.post("/zones/{zone_id}")
async def save_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    values = _form_values(await request.form())
    try:
        name, display_name, kind_id, order_value, device_id = _checked_values(session, values)
        update_zone(
            session,
            zone,
            principal,
            name=name,
            display_name=display_name,
            operating_mode_id=kind_id,
            sort_order=order_value,
            temperature_source_device_id=device_id,
        )
    except ZoneNameTaken:
        return _form_again(
            request,
            session,
            values,
            FormError("name", "Dieser Name ist bereits vergeben."),
            zone=zone,
        )
    except FormError as exc:
        return _form_again(request, session, values, exc, zone=zone)
    return RedirectResponse("/zones", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zones/{zone_id}/delete")
async def confirm_zone_delete(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    return templates.TemplateResponse(
        request,
        "zone_delete.html",
        {"zone": zone, "dependencies": zone_dependencies(session, zone.id)},
    )


@router.post("/zones/{zone_id}/delete")
async def execute_zone_delete(
    zone_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _visible_zone(session, principal, zone_id)
    delete_zone(session, zone, principal)
    return RedirectResponse("/zones", status_code=status.HTTP_303_SEE_OTHER)
