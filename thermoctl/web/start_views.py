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
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import get_session
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_aufloesen
from thermoctl.db.models.identity import User
from thermoctl.db.models.zone import Zone

router = APIRouter()
templates = Jinja2Templates(directory="thermoctl/web/templates")


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

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "benutzer": benutzer,
            "zonen": session.scalar(select(func.count()).select_from(Zone)) or 0,
            "benutzerzahl": session.scalar(select(func.count()).select_from(User)) or 0,
        },
    )
