# ruff: noqa: E501
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.domain.authz import require
from thermoctl.domain.controller import (
    ControllerError,
    controller_zones,
    set_binding,
)
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.domain.principal import Principal
from thermoctl.web import templates
from thermoctl.web.guards import admin_ui_only
from thermoctl.web.shared.controllers import (
    controllers_context,
    managed_device,
    require_manageable_zone,
    require_readable_device,
)
from thermoctl.web.shared.controllers import zones_with_permission as _zones
from thermoctl.web.urls import prefixed

# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


@router.get("/controllers")
async def controllers(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    # The navigation entry for this page has always claimed `device.read`; the page
    # itself never checked it. A merely logged-in user could open it and read the
    # device list -- the one thing on it that was not zone-filtered.
    if not _zones(session, principal, "device.read"):
        require(principal, "device.read")
    return templates.TemplateResponse(request, "controllers.html", controllers_context(session, principal))


@router.post("/controllers/channel")
async def channel_set(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    form = await request.form()
    try:
        device = managed_device(session, principal, int(str(form.get("device_id", ""))))
        if form.get("zone_id"):
            require_manageable_zone(session, principal, int(str(form["zone_id"])))
        if form.get("source_device_id"):
            require_readable_device(session, principal, int(str(form["source_device_id"])))
        raw_number = str(form.get("fixed_number", "")).replace(",", ".").strip()
        configure_channel(
            session, device, str(form.get("property_name", "")), str(form.get("direction", "")), str(form.get("kind", "")),
            zone_id=int(str(form["zone_id"])) if form.get("zone_id") else None,
            source_device_id=int(str(form["source_device_id"])) if form.get("source_device_id") else None,
            fixed_text=str(form.get("fixed_text", "")).strip() or None,
            fixed_number=Decimal(raw_number) if raw_number else None,
        )
    except (ValueError, InvalidOperation, ControllerChannelError) as exc:
        return templates.TemplateResponse(request, "controllers.html", controllers_context(session, principal, errors={"channel": str(exc)}), status_code=400)
    return RedirectResponse(prefixed(request, "/controllers"), status.HTTP_303_SEE_OTHER)


@router.post("/controllers/button")
async def button_set(request: Request, principal: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]) -> Response:
    form = await request.form()
    device = managed_device(session, principal, int(str(form.get("device_id", ""))))
    # A button binding fires in *every* zone the controller hangs in (see
    # `domain/controller.py::execute_action`). Managing one of those zones is
    # therefore not enough to change it -- otherwise `device.manage` for the
    # shared hallway dial would reach every room it also controls.
    for zone in controller_zones(session, device):
        require(principal, "device.manage", zone.id)
    action = str(form.get("action_code", "")).strip()
    if not action:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Aktion angegeben")
    raw_step = str(form.get("step_k", "")).replace(",", ".").strip()
    try:
        set_binding(session, device, action, str(form.get("command", "")).strip() or None, Decimal(raw_step) if raw_step else None)
    except (InvalidOperation, ControllerError) as exc:
        return templates.TemplateResponse(request, "controllers.html", controllers_context(session, principal, errors={"button": str(exc)}), status_code=400)
    return RedirectResponse(prefixed(request, "/controllers"), status.HTTP_303_SEE_OTHER)
