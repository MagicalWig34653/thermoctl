from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.device_survey import WITHOUT_CHIP, DeviceSurvey, befunde
from thermoctl.domain.plant_diagram import plant_diagram
from thermoctl.domain.principal import Principal
from thermoctl.web import ist_teilaustausch, templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

# Wonach die Geraeteseite still ist, wenn die Einrichtung noch keine Vorgaben angelegt
# hat. Die Seite ist genau dann erreichbar, und ohne diesen Rueckfall haette sie gar
# keine Schwelle -- ein Geraet waere nie stumm, egal wie lange es schweigt.
SILENT_WITHOUT_DEFAULTS_SECONDS = 900


@router.get("/devices")
async def device_overview(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "device.read")
    zeilen = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.id)
    ).all()
    capabilities: defaultdict[int, list[str]] = defaultdict(list)
    still: defaultdict[int, int] = defaultdict(int)
    for device_id, code, bezeichnung in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code, DeviceCapability.label)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.label)
    ):
        if code in WITHOUT_CHIP:
            still[device_id] += 1
        else:
            capabilities[device_id].append(bezeichnung)

    zones: defaultdict[int, set[str]] = defaultdict(set)
    for device_id, anzeigename in session.execute(
        select(ZoneDevice.device_id, Zone.display_name).join(Zone, Zone.id == ZoneDevice.zone_id)
    ):
        zones[device_id].add(anzeigename)
    for device_id, anzeigename in session.execute(
        select(Zone.temperature_source_device_id, Zone.display_name).where(
            Zone.temperature_source_device_id.is_not(None)
        )
    ):
        if device_id is not None:
            zones[device_id].add(anzeigename)

    defaults = session.get(Setting, 1)
    silent_after = (
        defaults.default_sensor_timeout_seconds
        if defaults is not None
        else SILENT_WITHOUT_DEFAULTS_SECONDS
    )
    now = utcnow()
    schau = [
        DeviceSurvey(
            device_id=device.id,
            name=device.display_name,
            modell=device.model,
            integration=integration.label,
            ist_group=device.is_group,
            capabilities=capabilities[device.id],
            quiet_capabilities=still[device.id],
            zones=sorted(zones[device.id]),
            last_heard=state.last_payload_at if state else None,
            battery=state.battery_percent if state else None,
            radio_quality=state.link_quality if state else None,
            befunde=befunde(
                active=device.is_enabled,
                last_heard=state.last_payload_at if state else None,
                availability=state.availability if state else None,
                battery=state.battery_percent if state else None,
                radio_quality=state.link_quality if state else None,
                silent_after_seconds=silent_after,
                now=now,
            ),
        )
        for device, integration, state in zeilen
    ]
    # Auffaelliges nach oben: Die Frage, mit der jemand herkommt, ist fast immer
    # "stimmt etwas nicht?" -- und die Antwort soll nicht unter zwanzig gesunden
    # Geraeten stehen.
    schau.sort(key=lambda g: (g.schwere, g.name))

    return templates.TemplateResponse(
        request,
        "geraete.html",
        {
            "devices": schau,
            "auffaellig": [g for g in schau if not g.in_ordnung],
            "unauffaellig": [g for g in schau if g.in_ordnung],
            "without_zone": sum(1 for g in schau if not g.zones),
            "ist_htmx": ist_teilaustausch(request),
        },
    )


@router.get("/plant")
async def show_anlage(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Das Anlagenbild: welches Geraet wo etwas tut.

    `device.read`, wie die Geraeteliste: Es ist dieselbe Auskunft, nur als Weg statt als
    Tabelle.
    """
    require(principal, "device.read")
    return templates.TemplateResponse(
        request,
        "anlage.html",
        {
            **plant_diagram(
                session, visible_zones(session, principal, "zone.read")
            ).__dict__,
            "bridge": getattr(request.app.state, "bridge_reachable", None),
        },
    )
