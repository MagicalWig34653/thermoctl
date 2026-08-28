import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.csrf import CSRF_HEADER, csrf_pruefen
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.passwords import verify_password
from thermoctl.auth.sessions import (
    COOKIE_NAME,
    sitzung_anlegen,
    sitzung_aufloesen,
    sitzung_widerrufen,
)
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.web import templates

router = APIRouter()

# 14 Tage — deckungsgleich mit dem Standardwert von `setting.session_lifetime_seconds`.
SITZUNGS_LEBENSDAUER_S = 60 * 60 * 24 * 14

# Je Benutzername gezaehlte Fehlversuche. Laeuft im Prozessspeicher, nicht in der
# Datenbank: sie soll Rateversuche bremsen, nicht ueberdauern. Es gibt ausdruecklich
# keine Kontosperre — in einem Einhaushalt-System waere sie vor allem eine bequeme
# Moeglichkeit, sich selbst auszusperren.
FEHLVERSUCHE: dict[str, int] = {}

_FEHLERMELDUNG = "Benutzername oder Passwort falsch."


def schlafen(sekunden: float) -> None:
    """Eigene Funktion statt eines direkten `time.sleep()`-Aufrufs, damit Tests die
    Verzoegerung durch `monkeypatch.setattr` ersetzen koennen, ohne wirklich zu warten."""
    time.sleep(sekunden)


@router.get("/login")
async def login_formular(request: Request) -> Response:
    return templates.TemplateResponse(request, "anmeldung.html", {})


@router.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()

    bisherige_fehlversuche = FEHLVERSUCHE.get(username, 0)
    schlafen(min(2**bisherige_fehlversuche, 5))

    benutzer = session.scalar(select(User).where(User.username == username))
    erfolgreich = (
        benutzer is not None
        and benutzer.is_active
        and verify_password(password, benutzer.password_hash)
    )

    if not erfolgreich:
        FEHLVERSUCHE[username] = bisherige_fehlversuche + 1
        # Dieselbe Zusammenfassung fuer existierende wie fuer nicht existierende
        # Benutzernamen — der Audit-Eintrag darf verraten, was passiert ist, die
        # HTTP-Antwort an den Aufrufer aber nicht, ob der Benutzername existiert.
        audit.record(
            session, source="web", action="login_failed", object_type="user",
            object_id=username, summary=f"Anmeldung als '{username}' fehlgeschlagen",
        )
        return templates.TemplateResponse(
            request, "anmeldung.html", {"fehler": _FEHLERMELDUNG},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    assert benutzer is not None
    FEHLVERSUCHE[username] = 0
    benutzer.last_login_at = utcnow()

    _eintrag, geheimnis = sitzung_anlegen(
        session, benutzer, SITZUNGS_LEBENSDAUER_S,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    audit.record(
        session, source="web", action="login", object_type="user",
        object_id=str(benutzer.id), summary=f"Anmeldung als '{benutzer.username}'",
        user_id=benutzer.id,
    )

    antwort = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    antwort.set_cookie(
        COOKIE_NAME, geheimnis, max_age=SITZUNGS_LEBENSDAUER_S,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    return antwort


@router.post("/logout")
async def logout(request: Request, session: Annotated[Session, Depends(get_session)]) -> Response:
    settings = get_settings()
    cookie_wert = request.cookies.get(COOKIE_NAME)
    uebermitteltes_csrf_token = request.headers.get(CSRF_HEADER)

    # Ein Token wird nur geprueft, wenn es tatsaechlich mitgeschickt wurde — die
    # eigentliche Absicherung gegen klassisches Cross-Site-Request-Forgery leistet
    # bereits `SameSite=Lax`. Wird trotzdem eines mitgeschickt, muss es zur Sitzung
    # passen, sonst wird die Anfrage abgewiesen.
    if cookie_wert is not None and uebermitteltes_csrf_token is not None:
        gueltig = csrf_pruefen(
            uebermitteltes_csrf_token, cookie_wert, settings.secret_key.get_secret_value()
        )
        if not gueltig:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Ungueltiges CSRF-Token"
            )

    if cookie_wert is not None:
        sitzung = sitzung_aufloesen(session, cookie_wert)
        if sitzung is not None:
            sitzung_widerrufen(session, sitzung)

    antwort = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    antwort.delete_cookie(COOKIE_NAME)
    return antwort
