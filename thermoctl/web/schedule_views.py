from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ScheduleError,
    adopt_schedule,
    change_schedule_point_mode,
    create_schedule_point,
    delete_schedule_point,
    move_schedule_point,
    time_of_day_in_minutes,
    week_segments,
)
from thermoctl.web import templates, warmth_fraction

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

WEEKDAYS = (
    (1, "Montag"),
    (2, "Dienstag"),
    (3, "Mittwoch"),
    (4, "Donnerstag"),
    (5, "Freitag"),
    (6, "Samstag"),
    (7, "Sonntag"),
)


def _zone_or_404(
    session: Session, principal: Principal, zone_id: int, permission: str
) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, permission) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return zone


def _points(session: Session, zone_id: int) -> list[SchedulePoint]:
    return list(
        session.scalars(
            select(SchedulePoint)
            .where(SchedulePoint.zone_id == zone_id)
            .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
        )
    )


def _modes(session: Session) -> list[SetpointMode]:
    return list(
        session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name)
        )
    )


def _schedule_page(
    request: Request,
    session: Session,
    zone: Zone,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    errors: ScheduleError | None = None,
    move_error: str = "",
) -> Response:
    points = _points(session, zone.id)
    modes = _modes(session)
    segments = week_segments(points, {mode.id: mode.name for mode in modes})
    # The warmth per mode, so the week view speaks the same language as the start
    # page's day track: warmer means warmer. Without it, day and night would be two
    # identical-looking bars -- and the schedule would only show *that* it switches,
    # not to what.
    temperatures: dict[int, Decimal] = {
        mode_id: temperature
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
    warmth = {mode.id: warmth_fraction(temperatures.get(mode.id)) for mode in modes}
    by_day = {
        day: [segment for segment in segments if segment.weekday == day]
        for day, _name in WEEKDAYS
    }
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "zone": zone,
            "points": points,
            "modes": modes,
            "weekdays": WEEKDAYS,
            "segments": by_day,
            "warmth": warmth,
            "temperatures": temperatures,
            "values": values or {"weekday": "1", "time_of_day": "06:00", "mode_id": ""},
            "errors": {errors.field: errors.notice} if errors else {},
            # Its own channel, not `fehler`: a rejected move would otherwise show up
            # on the time field of the *creation* form -- both report "there's
            # already a point at this time", and both write into the same field. The
            # user saw a red message on a form they hadn't even touched, while the
            # bar that snapped back stayed uncommented. Noticed in the browser, by no
            # test.
            "move_error": move_error,
            "may_edit": has_permission(principal, "schedule.manage", zone.id),
            "csrf": csrf_token(
                request.cookies[COOKIE_NAME],
                get_settings().secret_key.get_secret_value(),
            ),
        },
    )


@router.get("/zones/{zone_id}/schedule")
async def show_schedule(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.read")
    return _schedule_page(request, session, zone, principal)


@router.post("/zones/{zone_id}/schedule/points")
async def create_schedule_point_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in ("weekday", "time_of_day", "mode_id")
    }
    try:
        try:
            weekday = int(values["weekday"])
        except ValueError as exc:
            raise ScheduleError(
                "weekday", "Bitte einen Wochentag auswählen."
            ) from exc
        minute = time_of_day_in_minutes(values["time_of_day"])
        try:
            mode_id = int(values["mode_id"])
        except ValueError as exc:
            raise ScheduleError("mode_id", "Bitte einen Modus auswählen.") from exc
        create_schedule_point(
            session,
            zone,
            weekday=weekday,
            minute=minute,
            mode_id=mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _schedule_page(
            request, session, zone, principal, values=values, errors=exc
        )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/schedule/points/move")
async def reposition_schedule_point(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Target of dragging in the week view.

    Deliberately an ordinary form and not a JSON interface: this way the same CSRF
    protection, the same permission check, and the same error display apply as for
    the route via the forms -- and the schedule stays fully operable without
    JavaScript, because dragging is only a second way of operating the same change.

    The point id lives in the body and not in the path, unlike the delete route next
    to it. The reason is htmx: `hx-boost` reads a form's `action` **once** when
    processing the page. A script that rewrites it before submission changes nothing
    -- the request would still go to the earlier path. With a fixed path and a field,
    this trap doesn't exist.
    """
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in ("point_id", "weekday", "time_of_day")
    }
    try:
        point_id = int(values["point_id"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    point = _point_or_404(session, zone, point_id)
    try:
        try:
            weekday = int(values["weekday"])
        except ValueError as exc:
            raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.") from exc
        move_schedule_point(
            session,
            zone,
            point,
            weekday=weekday,
            minute=time_of_day_in_minutes(values["time_of_day"]),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _schedule_page(
            request, session, zone, principal, move_error=exc.notice
        )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/schedule/points/mode")
async def change_schedule_point_mode_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Changes a point's mode through the same fixed-action form pattern as moving it.

    The point id deliberately lives in the body: hx-boost reads a form action once
    while processing the page, so changing an id in that path at submit time would
    still send the request to the old target.
    """
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    try:
        point_id = int(str(form.get("point_id", "")).strip())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    point = _point_or_404(session, zone, point_id)
    try:
        try:
            mode_id = int(str(form.get("mode_id", "")).strip())
        except ValueError as exc:
            raise ScheduleError("mode_id", "Bitte einen Modus auswählen.") from exc
        change_schedule_point_mode(
            session,
            zone,
            point,
            mode_id=mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _schedule_page(request, session, zone, principal, move_error=exc.notice)
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


def _point_or_404(session: Session, zone: Zone, point_id: int) -> SchedulePoint:
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return point


@router.get("/zones/{zone_id}/schedule/points/{point_id}/delete")
async def schedule_point_delete_form(
    zone_id: int,
    point_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    point = _point_or_404(session, zone, point_id)
    return templates.TemplateResponse(
        request,
        "schedule_point_delete.html",
        {"zone": zone, "point": point, "weekdays": dict(WEEKDAYS)},
    )


@router.post("/zones/{zone_id}/schedule/points/{point_id}/delete")
async def remove_schedule_point(
    zone_id: int,
    point_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    point = _point_or_404(session, zone, point_id)
    delete_schedule_point(
        session,
        zone,
        point,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


def _schedule_adopt_page(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    *,
    source_id: int | None = None,
    confirmation: bool = False,
    errors: str = "",
) -> Response:
    sources = [
        others
        for others in visible_zones(session, principal, "zone.read")
        if others.id != zone.id
    ]
    return templates.TemplateResponse(
        request,
        "schedule_adopt.html",
        {
            "zone": zone,
            "sources": sources,
            "source_id": source_id,
            "confirmation": confirmation,
            "errors": errors,
        },
    )


@router.get("/zones/{zone_id}/schedule/adopt")
async def schedule_adopt_form(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    return _schedule_adopt_page(request, session, principal, zone)


@router.post("/zones/{zone_id}/schedule/adopt")
async def execute_schedule_adoption(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    target = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id", "")))
    except ValueError:
        return _schedule_adopt_page(
            request, session, principal, target, errors="Bitte eine Quellzone auswählen."
        )
    source = next(
        (
            zone
            for zone in visible_zones(session, principal, "zone.read")
            if zone.id == source_id and zone.id != target.id
        ),
        None,
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    hat_plan = bool(_points(session, target.id))
    if hat_plan and str(form.get("confirmed", "")) != "yes":
        return _schedule_adopt_page(
            request,
            session,
            principal,
            target,
            source_id=source.id,
            confirmation=True,
        )
    adopt_schedule(
        session,
        target,
        source,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zones/{target.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )
