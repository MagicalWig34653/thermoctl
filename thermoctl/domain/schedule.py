from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.modes import check_temperature
from thermoctl.domain.time import local_time

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


ScheduleSnapshot = tuple[tuple[int, int, int], ...]
ScheduleGesture = tuple[ScheduleSnapshot, ScheduleSnapshot, int]


def schedule_snapshot(points: list[SchedulePoint]) -> ScheduleSnapshot:
    """Returns the stable, identifier-free representation used for one-step undo."""
    return tuple(
        sorted(
            (point.weekday, point.minute_of_day, point.setpoint_mode_id)
            for point in points
        )
    )


def _zone_points(session: Session, zone_id: int) -> list[SchedulePoint]:
    return list(
        session.scalars(
            select(SchedulePoint)
            .where(SchedulePoint.zone_id == zone_id)
            .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
        )
    )


def _mode_at(points: list[SchedulePoint], week_minute: int, fallback: int) -> int:
    if not points:
        return fallback
    before = [point for point in points if _point_minute(point) <= week_minute]
    return max(before or points, key=_point_minute).setpoint_mode_id


def _replace_snapshot(
    session: Session, zone: Zone, snapshot: ScheduleSnapshot
) -> None:
    session.execute(delete(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    session.add_all(
        SchedulePoint(
            zone_id=zone.id,
            weekday=weekday,
            minute_of_day=minute,
            setpoint_mode_id=mode_id,
        )
        for weekday, minute, mode_id in snapshot
    )
    session.flush()


def _canonical_snapshot(entries: dict[int, int]) -> ScheduleSnapshot:
    """Drops switches whose mode is already in effect on the weekly ring."""
    ordered = sorted(entries.items())
    while len(ordered) > 1 and ordered[0][1] == ordered[-1][1]:
        ordered.pop(0)
    reduced: list[tuple[int, int]] = []
    for minute, mode_id in ordered:
        if reduced and reduced[-1][1] == mode_id:
            continue
        reduced.append((minute, mode_id))
    return tuple(
        (minute // 1440 + 1, minute % 1440, mode_id)
        for minute, mode_id in reduced
    )


def _schedule_revision(session: Session, event: AuditEvent) -> int:
    """Returns the exact persisted revision created by this schedule gesture."""
    session.flush()
    revision = event.id
    if revision is None:  # pragma: no cover - callers record before asking for it
        raise RuntimeError("Die Zeitplanrevision fehlt.")
    return revision


def paint_schedule_interval(
    session: Session,
    zone: Zone,
    *,
    weekday: int,
    start_minute: int,
    end_minute: int,
    mode_id: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> ScheduleGesture | None:
    """Paints one half-open interval and stores its minimal switch-point form.

    Painting is deliberately confined to one day. An end at 24:00 is valid; an end
    before or equal to the start would cross midnight and is rejected. The configured
    frost-protection mode is the background of an empty schedule.
    """
    if not 1 <= weekday <= 7:
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    if not 0 <= start_minute < 1440 or not 0 < end_minute <= 1440:
        raise ScheduleError("time_range", "Bitte einen gültigen Zeitraum auswählen.")
    if end_minute <= start_minute:
        raise ScheduleError(
            "time_range", "Malen über Mitternacht ist nicht möglich; bitte tageweise malen."
        )
    if session.get(SetpointMode, mode_id) is None:
        raise ScheduleError("mode_id", "Dieser Modus ist nicht bekannt.")
    settings = session.get(Setting, 1)
    if settings is None:  # pragma: no cover - setup and every normal request guarantee it
        raise RuntimeError("Die Grundeinstellungen fehlen.")

    points = _zone_points(session, zone.id)
    before = schedule_snapshot(points)
    absolute_start = (weekday - 1) * 1440 + start_minute
    absolute_end = (weekday - 1) * 1440 + end_minute
    fallback = settings.frost_protection_mode_id
    # A one-point schedule applies that mode around the complete weekly ring. Painting
    # the same mode on Monday therefore is a genuine no-op, even if its only stored
    # point is on Wednesday: no effective minute would change.
    if (
        _mode_at(points, absolute_start, fallback) == mode_id
        and all(
            point.setpoint_mode_id == mode_id
            for point in points
            if absolute_start < _point_minute(point) < absolute_end
        )
    ):
        return None
    end_mode = _mode_at(points, absolute_end % MINUTES_PER_WEEK, fallback)
    entries = {_point_minute(point): point.setpoint_mode_id for point in points}
    for minute in [minute for minute in entries if absolute_start <= minute < absolute_end]:
        del entries[minute]
    entries[absolute_start] = mode_id
    if absolute_end < MINUTES_PER_WEEK:
        entries[absolute_end] = end_mode
    else:
        entries[0] = end_mode
    after = _canonical_snapshot(entries)
    # The semantic no-op is caught before rewriting `entries`. Keep the comparison as
    # a final guard against a future normalization rule that produces the old plan.
    if after == before:  # pragma: no cover - unreachable with today's normalization
        return None
    _replace_snapshot(session, zone, after)
    event = audit.record(
        session,
        source=source,
        action="update",
        object_type="schedule",
        object_id=str(zone.id),
        summary=f"Zeitplan für Zone '{zone.display_name}' gemalt",
        detail=(
            f"{_DAYS[weekday]} {_label_time(start_minute)}–{_label_time(end_minute)}"
        ),
        user_id=user_id,
        token_id=token_id,
    )
    return before, after, _schedule_revision(session, event)


def _label_time(minute: int) -> str:
    if minute == 1440:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def copy_schedule_day(
    session: Session,
    zone: Zone,
    *,
    source_weekday: int,
    target_weekdays: list[int],
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> ScheduleGesture | None:
    """Copies a source day's effective pattern to target days as one gesture."""
    if not 1 <= source_weekday <= 7 or any(not 1 <= day <= 7 for day in target_weekdays):
        raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.")
    points = _zone_points(session, zone.id)
    if not points:
        return None
    before = schedule_snapshot(points)
    entries = {_point_minute(point): point.setpoint_mode_id for point in points}

    def day_pattern(weekday: int) -> list[tuple[int, int]]:
        day_start = (weekday - 1) * 1440
        pattern = [(0, _mode_at(points, day_start, 0))]
        for point in points:
            if (
                point.weekday == weekday
                and point.minute_of_day > 0
                and point.setpoint_mode_id != pattern[-1][1]
            ):
                pattern.append((point.minute_of_day, point.setpoint_mode_id))
        return pattern

    source_pattern = day_pattern(source_weekday)
    target_days = sorted(set(target_weekdays) - {source_weekday})
    if all(day_pattern(day) == source_pattern for day in target_days):
        return None
    target_day_set = set(target_days)
    for day in target_days:
        day_start = (day - 1) * 1440
        next_start = day_start + 1440
        for minute in [minute for minute in entries if day_start <= minute < next_start]:
            del entries[minute]
        for offset, copied_mode in source_pattern:
            entries[day_start + offset] = copied_mode
    # Restore the old ring only where a copied day is followed by an untouched day.
    # Writing every boundary while iterating would overwrite Monday 00:00 when Sunday
    # happens to be the last copied target.
    for day in target_days:
        next_day = day % 7 + 1
        if next_day not in target_day_set:
            next_start = (day * 1440) % MINUTES_PER_WEEK
            entries[next_start] = _mode_at(points, next_start, 0)
    after = _canonical_snapshot(entries)
    # The effective-pattern check catches current no-ops. Keep this final guard for
    # future normalization changes that may arrive at the old storage representation.
    if after == before:  # pragma: no cover
        return None
    _replace_snapshot(session, zone, after)
    event = audit.record(
        session, source=source, action="update", object_type="schedule",
        object_id=str(zone.id),
        summary=f"Tag im Zeitplan für Zone '{zone.display_name}' übertragen",
        detail=(
            f"{_DAYS[source_weekday]} → "
            f"{', '.join(_DAYS[d] for d in sorted(set(target_weekdays)))}"
        ),
        user_id=user_id, token_id=token_id,
    )
    return before, after, _schedule_revision(session, event)


def undo_schedule_gesture(
    session: Session,
    zone: Zone,
    *,
    before: ScheduleSnapshot,
    expected_after: ScheduleSnapshot,
    expected_revision: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Restores one gesture only if no later schedule edit has intervened."""
    current_revision = session.scalar(
        select(func.max(AuditEvent.id)).where(
            AuditEvent.object_type == "schedule",
            AuditEvent.object_id == str(zone.id),
        )
    )
    if (
        current_revision != expected_revision
        or schedule_snapshot(_zone_points(session, zone.id)) != expected_after
    ):
        raise ScheduleError("undo", "Der Zeitplan wurde inzwischen geändert.")
    _replace_snapshot(session, zone, before)
    audit.record(
        session, source=source, action="update", object_type="schedule",
        object_id=str(zone.id),
        summary=(
            f"Letzte Zeitplangeste für Zone '{zone.display_name}' rückgängig gemacht"
        ),
        user_id=user_id, token_id=token_id,
    )


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
            "time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
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
            "time_of_day", "Zu diesem Zeitpunkt gibt es bereits einen Punkt."
        ) from exc
    return point


def change_schedule_point_mode(
    session: Session,
    zone: Zone,
    point: SchedulePoint,
    *,
    mode_id: int,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> SchedulePoint:
    """Changes a point's mode without replacing the point."""
    mode = session.get(SetpointMode, mode_id)
    if mode is None:
        raise ScheduleError("mode_id", "Dieser Modus ist nicht bekannt.")
    if point.setpoint_mode_id == mode_id:
        return point

    previous_mode = session.get(SetpointMode, point.setpoint_mode_id)
    previous_name = previous_mode.name if previous_mode is not None else str(point.setpoint_mode_id)
    point.setpoint_mode_id = mode_id
    session.flush()
    audit.record(
        session,
        source=source,
        action="update",
        object_type="schedule_point",
        object_id=str(point.id),
        summary=f"Modus des Zeitplanpunkts für Zone '{zone.display_name}' geändert",
        detail=f"{previous_name} → {mode.name}",
        user_id=user_id,
        token_id=token_id,
    )
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
    now: datetime | None = None,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str = "web",
) -> ZoneOverride:
    """Creates a concrete override; web, API and MCP share this mutation.

    `now` is the moment the override starts from, and callers that already have one
    must pass it. Reading the clock again here looked harmless -- the two values are
    milliseconds apart in normal operation -- but they are not the same value, and
    `resolved_setpoint` only counts an override whose `starts_at` has already been
    reached. A boost stamped a hair later than the moment the decision was made for
    is an override that does not yet apply, and the caller sees its own change have
    no effect. The same defect was fixed once already in `end_of_next_switch`; this
    is the other half of it.

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
        starts_at=now if now is not None else utcnow(),
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


def frost_protection_temperature(session: Session, zone: Zone) -> Decimal:
    """The zone's frost-protection setpoint.

    Extracted because a second caller appeared: a self-regulating valve is told this
    number when a window is open. Computed twice, the two would eventually differ, and
    the difference would be a room that freezes in one path and not in the other.

    The fallback of 16 degrees applies when the frost mode has no setpoint for this
    zone -- a plant that is not fully set up should still not freeze.
    """
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    return temperature_for_mode(session, zone, settings.frost_protection_mode_id) or Decimal(
        "16.0"
    )


def resolved_setpoint(session: Session, zone: Zone, now_utc: datetime) -> Setpoint:
    """Which setpoint currently applies, and why.

    Precedence: operating mode 'off' beats everything, then a running override, then
    the schedule, and last of all frost protection.
    """
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    frost_id = settings.frost_protection_mode_id
    frost_temp = frost_protection_temperature(session, zone)
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
                running.temperature_c, "Übersteuerung (feste Temperatur)", None, None
            )
        temp = temperature_for_mode(session, zone, running.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == running.setpoint_mode_id)
        )
        if temp is not None:
            return Setpoint(
                temp, f"Übersteuerung auf Modus {code}", code, running.setpoint_mode_id
            )

    # Schedules are stored in local time, so the night setback does not shift when
    # clocks change for daylight saving.
    local = local_time(now_utc, settings.timezone)
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
    configured_timezone = settings.timezone if settings is not None else "Europe/Berlin"
    timezone_name = ZoneInfo(configured_timezone)
    local = local_time(now_utc or utcnow(), configured_timezone)
    points = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    end_at = next_point(points, local.replace(tzinfo=None))
    if end_at is None:
        return None
    return end_at.replace(tzinfo=timezone_name).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
