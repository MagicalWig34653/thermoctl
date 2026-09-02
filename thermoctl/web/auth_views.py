import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import csrf_protection, get_session
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
from thermoctl.setup import setup_needed
from thermoctl.web import templates

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

# Failed attempts counted per username. Lives in process memory, not in the
# database: it's meant to slow down guessing, not persist. There is deliberately no
# account lockout — in a single-household system it would mostly be a convenient way
# to lock yourself out.
FEHLVERSUCHE: dict[str, int] = {}

_ERROR_MESSAGE = "Benutzername oder Passwort falsch."


# Generated once at load time, from randomness: only the computation time of
# `verify_password` is needed, never the corresponding password. Deliberately not a
# hardcoded value -- there's no hash in the repo, not even a meaningless one.
_VERGLEICHS_HASH = hash_password(secrets.token_urlsafe(32))


def sleep(seconds: float) -> None:
    """A dedicated function instead of a direct `time.sleep()` call, so tests can
    replace the delay via `monkeypatch.setattr` without actually waiting."""
    time.sleep(seconds)  # pragma: no cover - every test replaces exactly this line


@router.get("/login")
async def login_form(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Response:
    # See start_views.start(): without a single user, the form leads nowhere. GET
    # only -- a POST to /login without a user fails at the ordinary check anyway,
    # and that check should keep its identical error message, instead of letting the
    # redirect target reveal what the service knows.
    if setup_needed(session):
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"passkeys_available": get_settings().passkeys_available()}
    )


@router.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    settings = get_settings()

    previous_failures = FEHLVERSUCHE.get(username, 0)
    sleep(min(2**previous_failures, 5))

    user = session.scalar(select(User).where(User.username == username))
    # The password check ALWAYS runs, even for an unknown username -- then against a
    # throwaway hash. Otherwise Python's short-circuit evaluation would skip
    # `verify_password` for an unknown name, and the request would be measurably
    # faster than for an existing name: Argon2id is deliberately slow, and exactly
    # this computation time would reveal which accounts exist. The same message and
    # the same wait time alone are not enough for that.
    if user is None:
        verify_password(password, _VERGLEICHS_HASH)
        successful = False
    else:
        successful = user.is_active and verify_password(password, user.password_hash)

    if not successful:
        FEHLVERSUCHE[username] = previous_failures + 1
        # The same summary for existing and non-existing usernames alike — the audit
        # entry is allowed to reveal what happened, but the HTTP response to the
        # caller must not reveal whether the username exists.
        audit.record(
            session, source="web", action="login_failed", object_type="user",
            object_id=username, summary=f"Anmeldung als '{username}' fehlgeschlagen",
        )
        return templates.TemplateResponse(
            request, "login.html",
            {"errors": _ERROR_MESSAGE, "passkeys_available": settings.passkeys_available()},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    assert user is not None
    FEHLVERSUCHE[username] = 0
    user.last_login_at = utcnow()

    lifetime_s = session_lifetime_s(session)
    _entry, secret = create_session(
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
        COOKIE_NAME, secret, max_age=lifetime_s,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    # Not httpOnly: the interface (HTMX) reads the value and sends it along as an
    # `X-CSRF-Token` header.
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf_token(secret, settings.secret_key.get_secret_value()),
        max_age=lifetime_s, httponly=False, samesite="lax", secure=settings.secure_cookies,
    )
    return response


@router.post("/logout")
async def logout(request: Request, session: Annotated[Session, Depends(get_session)]) -> Response:
    # The CSRF proof is attached to the router (`csrf_schutz`) and has already been
    # provided by this point.
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is not None:
        http_session = resolve_session(session, cookie_value)
        if http_session is not None:
            revoke_session(session, http_session)

    # A boosted form follows ordinary redirects in the background and only swaps
    # the response body.  The login form would then appear while the address still
    # named the authenticated page.  Use htmx's navigation response for that path;
    # a non-htmx client receives the ordinary direct redirect to the same target.
    if request.headers.get("hx-request") is not None:
        response: Response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Redirect"] = "/login"
    else:
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return response
