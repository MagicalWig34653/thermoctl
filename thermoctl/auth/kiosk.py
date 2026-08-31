"""Cookie handling for the kiosk dashboard.

A kiosk token travels in a URL once (`/kiosk/{token}`, meant to become a tablet's
bookmark) and from then on in a cookie, exactly like a session secret does for a
logged-in user (see `thermoctl/auth/sessions.py`) -- the cookie is what keeps the
plaintext out of the address bar and the request log on every visit after the first.

CSRF protection reuses the same HMAC scheme as the logged-in UI
(`thermoctl/auth/csrf.py`), just bound to the kiosk cookie's secret instead of the
session's: a foreign origin that gets a browser to submit a form still cannot produce
a matching header, because it never sees the cookie value it would have to sign.
"""

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_FIELD_NAME, CSRF_HEADER, check_csrf
from thermoctl.auth.tokens import resolve_token
from thermoctl.config import get_settings
from thermoctl.db.models.credential import ApiToken

KIOSK_COOKIE_NAME = "thermoctl_kiosk"
# Not httpOnly, for the same reason as `thermoctl_csrf`: htmx needs to read it to send
# the header along, and the value itself hands out no more than the session's CSRF
# cookie does -- it only proves the request came from whoever already holds the
# kiosk cookie.
KIOSK_CSRF_COOKIE_NAME = "thermoctl_kiosk_csrf"

# Same set as `csrf_protection` in thermoctl/auth/dependencies.py: methods that change
# nothing need no proof of origin.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def kiosk_token_from_cookie(request: Request, session: Session) -> ApiToken | None:
    """Resolves the kiosk cookie to a token -- `None` for anything not usable as one.

    Deliberately also rejects an otherwise valid, non-revoked, non-expired
    `ApiToken` that was never issued as a kiosk token: a developer's API token
    pasted into this cookie by hand would otherwise work here too, and a bearer
    token's threat model (kept in a script, sent in a header) is not the one a
    cookie sitting in a tablet's browser storage has.
    """
    cookie_value = request.cookies.get(KIOSK_COOKIE_NAME)
    if cookie_value is None:
        return None
    token = resolve_token(session, cookie_value)
    if token is None or not token.is_kiosk:
        return None
    return token


async def kiosk_csrf_protection(request: Request) -> None:
    """Same shape as `csrf_protection`, bound to the kiosk cookie instead of the session.

    Hung on the kiosk router's mutating routes: without a kiosk cookie there is
    nothing to forge (the request fails the token check right after anyway), and a
    safe method changes nothing regardless of origin.

    **Accepts the token from a form field as well as from the header**, and that is
    the difference to the logged-in interface. There, every form goes out through
    hx-boost, which sets the header; here the buttons are plain HTML forms. They
    posted without any header and were answered with "Ungueltiges CSRF-Token" -- the
    dashboard displayed correctly, refreshed itself correctly, and neither of its two
    buttons did anything.

    A hidden field instead of hanging hx-boost on them: a device on the wall should
    not need working JavaScript to turn the heating up. The token is the same value
    either way, and it is not weaker in a field -- a foreign origin still cannot read
    the cookie it would have to be derived from.
    """
    if request.method in _SAFE_METHODS:
        return
    cookie_value = request.cookies.get(KIOSK_COOKIE_NAME)
    if cookie_value is None:
        return
    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None:
        # Starlette caches the parsed form on the request, so the route reads the very
        # same object afterwards -- this does not consume the body.
        form = await request.form()
        value = form.get(CSRF_FIELD_NAME)
        submitted = value if isinstance(value, str) else None
    settings = get_settings()
    if not check_csrf(submitted, cookie_value, settings.secret_key.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Ungueltiges CSRF-Token"
        )
