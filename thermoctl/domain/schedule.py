from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.modes import check_temperature

MINUTES_PER_WEEK = 7 * 24 * 60


@dataclass(frozen=True)
class Setpoint:
    """The result together with its reasoning — principle 5 from CLAUDE.md."""

    temperature_c: Decimal
    reason: str
    mode_code: str | None
    # The mode whose stored temperature currently applies -- None if the setpoint does
    # not come from a mode (fixed override). The interface needs it for the thermostat
    # on the start page: whoever adjusts it there adjusts *this* mode, not "right now".
    mode_id: int | None = None


# Deliberately NOT `frozen=True`: Python attaches a traceback to an exception when it
# is raised, and a frozen dataclass refuses exactly that. The bug only surfaces once
# the exception is passed far enough — in our case through FastAPI's dependency
# resolution — and then shows up as `FrozenInstanceError` instead of the error you are
# actually looking for.
@dataclass
class ScheduleError(Exception):
    field: str
    notice: str


@dataclass(frozen=True)
class DaySegment:
    """A contiguous schedule bar, cut off at a day boundary."""

    weekday: int
    start_minute: int
    end_minute: int
    mode_name: str
    # The id of the mode currently in effect. The interface resolves the setpoint
    # through it -- without it, the day plan could only be named, not rendered as
    # heat.
    mode_id: int = 0
    # The point that starts this bar -- None for the remainder carrying over from the
    # previous day. The interface needs it to be able to drag a bar; a bar without its
    # own point belongs to another day and stays fixed.
    point_id: int | None = None


