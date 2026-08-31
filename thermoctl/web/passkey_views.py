"""The HTTP side of the passkey ceremonies.

Thin: the rules live in `thermoctl/domain/passkey.py`. This just receives, passes
along, and rejects uniformly.

**Every failed login looks the same** — same status, same text. Whether a credential
id is unknown, an account is locked, or a signature is wrong is in the audit log. Not
otherwise, or the responses would reveal which accounts exist.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
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
from thermoctl.web import is_partial_swap, templates

router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

_REJECTED = "Die Anmeldung war nicht erfolgreich."


def _passkeys_an(settings: Settings) -> None:
    """Without a relying party id, these routes don't exist at all — not halfway."""
    if not settings.passkeys_available():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkeys sind nicht eingerichtet.")


def _reject() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"status": "rejected", "notice": _REJECTED},
    )


@router.post("/passkey/authentication/options")
async def authentication_options(
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Returns the arguments for `navigator.credentials.get()`."""
    settings = get_settings()
    _passkeys_an(settings)
    # Cleanup on the side: expired challenges are worthless but do pile up.
    cleanup_old_challenges(session)
    return JSONResponse(begin_authentication(session, settings))


@router.post("/passkey/authentication/verify")
async def finish_authentication(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Receives the assertion and logs in on success."""
    settings = get_settings()
    _passkeys_an(settings)
    try:
        response: dict[str, Any] = await request.json()
    except Exception:
        return _reject()
    if not isinstance(response, dict):
        return _reject()

    try:
        user = verify_authentication(session, settings, response)
    except PasskeyError:
        # The reason is already in the log; it does not go out to the caller.
        return _reject()

    user.last_login_at = utcnow()
    lifetime_s = session_lifetime_s(session)
    _entry, secret = create_session(
        session, user, lifetime_s,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client is not None else None,
    )
    result = JSONResponse({"status": "signed_in", "redirect": "/"})
    result.set_cookie(
        COOKIE_NAME, secret, max_age=lifetime_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    result.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(secret, settings.secret_key.get_secret_value()),
        max_age=lifetime_s, httponly=False, samesite="lax",
        secure=settings.secure_cookies,
    )
    return result


def _own_user(session: Session, principal: Principal) -> User:
    user = None if principal.user_id is None else session.get(User, principal.user_id)
    if user is None:  # pragma: no cover
        # A principal without a user is an API token, and an API token never reaches
        # these routes: they hang on the web router, which authenticates by session
        # cookie. The guard stays because "belongs to a person" is the assumption the
        # rest of this function rests on, and it should say so rather than imply it.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return user


@router.get("/passkeys")
async def passkey_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """One's own passkeys. Nobody sees someone else's here — there's no route there."""
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
            "available": get_settings().passkeys_available(),
            "is_htmx": is_partial_swap(request),
        },
    )


@router.post("/passkey/registration/options")
async def registration_arguments(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    user = _own_user(session, principal)
    return JSONResponse(begin_registration(session, settings, user))


@router.post("/passkey/registration/verify")
async def save_registration(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()
    _passkeys_an(settings)
    user = _own_user(session, principal)
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unlesbare Antwort") from None

    label = str(payload.pop("label", "") or "")
    try:
        entry = finish_registration(
            session, settings, user, payload, label
        )
    except PasskeyError as exc:
        # The reason is allowed to go out here: the caller is logged in and is
        # registering their own key — an incomprehensible rejection would only be a
        # nuisance here, without protecting anything.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "rejected", "notice": str(exc)},
        )
    return JSONResponse({"status": "saved", "label": entry.label})


@router.post("/passkeys/{passkey_id}/remove")
async def delete_passkey(
    passkey_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    from fastapi.responses import RedirectResponse

    user = _own_user(session, principal)
    entry = session.get(UserPasskey, passkey_id)
    # Someone else's passkey is unfindable, not forbidden — otherwise the response
    # would reveal which ids exist.
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey nicht gefunden")
    remove_passkey(session, user, entry)
    return RedirectResponse("/passkeys", status_code=status.HTTP_303_SEE_OTHER)
