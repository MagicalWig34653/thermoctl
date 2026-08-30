"""Die HTTP-Seite der Passkey-Zeremonien.

Duenn: Die Regeln stehen in `thermoctl/domain/passkey.py`. Hier wird entgegengenommen,
weitergereicht und einheitlich abgelehnt.

**Jede gescheiterte Anmeldung sieht gleich aus** — gleicher Status, gleicher Text. Ob eine
Credential-ID unbekannt ist, ein Konto gesperrt oder eine Signatur falsch, steht im
Audit-Protokoll. Sonst liesse sich an den Antworten ablesen, welche Konten es gibt.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.auth.sessions import COOKIE_NAME, create_session, session_lifetime_s
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.domain.passkey import (
    PasskeyError,
    begin_authentication,
    begin_registration,
    cleanup_old_challenges,
    finish_registration,
    remove_passkey,
    verify_authentication,
)
from thermoctl.domain.principal import Principal
from thermoctl.web import ist_teilaustausch, templates

router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

_ABGELEHNT = "Die Anmeldung war nicht erfolgreich."


def _passkeys_an(settings: Settings) -> None:
    """Ohne Relying-Party-ID gibt es die Wege gar nicht — nicht halb, sondern gar nicht."""
    if not settings.passkeys_moeglich():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkeys sind nicht eingerichtet.")


def _ablehnen() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"status": "abgelehnt", "meldung": _ABGELEHNT},
    )


@router.post("/passkey/authentication/options")
async def authentication_options(
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Liefert die Argumente fuer `navigator.credentials.get()`."""
    settings = get_settings()
    _passkeys_an(settings)
    # Nebenbei aufraeumen: abgelaufene Challenges sind wertlos, sammeln sich aber an.
    cleanup_old_challenges(session)
    return JSONResponse(begin_authentication(session, settings))


@router.post("/passkey/authentication/verify")
async def finish_authentication(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Nimmt die Assertion entgegen und meldet bei Erfolg an."""
    settings = get_settings()
    _passkeys_an(settings)
    try:
        response: dict[str, Any] = await request.json()
    except Exception:
        return _ablehnen()
    if not isinstance(response, dict):
        return _ablehnen()

    try:
        user = verify_authentication(session, settings, response)
    except PasskeyError:
        # Der Grund steht bereits im Protokoll; nach aussen geht er nicht.
        return _ablehnen()

    user.last_login_at = utcnow()
    lifetime_s = session_lifetime_s(session)
    _entry, geheimnis = create_session(
        session, user, lifetime_s,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    result = JSONResponse({"status": "angemeldet", "weiter": "/"})
    result.set_cookie(
        COOKIE_NAME, geheimnis, max_age=lifetime_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    result.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(geheimnis, settings.secret_key.get_secret_value()),
        max_age=lifetime_s, httponly=False, samesite="lax",
        secure=settings.secure_cookies,
    )
    return result


def _own_user(session: Session, principal: Principal) -> User:
    user = None if principal.user_id is None else session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return user


@router.get("/passkeys")
async def passkey_list(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Die eigenen Passkeys. Fremde sieht hier niemand — es gibt keinen Weg dorthin."""
    user = _own_user(session, principal)
    return templates.TemplateResponse(
        request,
        "passkeys.html",
        {
            "passkeys": session.scalars(
                select(UserPasskey)
                .where(UserPasskey.user_id == user.id)
                .order_by(UserPasskey.created_at)
            ).all(),
            "moeglich": get_settings().passkeys_moeglich(),
            "ist_htmx": ist_teilaustausch(request),
        },
    )


@router.post("/passkey/registration/options")
async def registrierung_argumente(
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    user = _own_user(session, principal)
    return JSONResponse(begin_registration(session, settings, user))


@router.post("/passkey/registration/verify")
async def save_registration(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    user = _own_user(session, principal)
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unlesbare Antwort") from None

    bezeichnung = str(payload.pop("bezeichnung", "") or "")
    try:
        entry = finish_registration(
            session, settings, user, payload, bezeichnung
        )
    except PasskeyError as exc:
        # Hier darf der Grund nach aussen: Der Aufrufer ist angemeldet und registriert
        # seinen eigenen Schluessel — eine unverstaendliche Ablehnung waere hier nur
        # hinderlich, ohne irgendetwas zu schuetzen.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "abgelehnt", "meldung": str(exc)},
        )
    return JSONResponse({"status": "gespeichert", "bezeichnung": entry.bezeichnung})


@router.post("/passkeys/{passkey_id}/remove")
async def delete_passkey(
    passkey_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    from fastapi.responses import RedirectResponse

    user = _own_user(session, principal)
    entry = session.get(UserPasskey, passkey_id)
    # Ein fremder Passkey ist nicht auffindbar, nicht verboten — sonst verriete die
    # Antwort, welche Kennungen es gibt.
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey nicht gefunden")
    remove_passkey(session, user, entry)
    return RedirectResponse("/passkeys", status_code=status.HTTP_303_SEE_OTHER)
