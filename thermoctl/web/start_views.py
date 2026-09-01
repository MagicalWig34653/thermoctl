"""The start page: a status board, not a collection of metrics.

It answers exactly one question -- *is the house currently doing what I told it to?*
-- for all zones at once. Anything that doesn't answer this question belongs
elsewhere.

There used to be two count tiles here: the number of zones and the number of
**users**. How many accounts exist says nothing about a heating system; the number was
there because it was easy to compute. Both are gone.

Unlike the protected administration pages, this page does not respond to a
non-logged-in visitor with 401, but redirects to the login: whoever types the
service's address into a browser should see a login form, not an error message.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_protection, get_session
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint
from thermoctl.domain.authz import has_permission, principal_for_user, visible_zones
from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C
from thermoctl.domain.schedule import resolved_setpoint, week_segments
from thermoctl.domain.time import local_time
from thermoctl.setup import setup_needed
from thermoctl.web import templates, warmth_fraction

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


MINUTES_PER_DAY = 1440


def _day_track(
    session: Session, zone_ids: list[int], weekday: int
) -> dict[int, list[dict[str, object]]]:
    """Today's schedule per zone as segments with share, time, and setpoint.

    The same decomposition as the week view (`wochenabschnitte`), just restricted to
    one day -- a second version of the same logic in the browser would be exactly what
    principle 6 forbids.
    """
    if not zone_ids:
        return {}
    modes = {m.id: m for m in session.scalars(select(SetpointMode))}
    names = {identifier: mode.name for identifier, mode in modes.items()}
    temperatures: dict[tuple[int, int], Decimal] = {
        (zone_id, mode_id): temperature
        for zone_id, mode_id, temperature in session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id.in_(zone_ids))
        )
    }
    points_per_zone: dict[int, list[SchedulePoint]] = {zone_id: [] for zone_id in zone_ids}
    for point in session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id.in_(zone_ids))
    ):
        points_per_zone[point.zone_id].append(point)

    tracks: dict[int, list[dict[str, object]]] = {}
    for zone_id, points in points_per_zone.items():
        segments = [
            a for a in week_segments(points, names) if a.weekday == weekday
        ]
        tracks[zone_id] = [
            {
                "start": segment.start_minute,
                "width": (segment.end_minute - segment.start_minute)
                * 100
                / MINUTES_PER_DAY,
                "left": segment.start_minute * 100 / MINUTES_PER_DAY,
                "mode_name": segment.mode_name,
                "temperature": temperatures.get((zone_id, segment.mode_id)),
                "warmth": warmth_fraction(temperatures.get((zone_id, segment.mode_id))),
            }
            for segment in segments
        ]
    return tracks


@router.get("/")
def start(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Before login: as long as there isn't a single user, nobody can log in. A login
    # form would then be a dead end -- whoever types the service's address is here for
    # setup. That the redirect reveals the empty state is no gain for an attacker:
    # /setup responds visibly differently anyway after setup is complete, and setup
    # itself depends on the one-time token from the log, not on the page's
    # reachability.
    if setup_needed(session):
        return RedirectResponse("/setup", status_code=303)

    cookie_value = request.cookies.get(COOKIE_NAME)
    http_session = resolve_session(session, cookie_value) if cookie_value else None
    user = session.get(User, http_session.user_id) if http_session else None
    if user is None or not user.is_active:
        return RedirectResponse("/login", status_code=303)

    request.state.user = user
    principal = principal_for_user(session, user)
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()
    settings = session.get(Setting, 1)
    local_now = local_time(now, settings.timezone if settings is not None else None)
    states = {
        zone_id: (state, sensor_status_of)
        for zone_id, state, sensor_status_of in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    zone_ids = [zone.id for zone in zones]
    overrides: dict[int, ZoneOverride] = {}
    for entry in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > now),
        )
        .order_by(ZoneOverride.created_at.desc())
    ):
        overrides.setdefault(entry.zone_id, entry)
    decisions: dict[int, ShadowDecision] = {}
    for decision in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        decisions.setdefault(decision.zone_id, decision)

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "user": user,
            "zones": zones,
            "states": states,
            "setpoints": {
                zone.id: resolved_setpoint(session, zone, now) for zone in zones
            },
            "overrides": overrides,
            "decisions": decisions,
            "may_override": {
                zone.id
                for zone in zones
                if has_permission(principal, "override.create", zone.id)
            },
            "may_cancel": {
                zone.id
                for zone in zones
                if has_permission(principal, "override.cancel", zone.id)
            },
            "may_edit_setpoint": {
                zone.id
                for zone in zones
                if has_permission(principal, "setpoint.write", zone.id)
            },
            "thermostat_errors": request.query_params.get("thermostat_errors"),
            # From the domain: a `min="5"` in the markup would be a second version of
            # the limit and would fall behind on the next change.
            "minimum_temperature": MINIMUM_TEMPERATURE_C,
            "maximum_temperature": MAXIMUM_TEMPERATURE_C,
            # The display name, not the code: the thermostat used to show "frostschutz"
            # instead of "Frostschutz" -- a database identifier that has no business
            # showing up there.
            "mode_names": {
                identifier: name
                for identifier, name in session.execute(
                    select(SetpointMode.id, SetpointMode.name)
                )
            },
            "may_edit_parameters": {
                zone.id for zone in zones if has_permission(principal, "zone.manage", zone.id)
            },
            "override_errors": request.query_params.get("override_errors"),
            "error_zone_id": request.query_params.get("zone_id"),
            "override_values": request.query_params,
            # The plant in one sentence: are both latches open, is the bridge running,
            # and are there sensors that stay silent? Exactly the three things that make
            # a display untrustworthy if you don't know them.
            "armed": bool(settings and settings.control_armed),
            "sending_allowed": getattr(request.app.state, "sending_allowed", False),
            "bridge": getattr(request.app.state, "bridge_reachable", None),
            "silent_sensors": [
                zone.display_name
                for zone in zones
                if zone.id in states and states[zone.id][1].code != "ok"
            ],
            "day_tracks": _day_track(
                session, zone_ids, local_now.isoweekday()
            ),
            "now_fraction": (
                local_now.hour * 60 + local_now.minute
            ) * 100 / MINUTES_PER_DAY,
        },
    )
