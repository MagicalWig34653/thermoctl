"""Die Einlösung eines Kiosk-Tokens: `GET /kiosk/{token}`.

Die Adresse wird genau einmal geöffnet -- von dem Link, den ein Verwalter unter
„Kiosk-Tokens“ ausgegeben hat; das Wandtablett merkt sie sich als Lesezeichen. Danach
liegt der Klartext nur noch in einem Cookie (`thermoctl/auth/kiosk.py`), und die
Kioskansicht der Oberfläche (`/app/kiosk`) liest ihre Daten über `/kiosk-api` mit
diesem Cookie. Deshalb leitet die Einlösung um, statt selbst etwas anzuzeigen: Ein
Lesezeichen, das immer weiterleitet, trägt das Token auch nie in einem `Referer`.
Ein ungültiges Token bekommt die schlichte Hinweisseite `kiosk_invalid.html`.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.kiosk import (
    KIOSK_COOKIE_NAME,
    KIOSK_CSRF_COOKIE_NAME,
    kiosk_csrf_protection,
)
from thermoctl.auth.tokens import resolve_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.web import templates
from thermoctl.web.urls import cookie_path, prefixed

router = APIRouter(dependencies=[Depends(kiosk_csrf_protection)], include_in_schema=False)

# A year: long enough that nobody re-scans a QR code every few weeks, short enough
# that a forgotten, still-bookmarked tablet does not outlive every other credential in
# the project. Capped below by the token's own `expires_at`, if it has one.
_KIOSK_COOKIE_MAX_AGE_S = 60 * 60 * 24 * 365

def _kiosk_cookie_max_age_s(expires_at: datetime | None) -> int:
    if expires_at is None:
        return _KIOSK_COOKIE_MAX_AGE_S
    remaining = int((expires_at - utcnow()).total_seconds())
    return max(remaining, 0)


def _invalid_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "kiosk_invalid.html", {}, status_code=status.HTTP_401_UNAUTHORIZED
    )


@router.get("/kiosk/{plaintext}")
async def kiosk_entry(
    plaintext: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Redeems a kiosk token once, sets its cookie, then redirects to the bare page.

    The redirect matters, not just tidiness: it is what keeps the token out of the
    address bar and out of a `Referer` header on every visit after the first. It does
    **not** keep it out of the very first request line -- that is closed separately,
    in `thermoctl/logging.py`, by redacting `/kiosk/<token>` from the access log
    before it is ever formatted.
    """
    token = resolve_token(session, plaintext)
    if token is None or not token.is_kiosk:
        return _invalid_page(request)

    settings = get_settings()
    max_age = _kiosk_cookie_max_age_s(token.expires_at)
    # The redemption stays here (it sets the cookie); the page it lands on is the Svelte one.
    response = RedirectResponse(
        prefixed(request, "/app/kiosk"), status_code=status.HTTP_303_SEE_OTHER
    )
    response.set_cookie(
        KIOSK_COOKIE_NAME, plaintext, max_age=max_age,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
        path=cookie_path(request),
    )
    response.set_cookie(
        KIOSK_CSRF_COOKIE_NAME, csrf_token(plaintext, settings.secret_key.get_secret_value()),
        max_age=max_age, httponly=False, samesite="lax", secure=settings.secure_cookies,
        path=cookie_path(request),
    )
    return response
