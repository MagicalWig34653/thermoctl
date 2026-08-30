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
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_anlegen, sitzungslebensdauer_s
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.domain.passkey import (
    PasskeyFehler,
    alte_challenges_aufraeumen,
    anmeldung_beginnen,
    anmeldung_pruefen,
    passkey_entfernen,
    registrierung_abschliessen,
    registrierung_beginnen,
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


@router.post("/passkey/anmeldung/argumente")
async def anmeldung_argumente(
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Liefert die Argumente fuer `navigator.credentials.get()`."""
    settings = get_settings()
    _passkeys_an(settings)
    # Nebenbei aufraeumen: abgelaufene Challenges sind wertlos, sammeln sich aber an.
    alte_challenges_aufraeumen(session)
    return JSONResponse(anmeldung_beginnen(session, settings))


@router.post("/passkey/anmeldung/pruefen")
async def anmeldung_abschliessen(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Nimmt die Assertion entgegen und meldet bei Erfolg an."""
    settings = get_settings()
    _passkeys_an(settings)
    try:
        antwort: dict[str, Any] = await request.json()
    except Exception:
        return _ablehnen()
    if not isinstance(antwort, dict):
        return _ablehnen()

    try:
        benutzer = anmeldung_pruefen(session, settings, antwort)
    except PasskeyFehler:
        # Der Grund steht bereits im Protokoll; nach aussen geht er nicht.
        return _ablehnen()

    benutzer.last_login_at = utcnow()
    lebensdauer_s = sitzungslebensdauer_s(session)
    _eintrag, geheimnis = sitzung_anlegen(
        session, benutzer, lebensdauer_s,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    ergebnis = JSONResponse({"status": "angemeldet", "weiter": "/"})
    ergebnis.set_cookie(
        COOKIE_NAME, geheimnis, max_age=lebensdauer_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    ergebnis.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(geheimnis, settings.secret_key.get_secret_value()),
        max_age=lebensdauer_s, httponly=False, samesite="lax",
        secure=settings.secure_cookies,
    )
    return ergebnis


def _eigener_benutzer(session: Session, principal: Principal) -> User:
    benutzer = None if principal.user_id is None else session.get(User, principal.user_id)
    if benutzer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return benutzer


@router.get("/passkeys")
async def passkey_liste(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Die eigenen Passkeys. Fremde sieht hier niemand — es gibt keinen Weg dorthin."""
    benutzer = _eigener_benutzer(session, principal)
    return templates.TemplateResponse(
        request,
        "passkeys.html",
        {
            "passkeys": session.scalars(
                select(UserPasskey)
                .where(UserPasskey.user_id == benutzer.id)
                .order_by(UserPasskey.created_at)
            ).all(),
            "moeglich": get_settings().passkeys_moeglich(),
            "ist_htmx": ist_teilaustausch(request),
        },
    )


@router.post("/passkey/registrierung/argumente")
async def registrierung_argumente(
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    benutzer = _eigener_benutzer(session, principal)
    return JSONResponse(registrierung_beginnen(session, settings, benutzer))


@router.post("/passkey/registrierung/pruefen")
async def registrierung_speichern(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    benutzer = _eigener_benutzer(session, principal)
    try:
        nutzlast: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unlesbare Antwort") from None

    bezeichnung = str(nutzlast.pop("bezeichnung", "") or "")
    try:
        eintrag = registrierung_abschliessen(
            session, settings, benutzer, nutzlast, bezeichnung
        )
    except PasskeyFehler as exc:
        # Hier darf der Grund nach aussen: Der Aufrufer ist angemeldet und registriert
        # seinen eigenen Schluessel — eine unverstaendliche Ablehnung waere hier nur
        # hinderlich, ohne irgendetwas zu schuetzen.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "abgelehnt", "meldung": str(exc)},
        )
    return JSONResponse({"status": "gespeichert", "bezeichnung": eintrag.bezeichnung})


@router.post("/passkeys/{passkey_id}/entfernen")
async def passkey_loeschen(
    passkey_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    from fastapi.responses import RedirectResponse

    benutzer = _eigener_benutzer(session, principal)
    eintrag = session.get(UserPasskey, passkey_id)
    # Ein fremder Passkey ist nicht auffindbar, nicht verboten — sonst verriete die
    # Antwort, welche Kennungen es gibt.
    if eintrag is None or eintrag.user_id != benutzer.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey nicht gefunden")
    passkey_entfernen(session, benutzer, eintrag)
    return RedirectResponse("/passkeys", status_code=status.HTTP_303_SEE_OTHER)
