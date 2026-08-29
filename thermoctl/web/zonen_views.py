from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import hat_recht, require, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.zonen import (
    ZonennameVergeben,
    zone_aendern,
    zone_anlegen,
    zone_loeschen,
    zonenabhaengigkeiten,
)
from thermoctl.web import templates
from thermoctl.web.formulare import Formularfehler, formular_erneut

router = APIRouter(dependencies=[Depends(csrf_schutz)])


def _sichtbare_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, "zone.manage") if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _auswahlwerte(session: Session) -> dict[str, object]:
    betriebsarten = session.scalars(select(OperatingMode).order_by(OperatingMode.id)).all()
    geraete = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    return {
        "betriebsarten": [(art.id, art.label) for art in betriebsarten],
        "geraete": [(geraet.id, geraet.display_name) for geraet in geraete],
    }


def _formularwerte(formular: object) -> dict[str, str]:
    get = formular.get  # type: ignore[attr-defined]
    return {
        "name": str(get("name", "")).strip(),
        "display_name": str(get("display_name", "")).strip(),
        "operating_mode": str(get("operating_mode", "")),
        "sort_order": str(get("sort_order", "0")).strip(),
        "temperature_source_device_id": str(get("temperature_source_device_id", "")),
    }


def _gepruefte_werte(
    session: Session, werte: dict[str, str]
) -> tuple[str, str, int, int, int | None]:
    if not werte["name"]:
        raise Formularfehler("name", "Bitte einen technischen Namen eingeben.")
    if not werte["display_name"]:
        raise Formularfehler("display_name", "Bitte einen Anzeigenamen eingeben.")
    try:
        operating_mode_id = int(werte["operating_mode"])
    except ValueError as exc:
        raise Formularfehler("operating_mode", "Bitte eine Betriebsart auswählen.") from exc
    if session.get(OperatingMode, operating_mode_id) is None:
        raise Formularfehler("operating_mode", "Diese Betriebsart ist nicht bekannt.")
    try:
        sort_order = int(werte["sort_order"])
    except ValueError as exc:
        raise Formularfehler("sort_order", "Bitte eine ganze Zahl eingeben.") from exc
    geraet_id = None
    if werte["temperature_source_device_id"]:
        try:
            geraet_id = int(werte["temperature_source_device_id"])
        except ValueError as exc:
            raise Formularfehler(
                "temperature_source_device_id", "Bitte ein bekanntes Gerät auswählen."
            ) from exc
        if session.get(Device, geraet_id) is None:
            raise Formularfehler(
                "temperature_source_device_id", "Dieses Gerät ist nicht bekannt."
            )
    return werte["name"], werte["display_name"], operating_mode_id, sort_order, geraet_id


def _formular_erneut(
    request: Request,
    session: Session,
    werte: dict[str, str],
    fehler: Formularfehler,
    *,
    zone: Zone | None,
) -> Response:
    return formular_erneut(
        request,
        "zone_formular.html",
        werte,
        fehler,
        zone=zone,
        **_auswahlwerte(session),
    )


@router.get("/zonen")
async def zonenliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zonen = visible_zones(session, principal, "zone.read")
    return templates.TemplateResponse(
        request,
        "zonen.html",
        {
            "zonen": zonen,
            "darf_anlegen": hat_recht(principal, "zone.manage"),
            "darf_aendern": {
                zone.id for zone in zonen if hat_recht(principal, "zone.manage", zone.id)
            },
        },
    )


@router.get("/zonen/neu")
async def zone_neu(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    return templates.TemplateResponse(
        request,
        "zone_formular.html",
        {"zone": None, "werte": {"sort_order": "0"}, "fehler": {}, **_auswahlwerte(session)},
    )


@router.post("/zonen")
async def zone_erstellen(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.manage")
    werte = _formularwerte(await request.form())
    try:
        name, anzeigename, art_id, sortierung, geraet_id = _gepruefte_werte(session, werte)
        zone_anlegen(
            session,
            principal,
            name=name,
            display_name=anzeigename,
            operating_mode_id=art_id,
            sort_order=sortierung,
            temperature_source_device_id=geraet_id,
        )
    except ZonennameVergeben:
        return _formular_erneut(
            request,
            session,
            werte,
            Formularfehler("name", "Dieser Name ist bereits vergeben."),
            zone=None,
        )
    except Formularfehler as exc:
        return _formular_erneut(request, session, werte, exc, zone=None)
    return RedirectResponse("/zonen", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zonen/{zone_id}")
async def zone_bearbeiten(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id)
    werte = {
        "name": zone.name,
        "display_name": zone.display_name,
        "operating_mode": str(zone.operating_mode_id),
        "sort_order": str(zone.sort_order),
        "temperature_source_device_id": str(zone.temperature_source_device_id or ""),
    }
    return templates.TemplateResponse(
        request,
        "zone_formular.html",
        {"zone": zone, "werte": werte, "fehler": {}, **_auswahlwerte(session)},
    )


@router.post("/zonen/{zone_id}")
async def zone_speichern(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id)
    werte = _formularwerte(await request.form())
    try:
        name, anzeigename, art_id, sortierung, geraet_id = _gepruefte_werte(session, werte)
        zone_aendern(
            session,
            zone,
            principal,
            name=name,
            display_name=anzeigename,
            operating_mode_id=art_id,
            sort_order=sortierung,
            temperature_source_device_id=geraet_id,
        )
    except ZonennameVergeben:
        return _formular_erneut(
            request,
            session,
            werte,
            Formularfehler("name", "Dieser Name ist bereits vergeben."),
            zone=zone,
        )
    except Formularfehler as exc:
        return _formular_erneut(request, session, werte, exc, zone=zone)
    return RedirectResponse("/zonen", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zonen/{zone_id}/loeschen")
async def zone_loeschen_bestaetigen(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id)
    return templates.TemplateResponse(
        request,
        "zone_loeschen.html",
        {"zone": zone, "abhaengigkeiten": zonenabhaengigkeiten(session, zone.id)},
    )


@router.post("/zonen/{zone_id}/loeschen")
async def zone_loeschen_ausfuehren(
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _sichtbare_zone(session, principal, zone_id)
    zone_loeschen(session, zone, principal)
    return RedirectResponse("/zonen", status_code=status.HTTP_303_SEE_OTHER)
