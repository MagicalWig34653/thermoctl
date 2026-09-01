"""Two pages that operate on the same settings row -- and still belong apart.

`/steuerung` is **operations**: whether the persisted and startup-built latches are open,
what control is currently deciding, and the button for the persisted latch. This is what
you look at when something's wrong.

`/einstellungen` are the **control defaults**: hysteresis, minimum switch durations,
cycle time, retention, timezone. You set these once and then not again for years.

At first both lived on one page. That was convenient to build and wrong to use:
whoever wanted to check whether the plant is armed scrolled past nine number fields
that didn't interest them at that moment -- and whoever wanted to change a default
landed on the arm button first.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.domain.authz import has_permission, require, visible_zones
from thermoctl.domain.control import (
    GANZZAHLIG,
    LABELS,
    LIMITS,
    ControlError,
    arm,
    check_coordinate,
    save_settings,
    save_solar_location,
    settings,
)
from thermoctl.domain.interfaces import overview
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.statistics import as_duration, heating_periods
from thermoctl.web import templates

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


def _page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    errors: ControlError | None = None,
) -> Response:
    row = settings(session)
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()

    states = {
        zone_id: (state, status)
        for zone_id, state, status in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    decisions: dict[int, ShadowDecision] = {}
    for decision in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_([zone.id for zone in zones]))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        decisions.setdefault(decision.zone_id, decision)

    return templates.TemplateResponse(
        request,
        "control.html",
        {
            "settings": row,
            "zones": zones,
            "states": states,
            "decisions": decisions,
            "setpoints": {
                zone.id: resolved_setpoint(session, zone, now) for zone in zones
            },
            "errors": {errors.field: errors.notice} if errors else {},
            "may_arm": has_permission(principal, "control.arm"),
            # The first bolt sits in the MQTT client's constructor and is read from
            # the database at startup. Whoever arms the plant while it is running
            # ends up with a state where the plant decides while armed and still
            # sends nothing. That is intentional -- but it has to show up here,
            # otherwise someone spends hours hunting for the bug.
            "sending_allowed": getattr(request.app.state, "sending_allowed", False),
        },
    )


def _defaults_page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    solar_enabled: bool | None = None,
    errors: ControlError | None = None,
) -> Response:
    row = settings(session)
    if values is None:
        values = {field: str(getattr(row, field)) for field in LIMITS}
        values["timezone"] = row.timezone
        values["solar_forecast_latitude"] = (
            str(row.solar_forecast_latitude) if row.solar_forecast_latitude is not None else ""
        )
        values["solar_forecast_longitude"] = (
            str(row.solar_forecast_longitude) if row.solar_forecast_longitude is not None else ""
        )
        solar_enabled = row.solar_forecast_enabled
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "fields": [(field, LABELS[field], field in GANZZAHLIG) for field in LIMITS],
            "values": values,
            "solar_forecast_enabled": bool(solar_enabled),
            "errors": {errors.field: errors.notice} if errors else {},
            "may_edit": has_permission(principal, "setting.manage"),
        },
    )


@router.get("/control")
async def show_control(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Whoever may see the plant may read this. The operating state is the answer to
    # "is this thing actually switching right now?" -- nobody should have to guess it.
    require(principal, "zone.read")
    return _page(request, session, principal)


@router.get("/settings")
async def show_settings(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.read")
    return _defaults_page(request, session, principal)


@router.post("/settings")
async def save_defaults(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "setting.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in (*LIMITS, "timezone", "solar_forecast_latitude", "solar_forecast_longitude")
    }
    # Eine nicht angehakte Checkbox schickt gar nichts -- also zaehlt allein, ob
    # ueberhaupt ein Wert ankam. Vorher stand hier `== "on"`, der Vorgabewert eines
    # Browsers fuer eine Checkbox ohne `value`. Seit das Makro `value="yes"` setzt,
    # traf das nie mehr zu: Die Sonnenabsenkung liess sich nicht mehr einschalten.
    solar_enabled = form.get("solar_forecast_enabled") is not None
    try:
        # Validated -- but deliberately not yet written: `save_settings` below still
        # has to run its own check before either group's fields actually change, so
        # a bad coordinate here must not leave the (already checked) global defaults
        # committed on their own. Only `settings.manage` can trigger any of this, and
        # the request only ever commits at the very end (`get_session`) -- but that
        # commits whatever is on the row by then, checked or not.
        check_coordinate(
            "solar_forecast_latitude", values["solar_forecast_latitude"], bound=Decimal("90")
        )
        check_coordinate(
            "solar_forecast_longitude", values["solar_forecast_longitude"], bound=Decimal("180")
        )
        save_settings(
            session,
            values,
            values["timezone"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
        save_solar_location(
            session,
            enabled=solar_enabled,
            latitude_text=values["solar_forecast_latitude"],
            longitude_text=values["solar_forecast_longitude"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _defaults_page(
            request, session, principal, values=values, solar_enabled=solar_enabled, errors=exc
        )
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/control/arm")
async def arm_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Its own permission, not `setting.manage`: this changes the first safety latch.
    require(principal, "control.arm")
    form = await request.form()
    armed = str(form.get("armed", "")) == "yes"
    try:
        arm(
            session,
            armed,
            reason=str(form.get("reason", "")),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _page(request, session, principal, errors=exc)
    return RedirectResponse("/control", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/interfaces")
async def show_interfaces(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """What is connected from outside -- and whether it's really running.

    `setting.manage`, not `zone.read`: the page names broker addresses, webhook
    targets, and account names. None of that is a secret in the strict sense, but it's
    also nothing every operator of the heating needs to see.
    """
    require(principal, "setting.manage")
    return templates.TemplateResponse(
        request,
        "interfaces.html",
        {
            "interfaces": overview(
                session,
                get_settings(),
                getattr(request.app.state, "bridge_reachable", None),
            ),
        },
    )


# Time ranges people actually want to know about. No free-form date field: the
# question is "this week" or "this month", not "from the 14th to the 23rd".
ZEITRAEUME: dict[str, tuple[str, int]] = {
    "7": ("7 Tage", 7),
    "30": ("30 Tage", 30),
    "90": ("90 Tage", 90),
}


@router.get("/statistics")
async def show_statistics(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """When and for how long there was heating, per zone and day.

    In the dry run this is a statement about what thermoctl *would have* heated -- the
    page says so too, instead of just putting up a number that could be mistaken for
    the plant's actual history.
    """
    require(principal, "zone.read")
    zones = visible_zones(session, principal, "zone.read")
    row = settings(session)

    key = request.query_params.get("period", "7")
    if key not in ZEITRAEUME:
        key = "7"
    _label, days = ZEITRAEUME[key]

    bis = utcnow()
    start_at = (bis - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    values = heating_periods(
        session,
        [zone.id for zone in zones],
        start_at,
        bis,
        cycle_seconds=row.shadow_interval_seconds,
    )
    # The single longest day value determines the height of the bars. Scaling per
    # zone would be more comfortable to read and would falsify the comparison
    # between zones -- and that comparison is exactly why the zones are stacked.
    maximum = max(
        (t.seconds for stat in values.values() for t in stat.days), default=0
    )
    return templates.TemplateResponse(
        request,
        "statistics.html",
        {
            "zones": zones,
            "values": values,
            "maximum": maximum,
            "periods": [(s, b) for s, (b, _t) in ZEITRAEUME.items()],
            "period": key,
            "armed": row.control_armed,
            "as_duration": as_duration,
        },
    )
