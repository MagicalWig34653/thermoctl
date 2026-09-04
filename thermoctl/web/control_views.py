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

from thermoctl import audit
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
from thermoctl.domain.pi_control import (
    RESET_REASON_ARMING,
    RESET_REASON_CONTEXT_CHANGE,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_TIME_GAP,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.statistics import (
    RelayDeviceStatistics,
    as_duration,
    heating_periods,
    relay_operations,
)
from thermoctl.domain.time import local_day_start_utc, local_time
from thermoctl.integrations import notification
from thermoctl.services.shadow_run import PI_FALLBACK_INELIGIBLE
from thermoctl.web import templates
from thermoctl.web.urls import prefixed

# Readable text for `shadow_decision.controller_fallback_reason` (specification
# section 6: "einschliesslich Rueckfallgrund" has to be legible on the operating
# page, not just as a code). Kept here, not in the domain: this is a display-only
# translation, the same reasoning as `domain.control.LABELS`.
PI_FALLBACK_LABELS: dict[str, str] = {
    PI_FALLBACK_INELIGIBLE: "Die Zone erfüllt die PI-Voraussetzungen nicht (mehr).",
    RESET_REASON_WINDOW_OPEN: "Ein Fenster ist offen.",
    RESET_REASON_FROST: "Frostschutz ist wirksam.",
    RESET_REASON_SENSOR_FAILURE: "Der Sensor gilt als ausgefallen.",
    RESET_REASON_VALVE_PROTECTION: "Ein Ventilschutzlauf ist aktiv.",
    RESET_REASON_CONTEXT_CHANGE: "Der Sollwertkontext hat gerade gewechselt.",
    RESET_REASON_TIME_GAP: "Eine Zeitlücke seit der letzten Auswertung.",
    RESET_REASON_ARMING: "Die Anlage wurde gerade scharf geschaltet.",
    RESET_REASON_INVALID_STATE: "Der PI-Zustand war ungültig oder fehlte.",
}

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
            "pi_fallback_labels": PI_FALLBACK_LABELS,
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
    test_result: notification.WebhookTestResult | None = None,
    test_notice: str | None = None,
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
            "notify_sensor_faults": row.notify_sensor_faults,
            "notify_bridge_faults": row.notify_bridge_faults,
            "notify_command_failures": row.notify_command_failures,
            "notify_last_attempt_at": row.notify_last_attempt_at,
            "notify_last_ok": row.notify_last_ok,
            "notify_last_error": row.notify_last_error,
            "webhook_configured": get_settings().notify_webhook is not None,
            "test_result": test_result,
            "test_notice": test_notice,
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
    return RedirectResponse(prefixed(request, "/settings"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/notifications")
async def save_notification_preferences(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Which of the three fault-notice kinds go out at all -- plant-wide, no per-zone
    override. `setting.manage`, the same permission `/settings` and `/interfaces`
    already require: whoever may not see the webhook target should not be able to
    decide what gets sent to it either.

    No validation is possible on three checkboxes, unlike `save_defaults` above --
    there is nothing here that can be rejected, so unlike that route this one never
    re-renders the page with an error.
    """
    require(principal, "setting.manage")
    form = await request.form()
    row = settings(session)
    # A checkbox that isn't ticked sends nothing at all -- presence, not the value,
    # is what counts. Same reasoning as `solar_forecast_enabled` above.
    row.notify_sensor_faults = form.get("notify_sensor_faults") is not None
    row.notify_bridge_faults = form.get("notify_bridge_faults") is not None
    row.notify_command_failures = form.get("notify_command_failures") is not None
    audit.record(
        session,
        source="web",
        action="update",
        object_type="setting",
        object_id="1",
        summary="Einstellungen für Störungsmeldungen geändert",
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(prefixed(request, "/settings"), status_code=status.HTTP_303_SEE_OTHER)


# Below this, a repeated test button does nothing for a while rather than firing a
# second outbound call: whoever is troubleshooting a webhook can fix an address and
# try again in a moment, but nothing here should let an unattended tab hammer an
# external target. Enforced on the same row the result is shown from, not a separate
# store -- there is exactly one webhook target for the whole plant, so a plant-wide
# cooldown on the same field the operator is already looking at is enough.
_TEST_COOLDOWN = timedelta(seconds=10)


@router.post("/settings/notifications/test")
async def send_test_notification(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Sends a marked test notice over the real webhook path and shows what came back.

    `setting.manage`: this is the same permission that can already see the webhook
    address on `/interfaces` and change it via the environment -- nobody without
    that should be able to trigger an outbound call to it, repeatedly, on demand.

    Never redirects: a redirect would need the result carried in the URL or a
    session flash, and the result is exactly the thing this route exists to show
    immediately, on the same page, without a token or status text passing through
    the address bar or being kept around longer than this one response.
    """
    require(principal, "setting.manage")
    env_settings = get_settings()
    row = settings(session)
    result: notification.WebhookTestResult | None = None
    notice: str | None = None
    if env_settings.notify_webhook is None:
        notice = "Ohne hinterlegten Webhook gibt es nichts zu testen."
    else:
        last_attempt = row.notify_last_attempt_at
        now = utcnow()
        if last_attempt is not None and now - last_attempt < _TEST_COOLDOWN:
            notice = "Bitte kurz warten -- der letzte Versuch war gerade eben."
        else:
            result = await notification.send_test(env_settings)
            # `setattr` for the same reason as in `save_notification_preferences` above.
            row.notify_last_attempt_at = now
            row.notify_last_ok = result.ok
            row.notify_last_error = None if result.ok else result.error
    return _defaults_page(request, session, principal, test_result=result, test_notice=notice)


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
    return RedirectResponse(prefixed(request, "/control"), status_code=status.HTTP_303_SEE_OTHER)


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
    # The local day, not the UTC one: a period of "7 Tage" means seven local
    # calendar days including today, and its start is that first day's local
    # midnight -- converted to UTC only at the very end, so the query and
    # `heating_periods`' own bucketing agree on where a day begins.
    first_local_day = local_time(bis, row.timezone).date() - timedelta(days=days - 1)
    start_at = local_day_start_utc(first_local_day, row.timezone)
    values = heating_periods(
        session,
        [zone.id for zone in zones],
        start_at,
        bis,
        cycle_seconds=row.shadow_interval_seconds,
        timezone_name=row.timezone,
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


@router.get("/relay-wear")
async def show_relay_wear(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Relay operations derived from the actuator command log.

    This is deliberately separate from the heating-time page. Heating decisions are
    zone state and need ``zone.read``; command history is an audit trail and retains
    the existing plant-wide ``audit.read`` boundary. ``visible_zones`` additionally
    limits the aggregation to zones the principal may read, so an audit reader with
    only a zone-scoped ``zone.read`` grant cannot infer another zone's devices.
    """
    require(principal, "audit.read")
    zones = visible_zones(session, principal, "zone.read")
    row = settings(session)

    key = request.query_params.get("period", "7")
    if key not in ZEITRAEUME:
        key = "7"
    _label, days = ZEITRAEUME[key]
    until = utcnow()
    first_local_day = local_time(until, row.timezone).date() - timedelta(days=days - 1)
    start_at = local_day_start_utc(first_local_day, row.timezone)
    values = relay_operations(
        session,
        [zone.id for zone in zones],
        start_at,
        until,
        timezone_name=row.timezone,
        assumed_lifetime_operations=row.assumed_relay_lifetime_operations,
    )
    by_zone: dict[int, list[RelayDeviceStatistics]] = {zone.id: [] for zone in zones}
    for value in values:
        by_zone[value.zone_id].append(value)
    for device_values in by_zone.values():
        device_values.sort(key=lambda value: (-value.annual_projection, value.device_name))

    return templates.TemplateResponse(
        request,
        "relay_wear.html",
        {
            "zones": zones,
            "values": by_zone,
            "periods": [(period_key, label) for period_key, (label, _days) in ZEITRAEUME.items()],
            "period": key,
            "assumed_lifetime": row.assumed_relay_lifetime_operations,
            "has_values": bool(values),
        },
    )
