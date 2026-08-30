from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_HEADER, check_csrf
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.config import get_settings
from thermoctl.db.models.identity import User
from thermoctl.domain.authz import principal_for_user
from thermoctl.domain.principal import Principal

_NICHT_ANGEMELDET = "Nicht angemeldet"


def get_session(request: Request) -> Iterator[Session]:
    """Provides a database session for the duration of the request.

    Committed if the request runs through without error, otherwise rolled back. The
    session factory sits on ``app.state`` — ``create_app()`` sets it up on startup.
    """
    factory = request.app.state.session_factory
    http_session = factory()
    try:
        yield http_session
        http_session.commit()
    except Exception:
        http_session.rollback()
        raise
    finally:
        http_session.close()


def aktueller_principal(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Principal:
    """FastAPI dependency for protected routes: resolves the session cookie.

    If the cookie is missing, unknown, expired or revoked, or the associated user is
    inactive, this responds uniformly with 401 — the same response for every one of
    these cases, for the same reason as for the login itself.
    """
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    http_session = resolve_session(session, cookie_value)
    if http_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    user = session.get(User, http_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    # For the header bar, which should carry the logged-in name on every page (see
    # `_angemeldeter_benutzer` in thermoctl/web/__init__.py). Set here rather than in
    # every view: otherwise the bar carries the name only where some view happened to
    # think of it.
    request.state.user = user
    return principal_for_user(session, user)


# Safe methods change nothing and therefore need no CSRF proof. Without this
# exception, `csrf_schutz` could not be hung on an entire router — every ordinary
# page request from the browser sends the cookie, but no header.
_SICHERE_METHODEN = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def csrf_schutz(request: Request) -> None:
    """Shared dependency for every state-changing route of the UI.

    If the request carries a session cookie, it must bring the matching
    ``X-CSRF-Token`` header — even when none was sent at all. A protection that could
    be bypassed simply by omitting the header would be no protection at all.

    Without a session cookie the protection does not apply: there is then nothing
    that a foreign origin could send along unnoticed. This covers the login itself
    and setup, which is secured via the one-time token, as well as the REST API,
    which only ever evaluates bearer tokens.

    Hung as ``dependencies=[Depends(csrf_schutz)]`` on the router, not on the
    individual route: a route added later is thereby protected automatically,
    instead of relying on someone remembering to do it. `tests/test_csrf.py` checks
    this.
    """
    if request.method in _SICHERE_METHODEN:
        return
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is None:
        return
    settings = get_settings()
    if not check_csrf(
        request.headers.get(CSRF_HEADER), cookie_value, settings.secret_key.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Ungueltiges CSRF-Token"
        )
