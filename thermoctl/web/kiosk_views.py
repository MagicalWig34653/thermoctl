"""The kiosk dashboard: a wall tablet's view of the plant.

`GET /kiosk/{token}` is meant to be opened exactly once, from the address an admin
handed out on `/kiosk-tokens` -- the tablet bookmarks it. From then on the plaintext
lives only in a cookie (`thermoctl/auth/kiosk.py`), and every further visit goes
through the bare `/kiosk`, which resolves that cookie instead. This is also why the
entry route redirects rather than rendering directly: a bookmark that always redirects
to the cookie-backed page never needs to carry the token in a `Referer` header either.

Every read and every write below goes through the same domain functions and the same
`Principal`/`visible_zones`/`has_permission` machinery as the logged-in UI
(`thermoctl/web/start_views.py`, `thermoctl/web/daily_views.py`) -- a kiosk token is
just a `Principal` with a narrower `grants` set, not a parallel access path.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.kiosk import (
    KIOSK_COOKIE_NAME,
    KIOSK_CSRF_COOKIE_NAME,
    kiosk_csrf_protection,
    kiosk_token_from_cookie,
)
from thermoctl.auth.tokens import resolve_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, principal_for_token, require, visible_zones
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import RemoteControlError, set_setpoint
from thermoctl.domain.remote_control import boost as domain_boost
from thermoctl.domain.schedule import resolved_setpoint
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


def _kiosk_csrf(request: Request) -> str:
    """The CSRF token belonging to this tablet's kiosk cookie.

    Taken from the cookie the browser already sent rather than recomputed from the
    plaintext: this way the page cannot hand out a token for a different credential
    than the one the request actually carries.
    """
    plaintext = request.cookies.get(KIOSK_COOKIE_NAME)
    if plaintext is None:  # pragma: no cover - _dashboard only runs with a valid cookie
        return ""
    return csrf_token(plaintext, get_settings().secret_key.get_secret_value())


def _invalid_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "kiosk_invalid.html", {}, status_code=status.HTTP_401_UNAUTHORIZED
    )


def _zone_or_none(session: Session, principal: Principal, zone_id: int) -> Zone | None:
    """The zone if this token may see it -- `None` for anything else, unfindable.

    Deliberately not a 403: a zone this token has no `zone.read` grant for should
    look exactly like a zone that does not exist. `visible_zones` is the same
    function every other adapter uses to decide what is even visible.
    """
    return next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id), None
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
    response = RedirectResponse(prefixed(request, "/kiosk"), status_code=status.HTTP_303_SEE_OTHER)
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


def _dashboard(
    request: Request, session: Session, principal: Principal, *,
    error: str | None = None, error_zone_id: str | None = None,
) -> Response:
    zones = visible_zones(session, principal, "zone.read")
    zone_ids = [zone.id for zone in zones]
    now = utcnow()
    settings = session.get(Setting, 1)

    zustaende = {
        zone_id: (state, sensorstatus)
        for zone_id, state, sensorstatus in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_(zone_ids))
        )
    }
    would_heat_je_zone: dict[int, bool] = {}
    for decision in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        would_heat_je_zone.setdefault(decision.zone_id, decision.would_heat)

    return templates.TemplateResponse(
        request,
        "kiosk.html",
        {
            "now": now,
            "timezone": settings.timezone if settings is not None else None,
            "zones": zones,
            "zustaende": zustaende,
            "setpoints": {zone.id: resolved_setpoint(session, zone, now) for zone in zones},
            "would_heat_je_zone": would_heat_je_zone,
            "control_zone_ids": {
                zone.id for zone in zones if has_permission(principal, "setpoint.write", zone.id)
            },
            "error": error,
            "error_zone_id": error_zone_id,
            # Goes into the forms as a hidden field. The buttons are plain HTML and
            # send no header of their own -- see `kiosk_csrf_protection`.
            "csrf": _kiosk_csrf(request),
        },
    )


@router.get("/kiosk")
async def kiosk_dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    token = kiosk_token_from_cookie(request, session)
    if token is None:
        return _invalid_page(request)
    principal = principal_for_token(session, token)
    return _dashboard(
        request, session, principal,
        error=request.query_params.get("error"),
        error_zone_id=request.query_params.get("zone_id"),
    )


# The same step used by the start page's thermostat (`daily_views.py`) -- one click
# should be one perceptible change, not a fraction of a degree nobody notices and not
# a jump so coarse that reaching a target takes forever.
THERMOSTAT_STEP = Decimal("0.5")


@router.post("/kiosk/zones/{zone_id}/setpoint")
async def kiosk_adjust_setpoint(
    zone_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    direction: Annotated[str, Form()] = "",
) -> Response:
    token = kiosk_token_from_cookie(request, session)
    if token is None:
        return _invalid_page(request)
    principal = principal_for_token(session, token)
    zone = _zone_or_none(session, principal, zone_id)
    if zone is None:
        return _invalid_page(request)
    require(principal, "setpoint.write", zone.id)

    if direction not in ("up", "down"):
        return _dashboard(
            request, session, principal,
            error="Unbekannte Richtung.", error_zone_id=str(zone_id),
        )

    now = utcnow()
    current = resolved_setpoint(session, zone, now).temperature_c
    updated = current + (THERMOSTAT_STEP if direction == "up" else -THERMOSTAT_STEP)
    try:
        set_setpoint(session, zone, updated, now, token_id=principal.token_id, source="kiosk")
    except DomainError as exc:
        query = urlencode({"error": exc.notice, "zone_id": zone_id})
        return RedirectResponse(
            prefixed(request, f"/kiosk?{query}"), status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(prefixed(request, "/kiosk"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/kiosk/zones/{zone_id}/boost")
async def kiosk_boost(
    zone_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    token = kiosk_token_from_cookie(request, session)
    if token is None:
        return _invalid_page(request)
    principal = principal_for_token(session, token)
    zone = _zone_or_none(session, principal, zone_id)
    if zone is None:
        return _invalid_page(request)
    require(principal, "override.create", zone.id)

    try:
        domain_boost(session, zone, utcnow(), token_id=principal.token_id, source="kiosk")
    except RemoteControlError as exc:
        query = urlencode({"error": str(exc), "zone_id": zone_id})
        return RedirectResponse(
            prefixed(request, f"/kiosk?{query}"), status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(prefixed(request, "/kiosk"), status_code=status.HTTP_303_SEE_OTHER)
