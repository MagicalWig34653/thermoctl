from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.setup import einrichtung_durchfuehren, einrichtung_noetig
from thermoctl.web import templates
from thermoctl.web.forms import form_again, password_form_error

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

_GESCHLOSSEN = "Die Einrichtung ist bereits abgeschlossen."


def _ensure_open(session: Session) -> None:
    # Permanently closed once a user exists -- not just hidden. Otherwise, in the
    # unfavorable case, whoever finds the page first on the network wins.
    if not einrichtung_noetig(session):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_GESCHLOSSEN)


@router.get("/setup")
async def setup_form(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Response:
    _ensure_open(session)
    return templates.TemplateResponse(request, "einrichtung.html", {"errors": {}})


@router.post("/setup")
async def setup(
    request: Request,
    username: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    password: Annotated[str, Form()],
    timezone: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
    # With a default instead of a bare `Form()`: FastAPI treats an empty form field
    # for a *required* `Form()` as missing and responds with 422, before our code
    # even runs. A missing or empty token here, though, is a normal case to be
    # rejected (403) -- not a format error.
    setup_token: Annotated[str, Form()] = "",
) -> Response:
    _ensure_open(session)
    # Already filled-in fields are kept in the form when the input is rejected --
    # except the password, which never flows back into a response.
    form_values = {
        "username": username, "display_name": display_name, "timezone": timezone,
        "setup_token": setup_token,
    }
    try:
        einrichtung_durchfuehren(
            session, username=username, display_name=display_name, password=password,
            timezone_name=timezone, token=setup_token,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PasswordTooShort as exc:
        # Input that's too short is a user form error, not a fault of the service --
        # back to the form with an understandable message instead of 500.
        return form_again(
            request,
            "einrichtung.html",
            form_values,
            password_form_error(exc),
        )

    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
