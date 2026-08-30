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
    create_session,
    resolve_session,
    revoke_session,
    session_lifetime_s,
)
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.setup import einrichtung_noetig
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

_ERROR_MESSAGE = "Benutzername oder Passwort falsch."


# Einmalig beim Laden erzeugt, aus Zufall: nur die Rechenzeit von `verify_password`
# wird gebraucht, nie das zugehoerige Passwort. Bewusst kein fest eingetragener Wert --
# im Repo steht kein Hash, auch kein bedeutungsloser.
_VERGLEICHS_HASH = hash_password(secrets.token_urlsafe(32))


def schlafen(seconds: float) -> None:
    """Eigene Funktion statt eines direkten `time.sleep()`-Aufrufs, damit Tests die
    Verzoegerung durch `monkeypatch.setattr` ersetzen koennen, ohne wirklich zu warten."""
    time.sleep(seconds)


@router.get("/login")
async def login_form(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Response:
    # Siehe start_views.start(): Ohne einen einzigen Benutzer fuehrt das Formular
    # nirgendwohin. Nur GET -- ein POST auf /login ohne Benutzer scheitert ohnehin an
    # der gewoehnlichen Pruefung, und der soll seine gleichlautende Fehlermeldung
    # behalten, statt am Weiterleitungsziel erkennen zu lassen, was der Dienst weiss.
    if einrichtung_noetig(session):
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "anmeldung.html", {"passkeys_moeglich": get_settings().passkeys_moeglich()}
    )


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

    user = session.scalar(select(User).where(User.username == username))
    # Die Passwortpruefung laeuft IMMER, auch fuer einen unbekannten Benutzernamen --
    # dann gegen einen Wegwerf-Hash. Andernfalls wuerde Pythons Kurzschlussauswertung
    # `verify_password` bei unbekanntem Namen ueberspringen, und die Anfrage waere
    # messbar schneller als fuer einen existierenden Namen: Argon2id ist absichtlich
    # langsam, und genau diese Rechenzeit verriete, welche Konten es gibt. Gleiche
    # Meldung und gleiche Wartezeit allein genuegen dafuer nicht.
    if user is None:
        verify_password(password, _VERGLEICHS_HASH)
        erfolgreich = False
    else:
        erfolgreich = user.is_active and verify_password(password, user.password_hash)

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
            request, "anmeldung.html",
            {"errors": _ERROR_MESSAGE, "passkeys_moeglich": settings.passkeys_moeglich()},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    assert user is not None
    FEHLVERSUCHE[username] = 0
    user.last_login_at = utcnow()

    lifetime_s = session_lifetime_s(session)
    _entry, geheimnis = create_session(
        session, user, lifetime_s,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    audit.record(
        session, source="web", action="login", object_type="user",
        object_id=str(user.id), summary=f"Anmeldung als '{user.username}'",
        user_id=user.id,
    )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME, geheimnis, max_age=lifetime_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    # Nicht httpOnly: die Oberflaeche (HTMX) liest den Wert und schickt ihn als
    # `X-CSRF-Token`-Header mit.
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(geheimnis, settings.secret_key.get_secret_value()),
        max_age=lifetime_s, httponly=False, samesite="lax", secure=settings.secure_cookies,
    )
    return response


@router.post("/logout")
async def logout(request: Request, session: Annotated[Session, Depends(get_session)]) -> Response:
    # Der CSRF-Nachweis haengt am Router (`csrf_schutz`) und ist hier bereits erbracht.
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is not None:
        http_session = resolve_session(session, cookie_value)
        if http_session is not None:
            revoke_session(session, http_session)

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response
