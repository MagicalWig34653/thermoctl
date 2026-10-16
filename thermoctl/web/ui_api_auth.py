"""Schnittstellen der Svelte-Oberfläche, die ohne Anmeldung erreichbar sind.

Zwei Dinge gehören hierher, beide vor der Anmeldung:

* die Ersteinrichtung (`/ui-api/setup`) -- dieselbe Domänenfunktion `run_setup` und
  dieselbe Sperre wie das HTML-Formular in `setup_views.py`;
* die Angaben, die die Anmeldeseite braucht, bevor jemand angemeldet ist
  (`/ui-api/login-options`).

**Nichts davon gibt etwas preis.** Der Setup-Status sagt nur "offen" oder 404; das Token
wird weder geloggt noch zurückgegeben, und die Fehlertexte unterscheiden ein ungültiges,
verbrauchtes und abgelaufenes Token nicht (das entscheidet `run_setup`). Nach der
Einrichtung gibt es die Schnittstelle nicht mehr (404) -- dauerhaft zu, nicht nur versteckt.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, get_session
from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.config import get_settings
from thermoctl.setup import InvalidTimezone, run_setup, setup_needed

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ui-api", dependencies=[Depends(csrf_protection)], include_in_schema=False
)

_UNPROCESSABLE = 422
_CLOSED = "Die Einrichtung ist bereits abgeschlossen."

# Spaltenbreiten des Datenmodells (identity.User, operations.Setting); längere Eingaben
# wären auf MariaDB ein Datenbankfehler statt einer Rückmeldung am Feld.
_MAX_LENGTH = {"username": 64, "display_name": 128, "timezone": 64}
# Raw (untrimmed) ceilings: password so Argon2id cannot be fed megabytes, the token far
# above any real one.
_MAX_PASSWORD = 1024
_MAX_SETUP_TOKEN = 256


class LoginOptionsOut(BaseModel):
    passkeys_available: bool


class SetupStatusOut(BaseModel):
    open: bool


class SetupIn(BaseModel):
    username: str
    display_name: str
    password: str
    timezone: str
    setup_token: str


def _closed() -> JSONResponse:
    return JSONResponse({"detail": _CLOSED}, status_code=status.HTTP_404_NOT_FOUND)


def _field_error(field: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": detail, "field": field}, status_code=status_code)


@router.get("/login-options")
def login_options() -> LoginOptionsOut:
    """Was die Anmeldeseite anbietet. Nur ein Schalter der Betriebskonfiguration."""
    return LoginOptionsOut(passkeys_available=get_settings().passkeys_available())


@router.get("/setup")
def setup_status(session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    """Ist die Ersteinrichtung offen? 404 sobald ein Benutzer existiert."""
    if not setup_needed(session):
        return _closed()
    return JSONResponse(SetupStatusOut(open=True).model_dump())


@router.post("/setup")
def setup(
    body: SetupIn, session: Annotated[Session, Depends(get_session)]
) -> JSONResponse:
    """Schließt die Einrichtung ab (`run_setup`); Feldfehler mit `field`.

    Bewusst `def` und nicht `async def`: Das Passwort-Hashing (Argon2) soll in einem
    Arbeits-Thread laufen, nicht auf der Ereignisschleife der Regelung.
    """
    if not setup_needed(session):
        return _closed()
    for field, raw, limit in (
        ("username", body.username, _MAX_LENGTH["username"]),
        ("display_name", body.display_name, _MAX_LENGTH["display_name"]),
        ("timezone", body.timezone, _MAX_LENGTH["timezone"]),
        ("password", body.password, _MAX_PASSWORD),
        ("setup_token", body.setup_token, _MAX_SETUP_TOKEN),
    ):
        if len(raw) > limit:
            return _field_error(field, f"Höchstens {limit} Zeichen.", _UNPROCESSABLE)
    values = {
        "username": body.username.strip(),
        "display_name": body.display_name.strip(),
        "timezone": body.timezone.strip(),
    }
    for field, value in values.items():
        if not value:
            return _field_error(field, "Bitte ausfüllen.", _UNPROCESSABLE)
    try:
        run_setup(
            session,
            username=values["username"],
            display_name=values["display_name"],
            password=body.password,
            timezone_name=values["timezone"],
            token=body.setup_token.strip(),
        )
    except PermissionError as exc:
        # Never the token itself, not even its length -- only that it was refused.
        log.warning("Einrichtung abgelehnt: Token oder Zustand nicht gültig")
        return _field_error("setup_token", str(exc), status.HTTP_403_FORBIDDEN)
    except PasswordTooShort as exc:
        return _field_error("password", str(exc), _UNPROCESSABLE)
    except InvalidTimezone as exc:
        return _field_error("timezone", str(exc), _UNPROCESSABLE)
    return JSONResponse({"ok": True})