# Only for the readable audit text. The interface's labeling lives in the view; here
# it is about an entry that should still be understandable weeks later.
_DAYS = {1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa", 7: "So"}


def _label_for(weekday: int, minute: int) -> str:
    return f"{_DAYS[weekday]} {minute // 60:02d}:{minute % 60:02d}"


def time_of_day_in_minutes(time_of_day: str) -> int:
    """Converts local `HH:MM` form input into the DB representation."""
    parts = time_of_day.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    hour, minute = (int(part) for part in parts)
    if hour > 23 or minute > 59:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    return hour * 60 + minute


def week_segments(
    points: list[SchedulePoint], mode_names: dict[int, str]
) -> list[DaySegment]:
    """Splits the weekly ring into bars, without breaking the ring at Monday 00:00."""
    if not points:
        return []
    sorted_points = sorted(points, key=_point_minute)
    segments: list[DaySegment] = []
    for weekday in range(1, 8):
        day_start = (weekday - 1) * 1440
        limits = [
            day_start,
            *(
                _point_minute(point)
                for point in sorted_points
                if point.weekday == weekday and point.minute_of_day > 0
            ),
            day_start + 1440,
        ]
        for start, end_at in zip(limits, limits[1:], strict=False):
            before_it = [point for point in sorted_points if _point_minute(point) <= start]
            gilt = max(before_it or sorted_points, key=_point_minute)
            starts_here = next(
                (point for point in sorted_points if _point_minute(point) == start), None
            )
            segments.append(
                DaySegment(
                    weekday=weekday,
                    start_minute=start - day_start,
                    end_minute=end_at - day_start,
                    mode_name=mode_names[gilt.setpoint_mode_id],
                    mode_id=gilt.setpoint_mode_id,
                    point_id=starts_here.id if starts_here else None,
                )
            )
    return segments


def _moment_taken(session: Session, zone_id: int, weekday: int, minute: int) -> bool:
    """Its own function instead of an embedded query — same as with the zone names.

    This makes the race condition testable that the check right after it guards
    against: if the pre-check says 'free' because a concurrent request has just
    claimed the same slot, the `IntegrityError` must turn into an understandable
    message instead of a 500.
    """
    return (
        session.scalar(
            select(SchedulePoint.id).where(
                SchedulePoint.zone_id == zone_id,
                SchedulePoint.weekday == weekday,
                SchedulePoint.minute_of_day == minute,
            )
        )
        is not None
    )


def create_schedule_point(
    session: Session,
    zone: Zone,
    *,
    weekday: int,
    minute: int,
    mode_id: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> SchedulePoint:
    if not 1 <= weekday <= 7:
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    if session.get(SetpointMode, mode_id) is None:
        raise ScheduleError("mode_id", "Dieser Modus ist nicht bekannt.")
    if _moment_taken(session, zone.id, weekday, minute):
        raise ScheduleError("time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")
    point = SchedulePoint(
        zone_id=zone.id,
        weekday=weekday,
        minute_of_day=minute,
        setpoint_mode_id=mode_id,
    )
    try:
        with session.begin_nested():
            session.add(point)
            session.flush()
            audit.record(
                session,
                source=source,
                action="create",
                object_type="schedule_point",
                object_id=str(point.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' angelegt",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ScheduleError(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return point


def move_schedule_point(
    session: Session,
    zone: Zone,
    point: SchedulePoint,
    *,
    weekday: int,
    minute: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> SchedulePoint:
    """Moves an existing point to a different time.

    Functionally the same as deleting and recreating it, but as one operation: the
    point keeps its id, the audit log shows a move instead of two unrelated entries,
    and no gap appears in the plan in between.

    The collision check is the same function used at creation -- two separate checks
    would be two that can drift apart.
    """
    if not 1 <= weekday <= 7:
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    if not 0 <= minute <= 1439:
        raise ScheduleError("time_of_day", "Bitte eine gültige Uhrzeit eingeben.")
    if point.weekday == weekday and point.minute_of_day == minute:
        return point
    if _moment_taken(session, zone.id, weekday, minute):
        raise ScheduleError("time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt.")

    before = _label_for(point.weekday, point.minute_of_day)
    after = _label_for(weekday, minute)
    try:
        with session.begin_nested():
            point.weekday = weekday
            point.minute_of_day = minute
            session.flush()
            audit.record(
                session,
                source=source,
                action="update",
                object_type="schedule_point",
                object_id=str(point.id),
                summary=f"Zeitplanpunkt für Zone '{zone.display_name}' verschoben",
                detail=f"{before} → {after}",
                user_id=user_id,
                token_id=token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ScheduleError(
            "uhrzeit", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return point


def delete_schedule_point(
    session: Session,
    zone: Zone,
    point: SchedulePoint,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    point_id = point.id
    session.delete(point)
    audit.record(
        session,
        source=source,
        action="delete",
        object_type="schedule_point",
        object_id=str(point_id),
        summary=f"Zeitplanpunkt für Zone '{zone.display_name}' gelöscht",
        user_id=user_id,
        token_id=token_id,
    )


def adopt_schedule(
    session: Session,
    target: Zone,
    # `vorlage` and not `quelle`: throughout the project, the name `quelle` stands for
    # the origin of an audit entry (web, api, mcp). Two meanings in one signature would
    # be a trap for the next caller.
    template: Zone,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Replaces the target plan atomically with independent copies of the source plan."""
    source_points = list(
        session.scalars(
            select(SchedulePoint).where(SchedulePoint.zone_id == template.id)
        )
    )
    session.execute(delete(SchedulePoint).where(SchedulePoint.zone_id == target.id))
    session.add_all(
        SchedulePoint(
            zone_id=target.id,
            weekday=point.weekday,
            minute_of_day=point.minute_of_day,
            setpoint_mode_id=point.setpoint_mode_id,
        )
        for point in source_points
    )
    audit.record(
        session,
        source=source,
        action="update",
        object_type="schedule",
        object_id=str(target.id),
        summary=(
            f"Zeitplan für Zone '{target.display_name}' von "
            f"'{template.display_name}' übernommen"
        ),
        user_id=user_id,
        token_id=token_id,
    )


def _week_minute(moment: datetime) -> int:
    return (moment.isoweekday() - 1) * 24 * 60 + moment.hour * 60 + moment.minute


def _point_minute(point: SchedulePoint) -> int:
    return (point.weekday - 1) * 24 * 60 + point.minute_of_day


def current_point(
    points: list[SchedulePoint], moment: datetime
) -> SchedulePoint | None:
    """The last point before or exactly at the given time.

    The week is a ring: if no point lies before it, the last point of the week
    applies. So there can be neither gaps nor overlaps, as long as at least one point
    exists at all.
    """
    if not points:
        return None
    now = _week_minute(moment)
    before_it = [p for p in points if _point_minute(p) <= now]
    return max(before_it or points, key=_point_minute)


def next_point(points: list[SchedulePoint], moment: datetime) -> datetime | None:
    """When the next schedule point falls — the basis for 'until the next switch'."""
    if not points:
        return None
    now = _week_minute(moment)
    candidates = sorted(_point_minute(p) for p in points)
    later = [m for m in candidates if m > now]
    target = later[0] if later else candidates[0] + MINUTES_PER_WEEK
    week_start = (moment - timedelta(days=moment.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start + timedelta(minutes=target)


def create_override(
    session: Session,
    zone: Zone,
    temperature_c: Decimal,
    ends_at: datetime | None,
    *,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str = "web",
) -> ZoneOverride:
    """Creates a concrete override; web, API and MCP share this mutation.

    The temperature bound is checked **here** and not in the adapter. Until the final
    review it appeared three different ways: the interface checked by hand with no
    decimal places, the REST interface through its schema — and the MCP server not at
    all. A tool could have created `temperature_c=99` there, and that value flows
    unfiltered into the armed control decision in sub-project 4. An input bound that
    depends on which adapter you chose is not a bound at all.
    """
    temperature_c = check_temperature(temperature_c)
    # Used to be hardcoded to "api" even when the override came from the interface:
    # the `zone_override.source_id` column answers the question "how was this set",
    # and it answered it wrongly for two out of three adapters.
    source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
    if source_id is None:
        raise ValueError(f"Unbekannte Quelle {source!r}")
    entry = ZoneOverride(
        zone_id=zone.id,
        temperature_c=temperature_c,
        starts_at=utcnow(),
        ends_at=ends_at,
        created_by_user_id=user_id,
        created_by_token_id=token_id,
        source_id=source_id,
    )
    session.add(entry)
    session.flush()
    return entry


def cancel_override(session: Session, zone: Zone) -> ZoneOverride | None:
    """Ends the most recent still-active override, without deleting history."""
    entry = session.scalars(
        select(ZoneOverride)
        .where(ZoneOverride.zone_id == zone.id, ZoneOverride.cancelled_at.is_(None))
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if entry is not None:
        entry.cancelled_at = utcnow()
    return entry


def temperature_for_mode(session: Session, zone: Zone, mode_id: int) -> Decimal | None:
    """The temperature stored for this zone for a mode, or None.

    Public, because the thermostat on the start page needs the same value to add half
    a step to it -- and because an underscore that three modules ignore anyway is not
    protection, only a false signal.
    """
    return session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode_id
        )
    )


def resolved_setpoint(session: Session, zone: Zone, now_utc: datetime) -> Setpoint:
    """Which setpoint currently applies, and why.

    Precedence: operating mode 'off' beats everything, then a running override, then
    the schedule, and last of all frost protection.
    """
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    frost_id = settings.frost_protection_mode_id
    frost_temp = temperature_for_mode(session, zone, frost_id) or Decimal("16.0")
    frost_code = session.scalar(select(SetpointMode.code).where(SetpointMode.id == frost_id))

    if zone.operating_mode.code == "off":
        return Setpoint(frost_temp, "Betriebsart Aus — Frostschutz", frost_code, frost_id)

    running = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now_utc,
        )
        # `id` as a second criterion: MariaDB stores DATETIME with second precision.
        # Two overrides within the same second -- say, one replacing another -- would
        # otherwise share the same timestamp, and which one applies would be decided
        # by the database at its own discretion.
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    if running is not None and (running.ends_at is None or running.ends_at > now_utc):
        if running.temperature_c is not None:
            return Setpoint(
                running.temperature_c, "Uebersteuerung (feste Temperatur)", None, None
            )
        temp = temperature_for_mode(session, zone, running.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == running.setpoint_mode_id)
        )
        if temp is not None:
            return Setpoint(
                temp, f"Uebersteuerung auf Modus {code}", code, running.setpoint_mode_id
            )

    # Schedules are stored in local time, so the night setback does not shift when
    # clocks change for daylight saving.
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo(settings.timezone)
    )
    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    gilt = current_point(points, local.replace(tzinfo=None))
    if gilt is not None:
        temp = temperature_for_mode(session, zone, gilt.setpoint_mode_id)
        mode = session.get(SetpointMode, gilt.setpoint_mode_id)
        if temp is not None and mode is not None:
            time_of_day = f"{gilt.minute_of_day // 60:02d}:{gilt.minute_of_day % 60:02d}"
            return Setpoint(
                temp, f"Zeitplan: Modus {mode.name} ab {time_of_day}", mode.code, mode.id
            )

    return Setpoint(
        frost_temp, "Kein Zeitplan hinterlegt — Frostschutz", frost_code, frost_id
    )


def end_of_next_switch(
    session: Session, zone: Zone, now_utc: datetime | None = None
) -> datetime | None:
    """When the next schedule change falls, as naive UTC — or None without a schedule.

    Lives here and not in the adapter, because both the interface and the REST
    interface ask for it. Until the final review of sub-project 3, the computation
    appeared twice, separately in both adapters: a later fix to timezone handling
    would have been applied to one path and forgotten in the other, and the same zone
    would have gotten a different end depending on which way it was reached.

    `jetzt_utc` can be explicitly supplied because boost needs both at once: the point
    that would come next, *and* its point in time. If this function reached for the
    real clock while the caller was computing against a supplied moment, the two
    halves would refer to different instants -- the override would then end at some
    point, just not at its actual schedule point.
    """
    settings = session.get(Setting, 1)
    timezone_name = ZoneInfo(settings.timezone if settings is not None else "Europe/Berlin")
    local = (now_utc or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(timezone_name)
    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    end_at = next_point(points, local.replace(tzinfo=None))
    if end_at is None:
        return None
    return end_at.replace(tzinfo=timezone_name).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
