from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.plant_diagram import plant_diagram
from thermoctl.domain.principal import Principal
from thermoctl.web import is_partial_swap, templates
from thermoctl.web.guards import admin_ui_only
from thermoctl.web.shared.device_survey import build_device_survey

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


@router.get("/devices")
async def device_overview(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "device.read")
    survey, defaults = build_device_survey(session)

    return templates.TemplateResponse(
        request,
        "devices.html",
        {
            "devices": survey,
            "notable": [g for g in survey if not g.is_fine],
            "unremarkable": [g for g in survey if g.is_fine],
            "without_zone": sum(1 for g in survey if not g.zones),
            "timezone": defaults.timezone if defaults is not None else None,
            "is_htmx": is_partial_swap(request),
        },
    )


@router.get("/plant")
async def show_plant(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """The plant diagram: which device does what, where.

    `device.read`, same as the device list: it's the same information, just as a
    diagram instead of a table.
    """
    require(principal, "device.read")
    return templates.TemplateResponse(
        request,
        "plant.html",
        {
            **plant_diagram(
                session, visible_zones(session, principal, "zone.read")
            ).__dict__,
            "bridge": getattr(request.app.state, "bridge_reachable", None),
        },
    )
