from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import get_session
from thermoctl.setup import einrichtung_durchfuehren, einrichtung_noetig
from thermoctl.web import templates

router = APIRouter()

_GESCHLOSSEN = "Die Einrichtung ist bereits abgeschlossen."


def _sicherstellen_offen(session: Session) -> None:
    # Dauerhaft geschlossen, sobald ein Benutzer existiert -- nicht nur ausgeblendet.
    # Sonst gewinnt im unguenstigen Fall der Erste im Netz, der die Seite noch findet.
    if not einrichtung_noetig(session):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_GESCHLOSSEN)


@router.get("/setup")
async def setup_formular(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Response:
    _sicherstellen_offen(session)
    return templates.TemplateResponse(request, "einrichtung.html", {})


@router.post("/setup")
async def setup(
    request: Request,
    username: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    password: Annotated[str, Form()],
    timezone: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
    # Mit Default statt reinem `Form()`: FastAPI behandelt ein leeres Formularfeld
    # bei einem *erforderlichen* `Form()` als fehlend und antwortet mit 422, noch
    # bevor unser Code laeuft. Ein fehlendes oder leeres Token ist hier aber ein
    # normaler, abzuweisender Fall (403) -- kein Formatfehler.
    setup_token: Annotated[str, Form()] = "",
) -> Response:
    _sicherstellen_offen(session)
    try:
        einrichtung_durchfuehren(
            session, username=username, display_name=display_name, passwort=password,
            zeitzone=timezone, token=setup_token,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
