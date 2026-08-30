from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.modi import (
    HOECHSTTEMPERATUR_C,
    MINDESTTEMPERATUR_C,
    Domaenenfehler,
    loeschsperre,
    modus_aendern,
    modus_anlegen,
    modus_loeschen,
    sollwerte_aendern,
    temperatur_pruefen,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _modi(session: Session) -> list[SetpointMode]:
    return list(
        session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name)
        )
    )


def _modus_oder_404(session: Session, modus_id: int) -> SetpointMode:
    modus = session.get(SetpointMode, modus_id)
    if modus is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return modus


def _sortierung(wert: str) -> int:
    try:
        return int(wert)
    except ValueError as exc:
        raise Domaenenfehler("sort_order", "Die Sortierung muss eine ganze Zahl sein.") from exc


def _modusformular(
    request: Request,
    *,
    werte: dict[str, str],
    fehler: Domaenenfehler | None = None,
    modus: SetpointMode | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "modus_formular.html",
        {
            "werte": werte,
            "fehler": {fehler.feld: fehler.meldung} if fehler is not None else {},
            "modus": modus,
        },
    )


@router.get("/modi")
async def modusliste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modi = _modi(session)
    return templates.TemplateResponse(request, "modi.html", {"modi": modi})


@router.get("/modi/neu")
async def modus_neu_formular(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
) -> Response:
    require(principal, "mode.manage")
    return _modusformular(
        request, werte={"code": "", "name": "", "sort_order": "0"}
    )


@router.post("/modi")
async def modus_erstellen(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    formular = await request.form()
    werte = {name: str(formular.get(name, "")) for name in ("code", "name", "sort_order")}
    try:
        modus_anlegen(
            session,
            code=werte["code"],
            name=werte["name"],
            sort_order=_sortierung(werte["sort_order"]),
            user_id=principal.user_id,
        )
    except Domaenenfehler as exc:
        return _modusformular(request, werte=werte, fehler=exc)
    return RedirectResponse("/modi", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/modi/{modus_id}")
async def modus_bearbeiten_formular(
    request: Request,
    modus_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modus = _modus_oder_404(session, modus_id)
    return _modusformular(
        request,
        modus=modus,
        werte={"code": modus.code, "name": modus.name, "sort_order": str(modus.sort_order)},
    )


@router.post("/modi/{modus_id}")
async def modus_speichern(
    request: Request,
    modus_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modus = _modus_oder_404(session, modus_id)
    formular = await request.form()
    werte = {name: str(formular.get(name, "")) for name in ("code", "name", "sort_order")}
    try:
        modus_aendern(
            session,
            modus,
            code=werte["code"],
            name=werte["name"],
            sort_order=_sortierung(werte["sort_order"]),
            user_id=principal.user_id,
        )
    except Domaenenfehler as exc:
        return _modusformular(request, modus=modus, werte=werte, fehler=exc)
    return RedirectResponse("/modi", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/modi/{modus_id}/loeschen")
async def modus_loeschen_formular(
    request: Request,
    modus_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modus = _modus_oder_404(session, modus_id)
    return templates.TemplateResponse(
        request,
        "modus_loeschen.html",
        {"modus": modus, "sperre": loeschsperre(session, modus)},
    )


@router.post("/modi/{modus_id}/loeschen")
async def modus_entfernen(
    request: Request,
    modus_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "mode.manage")
    modus = _modus_oder_404(session, modus_id)
    try:
        modus_loeschen(session, modus, user_id=principal.user_id)
    except Domaenenfehler as exc:
        return templates.TemplateResponse(
            request, "modus_loeschen.html", {"modus": modus, "sperre": exc.meldung}
        )
    return RedirectResponse("/modi", status_code=status.HTTP_303_SEE_OTHER)


def _zone_oder_404(session: Session, principal: Principal, zone_id: int) -> Zone:
    zonen = visible_zones(session, principal, "setpoint.write")
    zone = next((eintrag for eintrag in zonen if eintrag.id == zone_id), None)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return zone


def _sollwertseite(
    request: Request,
    session: Session,
    zone: Zone,
    *,
    werte: dict[str, str] | None = None,
    fehler: dict[str, str] | None = None,
) -> Response:
    modi = _modi(session)
    if werte is None:
        gespeichert = {
            zeile.setpoint_mode_id: zeile.temperature_c
            for zeile in session.scalars(
                select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
            )
        }
        werte = {
            f"sollwert_{modus.id}": str(gespeichert.get(modus.id, "")) for modus in modi
        }
    return templates.TemplateResponse(
        request,
        "sollwerte.html",
        {
            # Aus der Domaene: Zahlen im Markup waeren eine zweite Fassung
            # der Grenze und blieben beim naechsten Verschieben zurueck.
            "mindesttemperatur": MINDESTTEMPERATUR_C,
            "hoechsttemperatur": HOECHSTTEMPERATUR_C,
            "zone": zone,
            "modi": modi,
            "werte": werte,
            "fehler": fehler or {},
        },
    )


@router.get("/zonen/{zone_id}/sollwerte")
async def sollwerte_formular(
    request: Request,
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id)
    return _sollwertseite(request, session, zone)


@router.post("/zonen/{zone_id}/sollwerte")
async def sollwerte_speichern(
    request: Request,
    zone_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_oder_404(session, principal, zone_id)
    modi = _modi(session)
    formular = await request.form()
    rohwerte = {
        f"sollwert_{modus.id}": str(formular.get(f"sollwert_{modus.id}", "")).strip()
        for modus in modi
    }
    werte: dict[int, Decimal | None] = {}
    for modus in modi:
        feld = f"sollwert_{modus.id}"
        if not rohwerte[feld]:
            werte[modus.id] = None
            continue
        try:
            werte[modus.id] = Decimal(rohwerte[feld])
        except InvalidOperation:
            return _sollwertseite(
                request,
                session,
                zone,
                werte=rohwerte,
                fehler={feld: "Der Sollwert muss eine Zahl sein."},
            )
    try:
        sollwerte_aendern(session, zone, werte, user_id=principal.user_id)
    except Domaenenfehler as exc:
        # Die Domänenregel kennt bewusst keine HTML-Feldnamen. Der erste Wert, der
        # ihre Temperaturregel verletzt, wird am zugehörigen Modusfeld angezeigt.
        for modus in modi:
            temperatur = werte[modus.id]
            if temperatur is not None:
                try:
                    temperatur_pruefen(temperatur)
                except Domaenenfehler:
                    return _sollwertseite(
                        request,
                        session,
                        zone,
                        werte=rohwerte,
                        fehler={f"sollwert_{modus.id}": exc.meldung},
                    )
        # Unerreichbar, solange jeder `Domaenenfehler` aus `sollwerte_aendern` aus
        # `temperatur_pruefen` stammt -- die Schleife darueber ruft dieselbe Pruefung
        # erneut auf und findet den Wert, der ihn ausgeloest hat. Die Zeile bleibt als
        # Notausgang: Kommt in der Domaene spaeter eine Regel dazu, die sich nicht einem
        # einzelnen Feld zuordnen laesst, faellt sie hier auf, statt still eine falsche
        # Feldmeldung zu erzeugen.
        raise  # pragma: no cover
    return RedirectResponse(
        f"/zonen/{zone.id}/sollwerte", status_code=status.HTTP_303_SEE_OTHER
    )
