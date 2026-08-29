from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration
from thermoctl.db.models.messwert import DeviceHealth
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


@router.get("/geraete")
async def geraeteuebersicht(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "device.read")
    geraete = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.id)
    ).all()
    faehigkeiten: defaultdict[int, list[str]] = defaultdict(list)
    for geraet_id, bezeichnung in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.label)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.label)
    ):
        faehigkeiten[geraet_id].append(bezeichnung)

    zonen: defaultdict[int, set[str]] = defaultdict(set)
    for geraet_id, anzeigename in session.execute(
        select(ZoneDevice.device_id, Zone.display_name).join(Zone, Zone.id == ZoneDevice.zone_id)
    ):
        zonen[geraet_id].add(anzeigename)
    for geraet_id, anzeigename in session.execute(
        select(Zone.temperature_source_device_id, Zone.display_name).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        if geraet_id is not None:
            zonen[geraet_id].add(anzeigename)

    return templates.TemplateResponse(
        request,
        "geraete.html",
        {
            "geraete": geraete,
            "faehigkeiten": faehigkeiten,
            "zonen": zonen,
            "ist_htmx": "HX-Request" in request.headers,
        },
    )
