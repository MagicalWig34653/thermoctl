from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.anlagenbild import anlagenbild
from thermoctl.domain.authz import hat_recht, visible_zones
from thermoctl.domain.geraetezuordnung import (
    FaehigkeitFehlt,
    ZuordnungBereitsVorhanden,
    geraet_loesen,
    geraet_tauschen,
    geraet_zuordnen,
    messquelle_setzen,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _sichtbare_zone(
    session: Session, principal: Principal, zone_id: int, recht: str
) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, recht) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _kontext(session: Session, zone: Zone, **zusatz: object) -> dict[str, object]:
    geraete = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    rollen = session.scalars(select(DeviceRole).order_by(DeviceRole.id)).all()
    zuordnungen = session.execute(
        select(ZoneDevice, Device, DeviceRole)
        .join(Device, Device.id == ZoneDevice.device_id)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id == zone.id)
        .order_by(DeviceRole.id, ZoneDevice.sort_order, Device.display_name)
    ).all()
    messquelle = (
        session.get(Device, zone.temperature_source_device_id)
        if zone.temperature_source_device_id is not None
        else None
    )
    faehigkeiten: dict[int, list[str]] = {}
    for geraet_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        faehigkeiten.setdefault(geraet_id, []).append(code)

    return {
        "zone": zone,
        "faehigkeiten": faehigkeiten,
        # Dasselbe Flussbild wie auf /anlage, hier fuer diese eine Zone. Es steht ueber
        # den Formularen, weil es die Frage beantwortet, mit der man herkommt -- was ist
        # hier verdrahtet und was fehlt -- bevor man etwas aendert.
        "bild": anlagenbild(session, [zone]).zonen[0],
        "geraete": geraete,
        "rollen": rollen,
        "zuordnungen": zuordnungen,
        "messquelle": messquelle,
        "fehler": {},
        **zusatz,
    }


def _antwort(session: Session, request: Request, zone: Zone, **zusatz: object) -> Response:
    return templates.TemplateResponse(
        request, "geraetezuordnung.html", _kontext(session, zone, **zusatz)
    )


def _geraet(session: Session, rohwert: object, feld: str) -> Device:
    try:
        geraet = session.get(Device, int(str(rohwert)))
    except ValueError:
        geraet = None
    if geraet is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiges Gerät im Feld {feld}")
    return geraet


@router.get("/zonen/{zone_id}/geraete")
async def geraete_der_zone(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id, "device.read")
    return _antwort(
        session,
        request,
        zone,
        darf_aendern=hat_recht(principal, "device.manage", zone.id),
    )


@router.post("/zonen/{zone_id}/geraete/zuordnen")
async def geraet_zuordnen_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id, "device.manage")
    formular = await request.form()
    try:
        geraet = _geraet(session, formular.get("device_id"), "device_id")
        rolle = session.get(DeviceRole, int(str(formular.get("role_id", ""))))
    except (ValueError, HTTPException):
        return _antwort(
            session,
            request,
            zone,
            darf_aendern=True,
            fehler={"zuordnung": "Bitte Gerät und Rolle auswählen."},
        )
    if rolle is None:
        return _antwort(
            session,
            request,
            zone,
            darf_aendern=True,
            fehler={"zuordnung": "Bitte eine bekannte Rolle auswählen."},
        )
    try:
        geraet_zuordnen(
            session, zone, geraet, rolle, akteur_id=principal.user_id
        )
    except ZuordnungBereitsVorhanden:
        return _antwort(
            session,
            request,
            zone,
            darf_aendern=True,
            fehler={
                "zuordnung": "Dieses Gerät ist der Zone in dieser Rolle bereits zugeordnet."
            },
        )
    except FaehigkeitFehlt as exc:
        return _antwort(
            session, request, zone, darf_aendern=True, fehler={"zuordnung": exc.meldung}
        )
    return RedirectResponse(f"/zonen/{zone.id}/geraete", status.HTTP_303_SEE_OTHER)


@router.post("/zonen/{zone_id}/geraete/loesen")
async def geraet_loesen_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Loest eine Zuordnung. Die Kennung steht im Rumpf, nicht im Pfad.

    Wie beim Verschieben im Zeitplan: `hx-boost` liest die `action` eines Formulars
    einmal beim Verarbeiten der Seite, ein spaeter umgeschriebener Pfad waere wirkungslos.
    Damit koennen die Schaltflaechen in der Tabelle und das Herausziehen aus dem
    Flussbild denselben Endpunkt benutzen.
    """
    zone = _sichtbare_zone(session, principal, zone_id, "device.manage")
    formular = await request.form()
    try:
        zuordnung_id = int(str(formular.get("zuordnung_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zuordnung nicht gefunden") from exc
    zuordnung = session.get(ZoneDevice, zuordnung_id)
    if zuordnung is None or zuordnung.zone_id != zone.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zuordnung nicht gefunden")
    geraet_loesen(session, zone, zuordnung, akteur_id=principal.user_id)
    return RedirectResponse(f"/zonen/{zone.id}/geraete", status.HTTP_303_SEE_OTHER)


@router.post("/zonen/{zone_id}/geraete/messquelle")
async def messquelle_setzen_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id, "device.manage")
    rohwert = (await request.form()).get("device_id")
    try:
        geraet = None if not rohwert else _geraet(session, rohwert, "device_id")
    except HTTPException:
        return _antwort(
            session,
            request,
            zone,
            darf_aendern=True,
            fehler={"messquelle": "Bitte ein bekanntes Gerät auswählen."},
        )
    try:
        messquelle_setzen(session, zone, geraet, akteur_id=principal.user_id)
    except FaehigkeitFehlt as exc:
        return _antwort(
            session, request, zone, darf_aendern=True, fehler={"messquelle": exc.meldung}
        )
    return RedirectResponse(f"/zonen/{zone.id}/geraete", status.HTTP_303_SEE_OTHER)


@router.post("/zonen/{zone_id}/geraete/tauschen")
async def geraet_tauschen_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id, "device.manage")
    formular = await request.form()
    try:
        altes = _geraet(session, formular.get("old_device_id"), "old_device_id")
        neues = _geraet(session, formular.get("new_device_id"), "new_device_id")
        geraet_tauschen(
            session, zone, altes, neues, akteur_id=principal.user_id
        )
    except (HTTPException, ValueError, FaehigkeitFehlt) as exc:
        meldung = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _antwort(
            session,
            request,
            zone,
            darf_aendern=True,
            fehler={"tausch": meldung},
        )
    return RedirectResponse(f"/zonen/{zone.id}/geraete", status.HTTP_303_SEE_OTHER)
