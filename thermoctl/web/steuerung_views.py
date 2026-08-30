"""Zwei Seiten, die dieselbe Einstellungszeile bedienen -- und trotzdem getrennt gehoeren.

`/steuerung` ist **Betrieb**: Schaltet die Anlage gerade wirklich, was entscheidet sie
gerade, und der Knopf, der beides umlegt. Das sieht man sich an, wenn etwas nicht stimmt.

`/einstellungen` sind die **Regelvorgaben**: Hysterese, Mindestschaltdauern, Zykluszeit,
Aufbewahrung, Zeitzone. Die stellt man einmal ein und dann jahrelang nicht mehr.

Zuerst standen beide auf einer Seite. Das war bequem zu bauen und falsch zu benutzen: Wer
nachsehen wollte, ob die Anlage scharf ist, scrollte an neun Zahlenfeldern vorbei, die ihn
in dem Moment nicht interessierten -- und wer eine Vorgabe aendern wollte, landete zuerst
beim Scharfschalt-Knopf.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.authz import hat_recht, require, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.domain.steuerung import (
    BESCHRIFTUNG,
    GANZZAHLIG,
    GRENZEN,
    Steuerungsfehler,
    einstellungen,
    einstellungen_speichern,
    scharf_schalten,
)
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _seite(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    fehler: Steuerungsfehler | None = None,
) -> Response:
    zeile = einstellungen(session)
    zonen = visible_zones(session, principal, "zone.read")
    jetzt = utcnow()

    zustaende = {
        zone_id: (zustand, status)
        for zone_id, zustand, status in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zonen]))
        )
    }
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_([zone.id for zone in zonen]))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "steuerung.html",
        {
            "einstellungen": zeile,
            "zonen": zonen,
            "zustaende": zustaende,
            "entscheidungen": entscheidungen,
            "sollwerte": {
                zone.id: aufgeloester_sollwert(session, zone, jetzt) for zone in zonen
            },
            "fehler": {fehler.feld: fehler.meldung} if fehler else {},
            "darf_scharf": hat_recht(principal, "control.arm"),
        },
    )


def _vorgabenseite(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    werte: dict[str, str] | None = None,
    fehler: Steuerungsfehler | None = None,
) -> Response:
    zeile = einstellungen(session)
    if werte is None:
        werte = {feld: str(getattr(zeile, feld)) for feld in GRENZEN}
        werte["timezone"] = zeile.timezone
    return templates.TemplateResponse(
        request,
        "einstellungen.html",
        {
            "felder": [(feld, BESCHRIFTUNG[feld], feld in GANZZAHLIG) for feld in GRENZEN],
            "werte": werte,
            "fehler": {fehler.feld: fehler.meldung} if fehler else {},
            "darf_aendern": hat_recht(principal, "setting.manage"),
        },
    )


@router.get("/steuerung")
async def steuerung_anzeigen(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Lesen darf, wer die Anlage sehen darf. Der Betriebszustand ist die Antwort auf
    # "schaltet das Ding gerade wirklich?" -- diese Frage soll niemand raten muessen.
    require(principal, "zone.read")
    return _seite(request, session, principal)


@router.get("/einstellungen")
async def einstellungen_anzeigen(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.read")
    return _vorgabenseite(request, session, principal)


@router.post("/einstellungen")
async def vorgaben_speichern(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "setting.manage")
    formular = await request.form()
    werte = {
        name: str(formular.get(name, "")).strip()
        for name in (*GRENZEN, "timezone")
    }
    try:
        einstellungen_speichern(
            session,
            werte,
            werte["timezone"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except Steuerungsfehler as exc:
        return _vorgabenseite(request, session, principal, werte=werte, fehler=exc)
    return RedirectResponse("/einstellungen", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/steuerung/scharf")
async def scharfschalten(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Eigenes Recht, nicht `setting.manage`: Das hier bewegt ein Ventil.
    require(principal, "control.arm")
    formular = await request.form()
    scharf = str(formular.get("scharf", "")) == "ja"
    try:
        scharf_schalten(
            session,
            scharf,
            begruendung=str(formular.get("begruendung", "")),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except Steuerungsfehler as exc:
        return _seite(request, session, principal, fehler=exc)
    return RedirectResponse("/steuerung", status_code=status.HTTP_303_SEE_OTHER)
