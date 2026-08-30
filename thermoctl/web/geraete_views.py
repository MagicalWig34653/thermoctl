from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, Integration
from thermoctl.db.models.messwert import DeviceHealth
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.anlagenbild import anlagenbild
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.geraeteschau import OHNE_KAERTCHEN, Geraeteschau, befunde
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
STUMM_OHNE_VORGABEN_SEKUNDEN = 900


@router.get("/geraete")
async def geraeteuebersicht(
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
    faehigkeiten: defaultdict[int, list[str]] = defaultdict(list)
    still: defaultdict[int, int] = defaultdict(int)
    for geraet_id, code, bezeichnung in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code, DeviceCapability.label)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.label)
    ):
        if code in OHNE_KAERTCHEN:
            still[geraet_id] += 1
        else:
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

    vorgaben = session.get(Setting, 1)
    stumm_nach = (
        vorgaben.default_sensor_timeout_seconds
        if vorgaben is not None
        else STUMM_OHNE_VORGABEN_SEKUNDEN
    )
    jetzt = utcnow()
    schau = [
        Geraeteschau(
            geraet_id=geraet.id,
            name=geraet.display_name,
            modell=geraet.model,
            anbindung=anbindung.label,
            ist_gruppe=geraet.is_group,
            faehigkeiten=faehigkeiten[geraet.id],
            stille_faehigkeiten=still[geraet.id],
            zonen=sorted(zonen[geraet.id]),
            zuletzt_gehoert=zustand.last_payload_at if zustand else None,
            batterie=zustand.battery_percent if zustand else None,
            funkguete=zustand.link_quality if zustand else None,
            befunde=befunde(
                aktiv=geraet.is_enabled,
                zuletzt_gehoert=zustand.last_payload_at if zustand else None,
                erreichbarkeit=zustand.availability if zustand else None,
                batterie=zustand.battery_percent if zustand else None,
                funkguete=zustand.link_quality if zustand else None,
                stumm_nach_sekunden=stumm_nach,
                jetzt=jetzt,
            ),
        )
        for geraet, anbindung, zustand in zeilen
    ]
    # Auffaelliges nach oben: Die Frage, mit der jemand herkommt, ist fast immer
    # "stimmt etwas nicht?" -- und die Antwort soll nicht unter zwanzig gesunden
    # Geraeten stehen.
    schau.sort(key=lambda g: (g.schwere, g.name))

    return templates.TemplateResponse(
        request,
        "geraete.html",
        {
            "geraete": schau,
            "auffaellig": [g for g in schau if not g.in_ordnung],
            "unauffaellig": [g for g in schau if g.in_ordnung],
            "ohne_zone": sum(1 for g in schau if not g.zonen),
            "ist_htmx": ist_teilaustausch(request),
        },
    )


@router.get("/anlage")
async def anlage_anzeigen(
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
            **anlagenbild(
                session, visible_zones(session, principal, "zone.read")
            ).__dict__,
            "bruecke": getattr(request.app.state, "bruecke_erreichbar", None),
        },
    )
