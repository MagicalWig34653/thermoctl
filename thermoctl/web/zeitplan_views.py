from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.authz import hat_recht, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    Zeitplanfehler,
    uhrzeit_in_minuten,
    wochenabschnitte,
    zeitplan_uebernehmen,
    zeitplanpunkt_anlegen,
    zeitplanpunkt_loeschen,
)
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

WOCHENTAGE = (
    (1, "Montag"),
    (2, "Dienstag"),
    (3, "Mittwoch"),
    (4, "Donnerstag"),
    (5, "Freitag"),
    (6, "Samstag"),
    (7, "Sonntag"),
)


def _zone_oder_404(
    session: Session, principal: Principal, zone_id: int, recht: str
) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, recht) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return zone


def _punkte(session: Session, zone_id: int) -> list[SchedulePoint]:
    return list(
        session.scalars(
            select(SchedulePoint)
            .where(SchedulePoint.zone_id == zone_id)
            .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
        )
    )


def _modi(session: Session) -> list[SetpointMode]:
    return list(
        session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name)
        )
    )


def _zeitplanseite(
    request: Request,
    session: Session,
    zone: Zone,
    principal: Principal,
    *,
    werte: dict[str, str] | None = None,
    fehler: Zeitplanfehler | None = None,
) -> Response:
    punkte = _punkte(session, zone.id)
    modi = _modi(session)
    abschnitte = wochenabschnitte(punkte, {modus.id: modus.name for modus in modi})
    nach_tag = {
        tag: [abschnitt for abschnitt in abschnitte if abschnitt.wochentag == tag]
        for tag, _name in WOCHENTAGE
    }
    return templates.TemplateResponse(
        request,
        "zeitplan.html",
        {
            "zone": zone,
            "punkte": punkte,
            "modi": modi,
            "wochentage": WOCHENTAGE,
            "abschnitte": nach_tag,
            "werte": werte or {"wochentag": "1", "uhrzeit": "06:00", "modus": ""},
            "fehler": {fehler.feld: fehler.meldung} if fehler else {},
            "darf_aendern": hat_recht(principal, "schedule.manage", zone.id),
        },
    )


@router.get("/zonen/{zone_id}/zeitplan")
async def zeitplan_anzeigen(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "zone.read")
    return _zeitplanseite(request, session, zone, principal)


@router.post("/zonen/{zone_id}/zeitplan/punkte")
async def zeitplanpunkt_erstellen(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "schedule.manage")
    formular = await request.form()
    werte = {
        name: str(formular.get(name, "")).strip()
        for name in ("wochentag", "uhrzeit", "modus")
    }
    try:
        try:
            wochentag = int(werte["wochentag"])
        except ValueError as exc:
            raise Zeitplanfehler(
                "wochentag", "Bitte einen Wochentag auswählen."
            ) from exc
        minute = uhrzeit_in_minuten(werte["uhrzeit"])
        try:
            modus_id = int(werte["modus"])
        except ValueError as exc:
            raise Zeitplanfehler("modus", "Bitte einen Modus auswählen.") from exc
        zeitplanpunkt_anlegen(
            session,
            zone,
            wochentag=wochentag,
            minute=minute,
            modus_id=modus_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except Zeitplanfehler as exc:
        return _zeitplanseite(
            request, session, zone, principal, werte=werte, fehler=exc
        )
    return RedirectResponse(
        f"/zonen/{zone.id}/zeitplan", status_code=status.HTTP_303_SEE_OTHER
    )


def _punkt_oder_404(session: Session, zone: Zone, punkt_id: int) -> SchedulePoint:
    punkt = session.get(SchedulePoint, punkt_id)
    if punkt is None or punkt.zone_id != zone.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return punkt


@router.get("/zonen/{zone_id}/zeitplan/punkte/{punkt_id}/loeschen")
async def zeitplanpunkt_loeschen_formular(
    zone_id: int,
    punkt_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "schedule.manage")
    punkt = _punkt_oder_404(session, zone, punkt_id)
    return templates.TemplateResponse(
        request,
        "zeitplanpunkt_loeschen.html",
        {"zone": zone, "punkt": punkt, "wochentage": dict(WOCHENTAGE)},
    )


@router.post("/zonen/{zone_id}/zeitplan/punkte/{punkt_id}/loeschen")
async def zeitplanpunkt_entfernen(
    zone_id: int,
    punkt_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "schedule.manage")
    punkt = _punkt_oder_404(session, zone, punkt_id)
    zeitplanpunkt_loeschen(
        session,
        zone,
        punkt,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zonen/{zone.id}/zeitplan", status_code=status.HTTP_303_SEE_OTHER
    )


def _uebernahmeseite(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    *,
    quelle_id: int | None = None,
    bestaetigung: bool = False,
    fehler: str = "",
) -> Response:
    quellen = [
        andere
        for andere in visible_zones(session, principal, "zone.read")
        if andere.id != zone.id
    ]
    return templates.TemplateResponse(
        request,
        "zeitplan_uebernehmen.html",
        {
            "zone": zone,
            "quellen": quellen,
            "quelle_id": quelle_id,
            "bestaetigung": bestaetigung,
            "fehler": fehler,
        },
    )


@router.get("/zonen/{zone_id}/zeitplan/uebernehmen")
async def zeitplan_uebernehmen_formular(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id, "schedule.manage")
    return _uebernahmeseite(request, session, principal, zone)


@router.post("/zonen/{zone_id}/zeitplan/uebernehmen")
async def zeitplan_uebernehmen_ausfuehren(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    ziel = _zone_oder_404(session, principal, zone_id, "schedule.manage")
    formular = await request.form()
    try:
        quelle_id = int(str(formular.get("quelle_id", "")))
    except ValueError:
        return _uebernahmeseite(
            request, session, principal, ziel, fehler="Bitte eine Quellzone auswählen."
        )
    quelle = next(
        (
            zone
            for zone in visible_zones(session, principal, "zone.read")
            if zone.id == quelle_id and zone.id != ziel.id
        ),
        None,
    )
    if quelle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    hat_plan = bool(_punkte(session, ziel.id))
    if hat_plan and str(formular.get("bestaetigt", "")) != "ja":
        return _uebernahmeseite(
            request,
            session,
            principal,
            ziel,
            quelle_id=quelle.id,
            bestaetigung=True,
        )
    zeitplan_uebernehmen(
        session,
        ziel,
        quelle,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zonen/{ziel.id}/zeitplan", status_code=status.HTTP_303_SEE_OTHER
    )
