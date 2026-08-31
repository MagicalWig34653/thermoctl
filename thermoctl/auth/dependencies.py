from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_FIELD_NAME, CSRF_HEADER, check_csrf
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


def current_principal(
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

# The two routes that lead *out* of a stale page. They must never be the ones that a
# stale token blocks -- otherwise a browser tab that sat open too long is in a dead
# end: every form is refused, logging out is refused, and logging in again is refused
# too as long as the old session cookie is still around. Reported from use, and the
# only escape was deleting exactly the right cookie by hand.
_RECOVERY_PATHS = frozenset({"/login", "/logout"})


class StalePage(Exception):
    """A browser sent a state-changing request carrying an outdated CSRF token.

    Not an ordinary 403: the request was not forbidden because the caller lacks a
    permission, but because the page it came from is older than the session it is
    talking to. The answer belongs to the person in front of the browser, so
    `app.py` turns this into something readable -- and on a recovery path into the
    way out.
    """

    def __init__(self, *, recovery: bool) -> None:
        super().__init__("Ungueltiges CSRF-Token")
        self.recovery = recovery


async def csrf_protection(request: Request) -> None:
    """Shared dependency for every state-changing route of the UI.

    If the request carries a session cookie, it must bring the matching token in the
    ``X-CSRF-Token`` header or a form field. The latter keeps ordinary HTML forms
    operable without JavaScript; a foreign origin can read neither value.

    Without a session cookie the protection does not apply: there is then nothing
    that a foreign origin could send along unnoticed. This covers the login itself
    and setup, which is secured via the one-time token, as well as the REST API,
    which only ever evaluates bearer tokens.

    A failed check does not raise a plain 403 but `StalePage` -- see there for why
    logging in and out have to survive an outdated token.

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
    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None:
        form = await request.form()
        value = form.get(CSRF_FIELD_NAME)
        submitted = value if isinstance(value, str) else None
    if not check_csrf(submitted, cookie_value, settings.secret_key.get_secret_value()):
        raise StalePage(recovery=request.url.path in _RECOVERY_PATHS)
