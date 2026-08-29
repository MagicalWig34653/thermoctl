"""Startseite.

Sie ist das Ziel jeder Weiterleitung nach der Anmeldung. Solange es keine
Zonenuebersicht gibt (die kommt in Teilprojekt 3), zeigt sie den Stand der Anlage
und fuehrt zu den vorhandenen Bereichen.

Anders als die geschuetzten Verwaltungsseiten antwortet sie einem nicht angemeldeten
Besucher nicht mit 401, sondern leitet auf die Anmeldung weiter: Wer die Adresse des
Dienstes im Browser eingibt, soll ein Anmeldeformular sehen und keine Fehlermeldung.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_aufloesen
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.authz import hat_recht, principal_fuer_benutzer, visible_zones
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.web import templates

router = APIRouter(dependencies=[Depends(csrf_schutz)])


@router.get("/")
def start(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    cookie_wert = request.cookies.get(COOKIE_NAME)
    sitzung = sitzung_aufloesen(session, cookie_wert) if cookie_wert else None
    benutzer = session.get(User, sitzung.user_id) if sitzung else None
    if benutzer is None or not benutzer.is_active:
        return RedirectResponse("/login", status_code=303)

    principal = principal_fuer_benutzer(session, benutzer)
    sichtbare_zonen = visible_zones(session, principal, "zone.read")
    jetzt = utcnow()
    zustaende = {
        zone_id: (zustand, sensorstatus)
        for zone_id, zustand, sensorstatus in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in sichtbare_zonen]))
        )
    }
    zone_ids = [zone.id for zone in sichtbare_zonen]
    uebersteuerungen: dict[int, ZoneOverride] = {}
    for eintrag in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > jetzt),
        )
        .order_by(ZoneOverride.created_at.desc())
    ):
        uebersteuerungen.setdefault(eintrag.zone_id, eintrag)
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "benutzer": benutzer,
            "zonen": sichtbare_zonen,
            "zustaende": zustaende,
            "sollwerte": {
                zone.id: aufgeloester_sollwert(session, zone, jetzt) for zone in sichtbare_zonen
            },
            "uebersteuerungen": uebersteuerungen,
            "entscheidungen": entscheidungen,
            "darf_uebersteuern": {
                zone.id
                for zone in sichtbare_zonen
                if hat_recht(principal, "override.create", zone.id)
            },
            "darf_aufheben": {
                zone.id
                for zone in sichtbare_zonen
                if hat_recht(principal, "override.cancel", zone.id)
            },
            "darf_parameter": {
                zone.id for zone in sichtbare_zonen if hat_recht(principal, "zone.manage", zone.id)
            },
            "uebersteuerungsfehler": request.query_params.get("uebersteuerungsfehler"),
            "fehler_zone_id": request.query_params.get("zone_id"),
            "uebersteuerungswerte": request.query_params,
            "benutzerzahl": session.scalar(select(func.count()).select_from(User)) or 0,
        },
    )
