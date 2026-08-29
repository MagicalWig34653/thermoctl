import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.passwords import hash_password, verify_password
from thermoctl.auth.sessions import (
    COOKIE_NAME,
    sitzung_anlegen,
    sitzung_aufloesen,
    sitzung_widerrufen,
    sitzungslebensdauer_s,
)
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.web import templates

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

# Je Benutzername gezaehlte Fehlversuche. Laeuft im Prozessspeicher, nicht in der
# Datenbank: sie soll Rateversuche bremsen, nicht ueberdauern. Es gibt ausdruecklich
# keine Kontosperre — in einem Einhaushalt-System waere sie vor allem eine bequeme
# Moeglichkeit, sich selbst auszusperren.
FEHLVERSUCHE: dict[str, int] = {}

_FEHLERMELDUNG = "Benutzername oder Passwort falsch."


# Einmalig beim Laden erzeugt, aus Zufall: nur die Rechenzeit von `verify_password`
# wird gebraucht, nie das zugehoerige Passwort. Bewusst kein fest eingetragener Wert --
# im Repo steht kein Hash, auch kein bedeutungsloser.
_VERGLEICHS_HASH = hash_password(secrets.token_urlsafe(32))


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
    # Die Passwortpruefung laeuft IMMER, auch fuer einen unbekannten Benutzernamen --
    # dann gegen einen Wegwerf-Hash. Andernfalls wuerde Pythons Kurzschlussauswertung
    # `verify_password` bei unbekanntem Namen ueberspringen, und die Anfrage waere
    # messbar schneller als fuer einen existierenden Namen: Argon2id ist absichtlich
    # langsam, und genau diese Rechenzeit verriete, welche Konten es gibt. Gleiche
    # Meldung und gleiche Wartezeit allein genuegen dafuer nicht.
    if benutzer is None:
        verify_password(password, _VERGLEICHS_HASH)
        erfolgreich = False
    else:
        erfolgreich = benutzer.is_active and verify_password(password, benutzer.password_hash)

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

    lebensdauer_s = sitzungslebensdauer_s(session)
    _eintrag, geheimnis = sitzung_anlegen(
        session, benutzer, lebensdauer_s,
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
        COOKIE_NAME, geheimnis, max_age=lebensdauer_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    # Nicht httpOnly: die Oberflaeche (HTMX) liest den Wert und schickt ihn als
    # `X-CSRF-Token`-Header mit.
    antwort.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(geheimnis, settings.secret_key.get_secret_value()),
        max_age=lebensdauer_s, httponly=False, samesite="lax", secure=settings.secure_cookies,
    )
    return antwort


@router.post("/logout")
async def logout(request: Request, session: Annotated[Session, Depends(get_session)]) -> Response:
    # Der CSRF-Nachweis haengt am Router (`csrf_schutz`) und ist hier bereits erbracht.
    cookie_wert = request.cookies.get(COOKIE_NAME)
    if cookie_wert is not None:
        sitzung = sitzung_aufloesen(session, cookie_wert)
        if sitzung is not None:
            sitzung_widerrufen(session, sitzung)

    antwort = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    antwort.delete_cookie(COOKIE_NAME)
    return antwort
