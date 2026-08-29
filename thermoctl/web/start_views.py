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
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_aufloesen
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.zustand import ZoneState
from thermoctl.domain.authz import principal_fuer_benutzer, visible_zones
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
    zustaende = {
        zone_id: (zustand, sensorstatus)
        for zone_id, zustand, sensorstatus in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in sichtbare_zonen]))
        )
    }

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "benutzer": benutzer,
            "zonen": sichtbare_zonen,
            "zustaende": zustaende,
            "benutzerzahl": session.scalar(select(func.count()).select_from(User)) or 0,
        },
    )
