from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_mode,
    create_settings,
    create_zone,
    point,
    source,
    zone_with_schedule,
)
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode
from thermoctl.domain import schedule as schedule_module
from thermoctl.domain.modes import DomainError
from thermoctl.domain.schedule import (
    MINUTES_PER_WEEK,
    DaySegment,
    NextSwitch,
    OverrideAlreadyRunning,
    ScheduleError,
    Setpoint,
    cancel_override,
    cancel_vacation,
    change_schedule_point_mode,
    copy_schedule_day,
    create_override,
    create_schedule_point,
    create_vacation,
    current_or_upcoming_vacation,
    current_point,
    frost_protection_temperature,
    jump_to_next_switch,
    move_schedule_point,
    next_point,
    next_switch,
    paint_schedule_interval,
    resolved_setpoint,
    running_override,
    running_vacation,
    schedule_forecast,
    schedule_snapshot,
    time_of_day_in_minutes,
    undo_schedule_gesture,
    week_segments,
)


def _stored_point(
    session: Session, zone_id: int, weekday: int, minute: int, mode_id: int
) -> None:
    session.add(
        SchedulePoint(
            zone_id=zone_id,
            weekday=weekday,
            minute_of_day=minute,
            setpoint_mode_id=mode_id,
        )
    )
    session.flush()


def _stored_snapshot(session: Session, zone_id: int) -> tuple[tuple[int, int, int], ...]:
    return schedule_snapshot(
        list(session.query(SchedulePoint).filter_by(zone_id=zone_id).all())
    )


def test_schedule_value_objects_are_immutable_with_documented_defaults() -> None:
    setpoint = Setpoint(Decimal("20.5"), "test reason", "comfort")
    segment = DaySegment(3, 17, 1439, "Comfort")

    assert setpoint.mode_id is None
    assert segment.mode_id == 0
    assert segment.point_id is None
    with pytest.raises(FrozenInstanceError):
        setpoint.temperature_c = Decimal("19.0")
    with pytest.raises(FrozenInstanceError):
        segment.end_minute = 1440


def test_week_constant_and_time_parser_preserve_real_calendar_values() -> None:
    assert MINUTES_PER_WEEK == 10080
    assert time_of_day_in_minutes("12:34") == 754
    assert time_of_day_in_minutes("23:59") == 1439
    for invalid in ("24:00", "12:60", "12", "12:x"):
        with pytest.raises(ScheduleError):
            time_of_day_in_minutes(invalid)


def test_calendar_helpers_represent_thursday_afternoon_without_aliasing() -> None:
    point_at_thursday = SchedulePoint(
        weekday=4, minute_of_day=17 * 60 + 23, setpoint_mode_id=9
    )

    assert schedule_module._week_minute(datetime(2026, 9, 3, 17, 23)) == 5363
    assert schedule_module._point_minute(point_at_thursday) == 5363
    assert schedule_module._label_time(17 * 60 + 23) == "17:23"
    assert schedule_module._label_for(4, 17 * 60 + 23) == "Do 17:23"


def test_canonical_schedule_removes_only_redundant_ring_switches() -> None:
    assert schedule_module._canonical_snapshot(
        {1563: 11, 2194: 22, 4520: 22, 6081: 11}
    ) == ((2, 754, 22), (5, 321, 11))


def test_canonical_schedule_preserves_singletons_and_two_mode_rings() -> None:
    assert schedule_module._canonical_snapshot({1440: 11}) == ((2, 0, 11),)
    assert schedule_module._canonical_snapshot({0: 11, 1440: 22}) == (
        (1, 0, 11),
        (2, 0, 22),
    )
    assert schedule_module._canonical_snapshot({0: 11, 1439: 22, 1440: 11}) == (
        (1, 1439, 22),
        (2, 0, 11),
    )


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 31, 0, 0), 0),
        (datetime(2026, 8, 31, 23, 59), 1439),
        (datetime(2026, 9, 1, 0, 0), 1440),
        (datetime(2026, 9, 3, 12, 34), 5074),
        (datetime(2026, 9, 6, 23, 59), 10079),
    ],
)
def test_week_minutes_match_known_calendar_landmarks(
    moment: datetime, expected: int
) -> None:
    assert schedule_module._week_minute(moment) == expected


def test_schedule_labels_preserve_minute_boundaries() -> None:
    assert schedule_module._label_time(1) == "00:01"
    assert schedule_module._label_time(60) == "01:00"
    assert schedule_module._label_time(1439) == "23:59"
    assert schedule_module._label_time(1440) == "24:00"
    assert schedule_module._label_for(7, 1439) == "So 23:59"


@pytest.mark.parametrize("weekday", [0, 8])
def test_painting_rejects_each_weekday_outside_the_calendar(
    session: Session, weekday: int
) -> None:
    create_settings(session)
    zone = create_zone(session, f"invalid-paint-weekday-{weekday}")
    mode = create_mode(session, f"invalid-paint-mode-{weekday}")
    with pytest.raises(ScheduleError, match="Wochentag"):
        paint_schedule_interval(
            session, zone, weekday=weekday, start_minute=1, end_minute=1439,
            mode_id=mode.id, user_id=None,
        )


@pytest.mark.parametrize(
    ("start_minute", "end_minute"),
    [(-1, 1), (1440, 1440), (0, 0), (0, 1441), (754, 754), (755, 754)],
)
def test_painting_rejects_every_open_interval_boundary_violation(
    session: Session, start_minute: int, end_minute: int
) -> None:
    create_settings(session)
    zone = create_zone(session, f"pr-{start_minute}-{end_minute}")
    mode = create_mode(session, f"pm-{start_minute}-{end_minute}")
    with pytest.raises(ScheduleError, match="Zeitraum|Mitternacht"):
        paint_schedule_interval(
            session, zone, weekday=4, start_minute=start_minute,
            end_minute=end_minute, mode_id=mode.id, user_id=None,
        )


def test_week_segments_keep_non_monday_offsets_and_switch_ownership() -> None:
    night = SchedulePoint(weekday=2, minute_of_day=123, setpoint_mode_id=11)
    day = SchedulePoint(weekday=3, minute_of_day=754, setpoint_mode_id=22)
    night.id = 101
    day.id = 202

    segments = week_segments([day, night], {11: "Night", 22: "Day"})

    wednesday = [segment for segment in segments if segment.weekday == 3]
    assert wednesday == [
        DaySegment(3, 0, 754, "Night", 11, None),
        DaySegment(3, 754, 1440, "Day", 22, 202),
    ]
    assert len(segments) == 9


def test_week_segments_keep_midnight_and_minute_one_distinct_on_sunday() -> None:
    midnight = SchedulePoint(weekday=7, minute_of_day=0, setpoint_mode_id=11)
    minute_one = SchedulePoint(weekday=7, minute_of_day=1, setpoint_mode_id=22)
    midnight.id = 101
    minute_one.id = 202

    sunday = [
        segment
        for segment in week_segments(
            [minute_one, midnight], {11: "Midnight", 22: "Minute one"}
        )
        if segment.weekday == 7
    ]

    assert sunday == [
        DaySegment(7, 0, 1, "Midnight", 11, 101),
        DaySegment(7, 1, 1440, "Minute one", 22, 202),
    ]


def test_week_segments_place_a_thursday_switch_on_thursday() -> None:
    wednesday = SchedulePoint(weekday=3, minute_of_day=100, setpoint_mode_id=11)
    thursday = SchedulePoint(weekday=4, minute_of_day=200, setpoint_mode_id=22)
    thursday.id = 202

    segments = week_segments([thursday, wednesday], {11: "Before", 22: "After"})

    assert [segment for segment in segments if segment.weekday == 4] == [
        DaySegment(4, 0, 200, "Before", 11, None),
        DaySegment(4, 200, 1440, "After", 22, 202),
    ]


def test_current_and_next_point_use_weekday_and_minute_components() -> None:
    tuesday = SchedulePoint(weekday=2, minute_of_day=123, setpoint_mode_id=1)
    friday = SchedulePoint(weekday=5, minute_of_day=754, setpoint_mode_id=2)

    moment = datetime(2026, 9, 4, 12, 33)
    assert current_point([tuesday, friday], moment) is tuesday
    assert next_point([tuesday, friday], moment) == datetime(2026, 9, 4, 12, 34)
    assert next_point([tuesday, friday], datetime(2026, 9, 6, 23, 59)) == datetime(
        2026, 9, 8, 2, 3
    )


def test_next_point_skips_the_current_switch_and_chooses_the_nearest_later_one() -> None:
    current = SchedulePoint(weekday=4, minute_of_day=754, setpoint_mode_id=1)
    first_later = SchedulePoint(weekday=4, minute_of_day=755, setpoint_mode_id=2)
    second_later = SchedulePoint(weekday=6, minute_of_day=1234, setpoint_mode_id=3)

    assert next_point(
        [second_later, current, first_later], datetime(2026, 9, 3, 12, 34)
    ) == datetime(2026, 9, 3, 12, 35)


def test_painting_a_non_monday_interval_preserves_exact_minute_boundaries(
    session: Session,
) -> None:
    settings = create_settings(session)
    source(session)
    zone = create_zone(session, "calendar-paint")
    comfort = create_mode(session, "calendar-comfort", "Comfort")

    paint_schedule_interval(
        session,
        zone,
        weekday=3,
        start_minute=754,
        end_minute=837,
        mode_id=comfort.id,
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (3, 754, comfort.id),
        (3, 837, settings.frost_protection_mode_id),
    )


@pytest.mark.parametrize(
    ("start_minute", "end_minute"),
    [(0, 1), (1439, 1440)],
)
def test_painting_accepts_each_minimal_day_edge_interval(
    session: Session, start_minute: int, end_minute: int
) -> None:
    settings = create_settings(session)
    source(session)
    zone = create_zone(session, f"paint-edge-{start_minute}")
    comfort = create_mode(session, f"paint-edge-mode-{start_minute}", "Comfort")

    paint_schedule_interval(
        session,
        zone,
        weekday=4,
        start_minute=start_minute,
        end_minute=end_minute,
        mode_id=comfort.id,
        user_id=None,
    )

    expected = [(4, start_minute, comfort.id)]
    if end_minute < 1440:
        expected.append((4, end_minute, settings.frost_protection_mode_id))
    else:
        expected.append((5, 0, settings.frost_protection_mode_id))
    assert _stored_snapshot(session, zone.id) == tuple(expected)


def test_painting_replaces_every_switch_inside_but_preserves_the_end_mode(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "paint-over-switches")
    before = create_mode(session, "paint-before", "Before")
    painted = create_mode(session, "paint-new", "Painted")
    inside = create_mode(session, "paint-inside", "Inside")
    after = create_mode(session, "paint-after", "After")
    _stored_point(session, zone.id, 5, 100, before.id)
    _stored_point(session, zone.id, 5, 200, inside.id)
    _stored_point(session, zone.id, 5, 300, after.id)

    paint_schedule_interval(
        session,
        zone,
        weekday=5,
        start_minute=100,
        end_minute=300,
        mode_id=painted.id,
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (5, 100, painted.id),
        (5, 300, after.id),
    )


def test_painting_restores_the_local_end_mode_not_the_week_end_mode(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "paint-local-end")
    local = create_mode(session, "paint-local-mode", "Local")
    painted = create_mode(session, "paint-local-new", "Painted")
    week_end = create_mode(session, "paint-week-end", "Week end")
    _stored_point(session, zone.id, 4, 100, local.id)
    _stored_point(session, zone.id, 7, 1000, week_end.id)

    paint_schedule_interval(
        session,
        zone,
        weekday=4,
        start_minute=200,
        end_minute=300,
        mode_id=painted.id,
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (4, 100, local.id),
        (4, 200, painted.id),
        (4, 300, local.id),
        (7, 1000, week_end.id),
    )


def test_painting_does_not_treat_different_interior_modes_as_a_no_op(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "paint-interior-mode")
    comfort = create_mode(session, "paint-interior-comfort", "Comfort")
    setback = create_mode(session, "paint-interior-setback", "Setback")
    _stored_point(session, zone.id, 3, 100, comfort.id)
    _stored_point(session, zone.id, 3, 200, setback.id)
    _stored_point(session, zone.id, 3, 300, comfort.id)

    result = paint_schedule_interval(
        session,
        zone,
        weekday=3,
        start_minute=100,
        end_minute=300,
        mode_id=comfort.id,
        user_id=None,
    )

    assert result is not None
    assert _stored_snapshot(session, zone.id) == ((3, 300, comfort.id),)


def test_sunday_midnight_restoration_does_not_overwrite_monday_minute_one(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "paint-week-ring-minute-one")
    monday = create_mode(session, "paint-ring-monday", "Monday")
    after_midnight = create_mode(session, "paint-ring-minute-one", "Minute one")
    painted = create_mode(session, "paint-ring-sunday", "Sunday")
    _stored_point(session, zone.id, 1, 0, monday.id)
    _stored_point(session, zone.id, 1, 1, after_midnight.id)

    paint_schedule_interval(
        session,
        zone,
        weekday=7,
        start_minute=1439,
        end_minute=1440,
        mode_id=painted.id,
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (1, 0, monday.id),
        (1, 1, after_midnight.id),
        (7, 1439, painted.id),
    )


def test_copying_between_non_adjacent_days_replaces_only_the_target_day(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "calendar-copy")
    night = create_mode(session, "calendar-night", "Night")
    comfort = create_mode(session, "calendar-day", "Comfort")
    _stored_point(session, zone.id, 2, 123, night.id)
    _stored_point(session, zone.id, 2, 754, comfort.id)
    _stored_point(session, zone.id, 4, 200, comfort.id)
    _stored_point(session, zone.id, 5, 321, night.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=2,
        target_weekdays=[4],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (2, 754, comfort.id),
        (4, 0, night.id),
        (4, 754, comfort.id),
        (5, 321, night.id),
    )


def test_copying_preserves_a_minute_one_switch_and_the_following_day(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-minute-one")
    night = create_mode(session, "copy-minute-night", "Night")
    morning = create_mode(session, "copy-minute-morning", "Morning")
    untouched = create_mode(session, "copy-minute-untouched", "Untouched")
    _stored_point(session, zone.id, 2, 0, night.id)
    _stored_point(session, zone.id, 2, 1, morning.id)
    _stored_point(session, zone.id, 4, 0, untouched.id)
    _stored_point(session, zone.id, 5, 17, night.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=2,
        target_weekdays=[4],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (2, 1, morning.id),
        (4, 0, night.id),
        (4, 1, morning.id),
        (5, 0, untouched.id),
        (5, 17, night.id),
    )


def test_copying_sunday_to_monday_uses_the_week_ring_and_keeps_tuesday(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-sunday-ring")
    monday = create_mode(session, "copy-ring-monday", "Monday")
    sunday = create_mode(session, "copy-ring-sunday", "Sunday")
    late = create_mode(session, "copy-ring-late", "Late")
    tuesday = create_mode(session, "copy-ring-tuesday", "Tuesday")
    _stored_point(session, zone.id, 1, 0, monday.id)
    _stored_point(session, zone.id, 2, 0, tuesday.id)
    _stored_point(session, zone.id, 7, 0, sunday.id)
    _stored_point(session, zone.id, 7, 1439, late.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=7,
        target_weekdays=[1],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (1, 0, sunday.id),
        (1, 1439, late.id),
        (2, 0, tuesday.id),
        (7, 0, sunday.id),
        (7, 1439, late.id),
    )


def test_copying_to_consecutive_days_does_not_restore_between_them(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-consecutive")
    source_mode = create_mode(session, "copy-consecutive-source", "Source")
    old_thursday = create_mode(session, "copy-consecutive-old", "Old")
    following = create_mode(session, "copy-consecutive-following", "Following")
    _stored_point(session, zone.id, 2, 0, source_mode.id)
    _stored_point(session, zone.id, 3, 0, old_thursday.id)
    _stored_point(session, zone.id, 4, 0, old_thursday.id)
    _stored_point(session, zone.id, 5, 0, following.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=2,
        target_weekdays=[3, 4],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (2, 0, source_mode.id),
        (5, 0, following.id),
    )


def test_copying_to_an_odd_day_restores_the_untouched_following_day(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-odd-target")
    source_mode = create_mode(session, "copy-odd-source", "Source")
    old_target = create_mode(session, "copy-odd-old", "Old target")
    following = create_mode(session, "copy-odd-following", "Following")
    _stored_point(session, zone.id, 2, 0, source_mode.id)
    _stored_point(session, zone.id, 3, 0, old_target.id)
    _stored_point(session, zone.id, 4, 0, following.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=2,
        target_weekdays=[3],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (2, 0, source_mode.id),
        (4, 0, following.id),
    )


def test_copying_to_an_odd_day_restores_a_carried_following_mode(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-odd-carried-mode")
    source_mode = create_mode(session, "copy-odd-carried-source", "Source")
    carried = create_mode(session, "copy-odd-carried-old", "Carried")
    later = create_mode(session, "copy-odd-carried-later", "Later")
    _stored_point(session, zone.id, 1, 0, source_mode.id)
    _stored_point(session, zone.id, 2, 0, carried.id)
    _stored_point(session, zone.id, 5, 0, later.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=1,
        target_weekdays=[3],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (1, 0, source_mode.id),
        (2, 0, carried.id),
        (3, 0, source_mode.id),
        (4, 0, carried.id),
        (5, 0, later.id),
    )


def test_copying_removes_a_target_switch_at_the_last_minute_of_day(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-last-minute")
    source_mode = create_mode(session, "copy-last-source", "Source")
    obsolete = create_mode(session, "copy-last-obsolete", "Obsolete")
    following = create_mode(session, "copy-last-following", "Following")
    _stored_point(session, zone.id, 1, 0, source_mode.id)
    _stored_point(session, zone.id, 2, 1439, obsolete.id)
    _stored_point(session, zone.id, 3, 0, following.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=1,
        target_weekdays=[2],
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (1, 0, source_mode.id),
        (3, 0, following.id),
    )


@pytest.mark.parametrize(
    ("source_weekday", "target_weekdays"),
    [(0, [1]), (8, [1]), (1, [0]), (1, [8])],
)
def test_copying_rejects_each_weekday_just_outside_the_calendar(
    session: Session, source_weekday: int, target_weekdays: list[int]
) -> None:
    create_settings(session)
    zone = create_zone(session, f"copy-invalid-{source_weekday}-{target_weekdays[0]}")
    with pytest.raises(ScheduleError, match="Wochentag"):
        copy_schedule_day(
            session,
            zone,
            source_weekday=source_weekday,
            target_weekdays=target_weekdays,
            user_id=None,
        )


def test_painting_until_sunday_midnight_preserves_monday_mode(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "sunday-midnight-ring")
    monday = create_mode(session, "monday-ring", "Montag")
    sunday = create_mode(session, "sunday-ring", "Sonntag")
    painted = create_mode(session, "painted-ring", "Gemalt")
    _stored_point(session, zone.id, 1, 0, monday.id)
    _stored_point(session, zone.id, 7, 1320, sunday.id)

    paint_schedule_interval(
        session,
        zone,
        weekday=7,
        start_minute=1380,
        end_minute=1440,
        mode_id=painted.id,
        user_id=None,
    )

    assert _stored_snapshot(session, zone.id) == (
        (1, 0, monday.id),
        (7, 1320, sunday.id),
        (7, 1380, painted.id),
    )


def test_copying_tuesday_to_all_days_replaces_monday_midnight(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "copy-tuesday-ring")
    monday = create_mode(session, "copy-old-monday", "A")
    morning = create_mode(session, "copy-tuesday-morning", "B")
    evening = create_mode(session, "copy-tuesday-evening", "C")
    _stored_point(session, zone.id, 1, 0, monday.id)
    _stored_point(session, zone.id, 2, 0, morning.id)
    _stored_point(session, zone.id, 2, 600, evening.id)

    copy_schedule_day(
        session,
        zone,
        source_weekday=2,
        target_weekdays=list(range(1, 8)),
        user_id=None,
    )

    snapshot = _stored_snapshot(session, zone.id)
    assert (1, 0, morning.id) in snapshot
    assert (1, 600, evening.id) in snapshot
    assert (1, 0, monday.id) not in snapshot


def test_undo_rejects_an_aba_sequence(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "undo-aba")
    mode_a = create_mode(session, "undo-aba-a", "A")
    mode_b = create_mode(session, "undo-aba-b", "B")
    first = paint_schedule_interval(
        session,
        zone,
        weekday=1,
        start_minute=360,
        end_minute=480,
        mode_id=mode_a.id,
        user_id=None,
    )
    assert first is not None
    before, after, revision = first
    paint_schedule_interval(
        session,
        zone,
        weekday=1,
        start_minute=360,
        end_minute=480,
        mode_id=mode_b.id,
        user_id=None,
    )
    paint_schedule_interval(
        session,
        zone,
        weekday=1,
        start_minute=360,
        end_minute=480,
        mode_id=mode_a.id,
        user_id=None,
    )
    assert _stored_snapshot(session, zone.id) == after

    with pytest.raises(ScheduleError, match="inzwischen"):
        undo_schedule_gesture(
            session,
            zone,
            before=before,
            expected_after=after,
            expected_revision=revision,
            user_id=None,
        )


def test_undo_rejects_an_aba_sequence_from_point_mode_edits(session: Session) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "undo-point-aba")
    mode_a = create_mode(session, "undo-point-aba-a", "A")
    mode_b = create_mode(session, "undo-point-aba-b", "B")
    gesture = paint_schedule_interval(
        session,
        zone,
        weekday=1,
        start_minute=360,
        end_minute=480,
        mode_id=mode_a.id,
        user_id=None,
    )
    assert gesture is not None
    before, after, revision = gesture
    point = session.scalar(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id,
            SchedulePoint.weekday == 1,
            SchedulePoint.minute_of_day == 360,
        )
    )
    assert point is not None
    change_schedule_point_mode(
        session, zone, point, mode_id=mode_b.id, user_id=None
    )
    change_schedule_point_mode(
        session, zone, point, mode_id=mode_a.id, user_id=None
    )
    assert _stored_snapshot(session, zone.id) == after

    with pytest.raises(ScheduleError, match="inzwischen"):
        undo_schedule_gesture(
            session,
            zone,
            before=before,
            expected_after=after,
            expected_revision=revision,
            user_id=None,
        )


def test_undo_rejects_legacy_point_edits_made_before_an_upgrade(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "undo-legacy-point-aba")
    mode_a = create_mode(session, "undo-legacy-point-a", "A")
    mode_b = create_mode(session, "undo-legacy-point-b", "B")
    gesture = paint_schedule_interval(
        session, zone, weekday=1, start_minute=360, end_minute=480,
        mode_id=mode_a.id, user_id=None,
    )
    assert gesture is not None
    before, after, revision = gesture
    point = session.scalar(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id,
            SchedulePoint.weekday == 1,
            SchedulePoint.minute_of_day == 360,
        )
    )
    assert point is not None
    change_schedule_point_mode(session, zone, point, mode_id=mode_b.id, user_id=None)
    change_schedule_point_mode(session, zone, point, mode_id=mode_a.id, user_id=None)
    for event in session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "schedule_point")
    ):
        event.object_id = str(point.id)
    session.flush()

    with pytest.raises(ScheduleError, match="inzwischen"):
        undo_schedule_gesture(
            session, zone, before=before, expected_after=after,
            expected_revision=revision, user_id=None,
        )


def test_painting_the_ring_mode_with_only_one_weekly_point_is_a_no_op(
    session: Session,
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, "one-point-ring")
    comfort = create_mode(session, "one-point-comfort", "Komfort")
    _stored_point(session, zone.id, 3, 720, comfort.id)
    before = _stored_snapshot(session, zone.id)

    result = paint_schedule_interval(
        session,
        zone,
        weekday=1,
        start_minute=360,
        end_minute=480,
        mode_id=comfort.id,
        user_id=None,
    )

    assert result is None
    assert _stored_snapshot(session, zone.id) == before


def test_moment_taken_matches_all_three_coordinates(session: Session) -> None:
    create_settings(session)
    first_zone = create_zone(session, "moment-first-zone")
    second_zone = create_zone(session, "moment-second-zone")
    mode = create_mode(session, "moment-mode")
    _stored_point(session, first_zone.id, 7, 1439, mode.id)
    _stored_point(session, second_zone.id, 6, 1438, mode.id)

    assert schedule_module._moment_taken(session, first_zone.id, 7, 1439)
    assert not schedule_module._moment_taken(session, first_zone.id, 6, 1439)
    assert not schedule_module._moment_taken(session, first_zone.id, 7, 1438)
    assert not schedule_module._moment_taken(session, second_zone.id, 7, 1439)


@pytest.mark.parametrize("weekday", [1, 7])
@pytest.mark.parametrize("minute", [0, 1439])
def test_creating_a_point_accepts_every_calendar_edge(
    session: Session, weekday: int, minute: int
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, f"create-edge-{weekday}-{minute}")
    mode = create_mode(session, f"create-edge-mode-{weekday}-{minute}")

    created = create_schedule_point(
        session,
        zone,
        weekday=weekday,
        minute=minute,
        mode_id=mode.id,
        user_id=None,
    )

    assert (created.weekday, created.minute_of_day) == (weekday, minute)


@pytest.mark.parametrize(("weekday", "minute"), [(0, 100), (8, 100), (2, -1), (2, 1440)])
def test_creating_a_point_rejects_each_coordinate_just_outside_the_calendar(
    session: Session, weekday: int, minute: int
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, f"create-invalid-{weekday}-{minute}")
    mode = create_mode(session, f"create-invalid-mode-{weekday}-{minute}")

    with pytest.raises(ScheduleError, match="Wochentag|Uhrzeit"):
        create_schedule_point(
            session,
            zone,
            weekday=weekday,
            minute=minute,
            mode_id=mode.id,
            user_id=None,
        )


@pytest.mark.parametrize(("weekday", "minute"), [(0, 100), (8, 100), (2, -1), (2, 1440)])
def test_moving_a_point_rejects_each_coordinate_just_outside_the_calendar(
    session: Session, weekday: int, minute: int
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, f"move-invalid-{weekday}-{minute}")
    mode = create_mode(session, f"move-invalid-mode-{weekday}-{minute}")
    existing = SchedulePoint(
        zone_id=zone.id, weekday=2, minute_of_day=100, setpoint_mode_id=mode.id
    )
    session.add(existing)
    session.flush()

    with pytest.raises(ScheduleError, match="Wochentag|Uhrzeit"):
        move_schedule_point(
            session,
            zone,
            existing,
            weekday=weekday,
            minute=minute,
            user_id=None,
        )


@pytest.mark.parametrize(("weekday", "minute"), [(1, 0), (7, 1439)])
def test_moving_a_point_accepts_every_calendar_edge(
    session: Session, weekday: int, minute: int
) -> None:
    create_settings(session)
    source(session)
    zone = create_zone(session, f"move-edge-{weekday}-{minute}")
    mode = create_mode(session, f"move-edge-mode-{weekday}-{minute}")
    existing = SchedulePoint(
        zone_id=zone.id, weekday=4, minute_of_day=700, setpoint_mode_id=mode.id
    )
    session.add(existing)
    session.flush()

    moved = move_schedule_point(
        session,
        zone,
        existing,
        weekday=weekday,
        minute=minute,
        user_id=None,
    )

    assert (moved.weekday, moved.minute_of_day) == (weekday, minute)


def test_a_point_holds_until_the_next_one() -> None:
    points = [point(1, 360, "tag"), point(1, 1380, "nacht")]  # Mon 06:00 and 23:00
    monday_ten = datetime(2026, 8, 31, 10, 0)
    assert current_point(points, monday_ten).minute_of_day == 360


def test_before_the_first_point_the_last_one_of_the_week_applies() -> None:
    """The Sunday-evening point holds until Monday morning — the week wraps around."""
    points = [point(1, 360, "tag"), point(7, 1320, "nacht")]  # Mon 06:00, Sun 22:00
    monday_three = datetime(2026, 8, 31, 3, 0)
    current = current_point(points, monday_three)
    assert current.weekday == 7 and current.minute_of_day == 1320


def test_without_any_points_there_is_no_current_one() -> None:
    assert current_point([], datetime(2026, 8, 31, 10, 0)) is None


def test_a_point_exactly_at_its_switch_minute_already_applies() -> None:
    points = [point(1, 360, "tag")]
    assert current_point(points, datetime(2026, 8, 31, 6, 0)) is not None


def test_the_next_point_lies_in_the_future() -> None:
    points = [point(1, 360, "tag"), point(1, 1380, "nacht")]
    next_one = next_point(points, datetime(2026, 8, 31, 10, 0))
    assert next_one == datetime(2026, 8, 31, 23, 0)


def test_without_a_schedule_frost_protection_applies(session: Session) -> None:
    zone = zone_with_schedule(session, "leer", points=[], frost_protection=Decimal("16.0"))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")
    assert "Frostschutz" in result.reason


def test_configured_frost_temperature_and_mode_identity_are_preserved(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session, "frost-identity", points=[], frost_protection=Decimal("15.5")
    )

    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))

    assert frost_protection_temperature(session, zone) == Decimal("15.5")
    assert result.temperature_c == Decimal("15.5")
    assert result.mode_code == "frost-frost-identity"
    assert result.mode_id is not None


def test_operating_mode_off_results_in_frost_protection(session: Session) -> None:
    zone = zone_with_schedule(session, "aus", points=[(1, 360, "tag", Decimal("21.0"))],
                             operating_mode="off", frost_protection=Decimal("16.0"))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")


def test_an_override_beats_the_schedule(session: Session) -> None:
    zone = zone_with_schedule(session, "über", points=[(1, 360, "tag", Decimal("21.0"))],
                             override=(Decimal("23.5"), None))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("23.5")
    assert "Übersteuerung" in result.reason


def test_an_expired_override_no_longer_applies(session: Session) -> None:
    zone = zone_with_schedule(
        session, "abgelaufen", points=[(1, 360, "tag", Decimal("21.0"))],
        override=(Decimal("23.5"), datetime(2026, 8, 31, 9, 0)),
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("21.0")


def test_an_override_expires_exactly_at_its_end(session: Session) -> None:
    now = datetime(2026, 8, 31, 10, 0)
    zone = zone_with_schedule(
        session,
        "expires-exactly",
        points=[(1, 360, "tag", Decimal("21.0"))],
        override=(Decimal("23.5"), now),
    )

    result = resolved_setpoint(session, zone, now)

    assert result.temperature_c == Decimal("21.0")
    assert result.mode_code == "tag"


def test_the_reason_names_the_decision(session: Session) -> None:
    """Principle 5: traceable, why this setpoint applies."""
    zone = zone_with_schedule(session, "grund", points=[(1, 360, "tag", Decimal("21.0"))])
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert "Tag" in result.reason and "06:00" in result.reason


def test_schedule_reason_preserves_non_round_switch_minutes(session: Session) -> None:
    zone = zone_with_schedule(
        session, "reason-minute", points=[(1, 119, "tag", Decimal("21.0"))]
    )

    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 3, 0))

    assert "01:59" in result.reason


def test_schedule_mode_without_a_zone_temperature_falls_back_to_frost(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "missing-mode-temperature",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("15.5"),
    )
    tag_mode = session.query(SetpointMode).filter_by(code="tag").one()
    session.query(schedule_module.ZoneSetpoint).filter_by(
        zone_id=zone.id, setpoint_mode_id=tag_mode.id
    ).delete()
    session.flush()

    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))

    assert result.temperature_c == Decimal("15.5")
    assert result.mode_code == "frost-missing-mode-temperature"


def test_there_is_no_next_point_without_any_points() -> None:
    assert next_point([], datetime(2026, 8, 31, 10, 0)) is None


def test_an_override_with_an_unknown_source_fails(session: Session) -> None:
    """An override with no source would be one where nobody could say afterward
    how it was set -- that should fail loudly.

    This used to hard-code the source 'api', even when the override came from
    the interface; the test therefore now checks the rejection of an
    *unknown* name instead of the absence of exactly one lookup row.
    """
    zone = create_zone(session, "ohne-quelle")
    with pytest.raises(ValueError, match="rauchzeichen"):
        create_override(
            session, zone, Decimal("20.0"), None, source="rauchzeichen"
        )


def test_an_override_remembers_its_adapter(session: Session) -> None:
    """Counter-check: the three adapters must remain distinguishable, otherwise
    `zone_override.source_id` answers the question 'what was this set through'
    wrongly for two out of three -- exactly the state this change fixes."""
    zone = create_zone(session, "adapterzone")
    from_web = create_override(session, zone, Decimal("21.0"), None)
    from_mcp = create_override(session, zone, Decimal("22.0"), None, source="mcp")
    assert from_web.source_id != from_mcp.source_id


def test_creating_an_override_creates_a_new_override(session: Session) -> None:
    zone = create_zone(session, "mit-quelle")
    source(session, "api")
    entry = create_override(
        session, zone, Decimal("22.5"), None, user_id=None, token_id=None
    )
    assert entry.zone_id == zone.id
    assert entry.temperature_c == Decimal("22.5")
    assert entry.id is not None


def test_cancelling_an_override_ends_the_active_one(session: Session) -> None:
    zone = zone_with_schedule(
        session, "aufheben", points=[], override=(Decimal("23.0"), None)
    )
    entry = cancel_override(session, zone)
    assert entry is not None
    assert entry.cancelled_at is not None


def test_cancelling_without_an_active_override_returns_none(session: Session) -> None:
    zone = create_zone(session, "keine-über")
    assert cancel_override(session, zone) is None


def test_an_override_on_a_mode_without_a_fixed_temperature(session: Session) -> None:
    """An override can point to a mode instead of a fixed temperature —
    the setpoint then comes from the zone's temperature for that mode."""
    zone = zone_with_schedule(session, "modus-über", points=[(1, 360, "tag", Decimal("21.0"))])
    tag_mode = session.query(SetpointMode).filter_by(code="tag").one()
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            setpoint_mode_id=tag_mode.id,
            starts_at=datetime(2026, 8, 31, 0, 0),
            ends_at=None,
            source_id=source(session).id,
        )
    )
    session.flush()
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("21.0")
    assert "Modus tag" in result.reason


# --- schedule_forecast -------------------------------------------------------------
#
# The preview's whole point is to never show something that will not actually
# happen -- so every one of these reuses `resolved_setpoint`'s own precedence
# instead of asserting an independently reasoned expectation. Several exact
# boundaries below (in particular the two daylight-saving tests) were derived by
# running `current_point`/`next_point`/the local<->UTC conversion by hand against
# these exact fixtures before writing the assertion, not the other way around --
# see the task's own worktree notes for that derivation.


def test_operating_mode_off_yields_one_unchanging_bar_for_the_whole_horizon(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session, "vorschau-aus", points=[(1, 360, "tag-vorschau-aus", Decimal("21.0"))],
        operating_mode="off", frost_protection=Decimal("16.0"),
    )
    now = datetime(2026, 8, 31, 10, 0)

    result = schedule_forecast(session, zone, now)

    assert len(result) == 1
    assert result[0].starts_at == now
    assert result[0].ends_at == now + timedelta(hours=24)
    assert result[0].setpoint.temperature_c == Decimal("16.0")
    assert "Aus" in result[0].setpoint.reason


def test_a_permanent_override_covers_the_entire_horizon(session: Session) -> None:
    zone = zone_with_schedule(
        session, "vorschau-dauerhaft",
        points=[(1, 360, "tag-vorschau-dauerhaft", Decimal("21.0"))],
        override=(Decimal("23.5"), None),
    )
    now = datetime(2026, 8, 31, 10, 0)

    result = schedule_forecast(session, zone, now)

    assert len(result) == 1
    assert result[0].ends_at == now + timedelta(hours=24)
    assert result[0].setpoint.temperature_c == Decimal("23.5")


def test_an_override_ending_mid_horizon_hands_over_to_the_schedule(
    session: Session,
) -> None:
    now = datetime(2026, 8, 31, 10, 0)
    override_end = datetime(2026, 8, 31, 13, 0)
    zone = zone_with_schedule(
        session, "vorschau-uebergabe",
        points=[(1, 360, "tag-vorschau-uebergabe", Decimal("21.0"))],
        override=(Decimal("23.5"), override_end),
    )

    result = schedule_forecast(session, zone, now)

    assert len(result) == 2
    assert (result[0].starts_at, result[0].ends_at) == (now, override_end)
    assert result[0].setpoint.temperature_c == Decimal("23.5")
    assert (result[1].starts_at, result[1].ends_at) == (
        override_end, now + timedelta(hours=24)
    )
    assert result[1].setpoint.temperature_c == Decimal("21.0")


def test_an_override_on_a_mode_without_a_usable_temperature_is_skipped_in_the_forecast(
    session: Session,
) -> None:
    """Parity with `resolved_setpoint`: an override that cannot resolve to a
    temperature does not count, so the forecast must not draw a bar for it either
    -- it must fall straight through to the schedule, from `now` on."""
    now = datetime(2026, 8, 31, 10, 0)
    zone = zone_with_schedule(
        session, "vorschau-ohne-temp",
        points=[(1, 360, "tag-vorschau-ohne-temp", Decimal("21.0"))],
    )
    ohne_temperatur = create_mode(session, "leer-vorschau-ohne-temp")
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            setpoint_mode_id=ohne_temperatur.id,
            starts_at=now,
            ends_at=None,
            source_id=source(session).id,
        )
    )
    session.flush()

    result = schedule_forecast(session, zone, now)

    assert result[0].starts_at == now
    assert result[0].setpoint.temperature_c == Decimal("21.0")
    assert "Zeitplan" in result[0].setpoint.reason


def test_a_zone_without_any_schedule_stays_at_frost_protection_for_the_whole_horizon(
    session: Session,
) -> None:
    now = datetime(2026, 8, 31, 10, 0)
    zone = zone_with_schedule(
        session, "vorschau-ohne-plan", points=[], frost_protection=Decimal("16.0")
    )

    result = schedule_forecast(session, zone, now)

    assert len(result) == 1
    assert (result[0].starts_at, result[0].ends_at) == (now, now + timedelta(hours=24))
    assert result[0].setpoint.temperature_c == Decimal("16.0")
    assert "Frostschutz" in result[0].setpoint.reason


def test_the_forecast_crosses_midnight_into_the_next_weekday(session: Session) -> None:
    """Derived by hand against `current_point`/`next_point` beforehand: at local
    Monday 20:00 the ring's last switch is last week's Tuesday 06:00 ('morgen'),
    so the first bar is 'morgen', not 'abend' -- the exact crossing this test
    exists to pin down."""
    now = datetime(2026, 8, 31, 18, 0)  # local (Europe/Berlin, CEST) Mon 20:00
    zone = zone_with_schedule(
        session, "vorschau-mitternacht",
        points=[
            (1, 1320, "abend-vorschau-mitternacht", Decimal("18.0")),
            (2, 360, "morgen-vorschau-mitternacht", Decimal("21.0")),
        ],
    )

    result = schedule_forecast(session, zone, now)

    assert len(result) == 3
    assert (result[0].starts_at, result[0].ends_at) == (
        now, datetime(2026, 8, 31, 20, 0)
    )
    assert result[0].setpoint.temperature_c == Decimal("21.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        datetime(2026, 8, 31, 20, 0), datetime(2026, 9, 1, 4, 0)
    )
    assert result[1].setpoint.temperature_c == Decimal("18.0")
    assert (result[2].starts_at, result[2].ends_at) == (
        datetime(2026, 9, 1, 4, 0), now + timedelta(hours=24)
    )
    assert result[2].setpoint.temperature_c == Decimal("21.0")


def test_adjacent_bars_resolving_to_the_same_setpoint_are_merged(session: Session) -> None:
    """Two schedule points can carry the same mode back to back -- nothing in
    `create_schedule_point` forbids it, unlike painting, which already collapses
    that case. The preview must not show a seam where nothing actually changes."""
    now = datetime(2026, 8, 30, 22, 0)  # local (CEST) Mon 00:00
    zone = zone_with_schedule(
        session, "vorschau-verschmelzen",
        points=[
            (1, 0, "a-vorschau-verschmelzen", Decimal("20.0")),
            (1, 720, "b-vorschau-verschmelzen", Decimal("15.0")),
        ],
    )
    # A second point carrying the *same* mode as the first -- added directly rather
    # than through `zone_with_schedule` a second time, which would try to insert a
    # second `ZoneSetpoint` row for the same (zone, mode) and collide with the
    # unique constraint. Nothing forbids two schedule points sharing a mode; only
    # painting happens to collapse that case, and this point is created by hand.
    a_mode = session.query(SetpointMode).filter_by(code="a-vorschau-verschmelzen").one()
    _stored_point(session, zone.id, 1, 360, a_mode.id)

    result = schedule_forecast(session, zone, now)

    assert len(result) == 2
    assert (result[0].starts_at, result[0].ends_at) == (
        now, datetime(2026, 8, 31, 10, 0)
    )
    assert result[0].setpoint.temperature_c == Decimal("20.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        datetime(2026, 8, 31, 10, 0), now + timedelta(hours=24)
    )
    assert result[1].setpoint.temperature_c == Decimal("15.0")


def test_the_forecast_survives_the_spring_forward_gap(session: Session) -> None:
    """2026-03-29 is the day Europe/Berlin's clocks jump from 02:00 CET straight to
    03:00 CEST -- the local day has 23 hours, and the wall clock never shows
    02:00-03:00 at all. The middle bar below reads as a 3-hour local span
    (01:00 to 04:00) but must be exactly 2 real hours in UTC -- the whole point of
    working in UTC instants instead of local-clock arithmetic."""
    now = datetime(2026, 3, 28, 23, 30)  # local Sun 00:30 CET, before the jump
    zone = zone_with_schedule(
        session, "vorschau-dst-vor",
        points=[
            (7, 60, "nacht-vorschau-dst-vor", Decimal("16.0")),
            (7, 240, "tag-vorschau-dst-vor", Decimal("21.0")),
        ],
    )

    result = schedule_forecast(session, zone, now)

    assert len(result) == 3
    assert (result[0].starts_at, result[0].ends_at) == (
        now, datetime(2026, 3, 29, 0, 0)
    )
    assert result[0].setpoint.temperature_c == Decimal("21.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        datetime(2026, 3, 29, 0, 0), datetime(2026, 3, 29, 2, 0)
    )
    assert result[1].setpoint.temperature_c == Decimal("16.0")
    assert (result[1].ends_at - result[1].starts_at) == timedelta(hours=2)
    assert (result[2].starts_at, result[2].ends_at) == (
        datetime(2026, 3, 29, 2, 0), now + timedelta(hours=24)
    )
    assert result[2].setpoint.temperature_c == Decimal("21.0")
    assert sum(
        (segment.ends_at - segment.starts_at for segment in result), timedelta()
    ) == timedelta(hours=24)


def test_the_forecast_survives_the_autumn_fold(session: Session) -> None:
    """2026-10-25 is the day Europe/Berlin's clocks fall back from 03:00 CEST to
    02:00 CET -- the local day has 25 hours, and 02:00-03:00 happens twice. The
    middle bar below reads as a 2-hour local span (01:30 to 03:30) but must be
    exactly 3 real hours in UTC, because the repeated hour actually elapsed."""
    now = datetime(2026, 10, 24, 22, 0)  # local Sun 00:00 CEST, before the fold
    zone = zone_with_schedule(
        session, "vorschau-dst-zurueck",
        points=[
            (7, 90, "nacht-vorschau-dst-zurueck", Decimal("16.0")),
            (7, 210, "tag-vorschau-dst-zurueck", Decimal("21.0")),
        ],
    )

    result = schedule_forecast(session, zone, now)

    assert len(result) == 3
    assert (result[0].starts_at, result[0].ends_at) == (
        now, datetime(2026, 10, 24, 23, 30)
    )
    assert result[0].setpoint.temperature_c == Decimal("21.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        datetime(2026, 10, 24, 23, 30), datetime(2026, 10, 25, 2, 30)
    )
    assert result[1].setpoint.temperature_c == Decimal("16.0")
    assert (result[1].ends_at - result[1].starts_at) == timedelta(hours=3)
    assert (result[2].starts_at, result[2].ends_at) == (
        datetime(2026, 10, 25, 2, 30), now + timedelta(hours=24)
    )
    assert result[2].setpoint.temperature_c == Decimal("21.0")
    assert sum(
        (segment.ends_at - segment.starts_at for segment in result), timedelta()
    ) == timedelta(hours=24)


# --- Urlaubsbetrieb -----------------------------------------------------------------


def test_create_vacation_converts_local_calendar_dates_through_the_configured_timezone(
    session: Session,
) -> None:
    zone_with_schedule(session, "urlaub-zeitzone", points=[])
    vacation = create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 1),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    # 1 August 2026 is CEST (UTC+2): local midnight is 22:00 UTC the day before.
    assert vacation.starts_at == datetime(2026, 7, 31, 22, 0)
    assert vacation.ends_at == datetime(2026, 8, 1, 22, 0)


def test_create_vacation_rejects_an_end_before_the_start(session: Session) -> None:
    zone_with_schedule(session, "urlaub-verkehrt", points=[])
    with pytest.raises(ScheduleError, match="Ende"):
        create_vacation(
            session,
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 1),
            setback_temperature_c=Decimal("15.0"),
            timezone_name="Europe/Berlin",
        )


def test_create_vacation_rejects_a_temperature_outside_the_bound(session: Session) -> None:
    zone_with_schedule(session, "urlaub-grenzwert", points=[])
    with pytest.raises(DomainError):
        create_vacation(
            session,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            setback_temperature_c=Decimal("99.0"),
            timezone_name="Europe/Berlin",
        )


def test_create_vacation_refuses_a_second_overlapping_one(session: Session) -> None:
    zone_with_schedule(session, "urlaub-doppelt", points=[])
    now = datetime(2026, 8, 1, 0, 0)
    create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 10),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
        now=now,
    )
    with pytest.raises(ScheduleError, match="laufend|geplant"):
        create_vacation(
            session,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            setback_temperature_c=Decimal("15.0"),
            timezone_name="Europe/Berlin",
            now=now,
        )


def test_create_vacation_with_an_unknown_source_fails(session: Session) -> None:
    """Same reasoning as `test_an_override_with_an_unknown_source_fails`: a vacation
    with no source would be one where nobody could say afterward how it was set."""
    zone_with_schedule(session, "urlaub-ohne-quelle", points=[])
    with pytest.raises(ValueError, match="rauchzeichen"):
        create_vacation(
            session,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            setback_temperature_c=Decimal("15.0"),
            timezone_name="Europe/Berlin",
            source="rauchzeichen",
        )


def test_cancel_vacation_ends_the_running_one(session: Session) -> None:
    zone_with_schedule(session, "urlaub-abbrechen", points=[])
    now = datetime(2026, 8, 1, 0, 0)
    create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 10),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
        now=now,
    )
    cancelled = cancel_vacation(session, now=now)
    assert cancelled is not None
    assert cancelled.cancelled_at is not None
    assert current_or_upcoming_vacation(session, cancelled.cancelled_at) is None


def test_cancel_vacation_ends_one_that_has_not_started_yet(session: Session) -> None:
    """Vorzeitig beenden ist auch vor dem Beginn möglich -- der einzige Zeitpunkt,
    an dem 'vorzeitig' hier überhaupt Sinn ergibt, ist ausdrücklich erlaubt."""
    zone_with_schedule(session, "urlaub-vorher-abbrechen", points=[])
    vacation = create_vacation(
        session,
        start_date=date(2030, 1, 1),
        end_date=date(2030, 1, 10),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    now = datetime(2026, 8, 1, 0, 0)
    cancelled = cancel_vacation(session, now=now)
    assert cancelled is not None
    assert cancelled.id == vacation.id
    assert running_vacation(session, vacation.starts_at) is None


def test_cancel_vacation_without_one_returns_none(session: Session) -> None:
    assert cancel_vacation(session) is None


def test_running_vacation_matches_only_within_its_window(session: Session) -> None:
    zone_with_schedule(session, "urlaub-fenster", points=[])
    vacation = create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 5),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    assert running_vacation(session, vacation.starts_at - timedelta(seconds=1)) is None
    assert running_vacation(session, vacation.starts_at) is not None
    assert running_vacation(session, vacation.ends_at - timedelta(seconds=1)) is not None
    assert running_vacation(session, vacation.ends_at) is None


def test_current_or_upcoming_vacation_also_matches_a_future_one(session: Session) -> None:
    zone_with_schedule(session, "urlaub-geplant", points=[])
    vacation = create_vacation(
        session,
        start_date=date(2030, 1, 1),
        end_date=date(2030, 1, 5),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    now = datetime(2026, 8, 1, 0, 0)
    assert running_vacation(session, now) is None
    found = current_or_upcoming_vacation(session, now)
    assert found is not None
    assert found.id == vacation.id


def test_a_vacation_beats_the_schedule(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "urlaub-vs-zeitplan",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("10.0"),
    )
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("15.0")
    assert "Urlaub" in result.reason
    assert result.mode_id is None


def test_a_vacation_setback_below_frost_protection_is_raised_to_it(
    session: Session,
) -> None:
    """Reproduces the review finding: a vacation setback entered for the whole plant
    must not push a zone below its own frost-protection floor -- the same absolute
    floor `solar_setback.apply` enforces for its own correction."""
    zone = zone_with_schedule(
        session,
        "urlaub-unter-frostschutz",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("16.0"),
    )
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("5.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")
    assert "Urlaub" in result.reason
    assert "Frostschutz" in result.reason
    assert result.mode_id is None


def test_a_vacation_setback_above_frost_protection_is_passed_through_unchanged(
    session: Session,
) -> None:
    """The normal case: the entered setback stays above frost protection and is
    handed through exactly as entered, with the plain reason text -- the frost-floor
    wording must not appear where the floor never had to act."""
    zone = zone_with_schedule(
        session,
        "urlaub-ueber-frostschutz",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("16.0"),
    )
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("18.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("18.0")
    assert result.reason == "Urlaubsbetrieb — Absenkung"
    assert result.mode_id is None


def test_schedule_forecast_also_raises_a_vacation_setback_to_frost_protection(
    session: Session,
) -> None:
    """`schedule_forecast` resolves every bar through `resolved_setpoint`, so the
    same frost floor must show up there too -- confirmed here instead of assumed."""
    zone = zone_with_schedule(
        session,
        "urlaub-vorschau-frost",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("16.0"),
    )
    now = datetime(2026, 8, 31, 10, 0)
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 5),
        setback_temperature_c=Decimal("5.0"),
        timezone_name="Europe/Berlin",
    )
    result = schedule_forecast(session, zone, now)
    assert result
    assert all(segment.setpoint.temperature_c == Decimal("16.0") for segment in result)
    assert all("Frostschutz" in segment.setpoint.reason for segment in result)


def test_a_running_override_beats_a_vacation(session: Session) -> None:
    """A hand-set override that is already running must not silently disappear
    once a vacation starts."""
    zone = zone_with_schedule(
        session,
        "urlaub-vs-uebersteuerung",
        points=[(1, 360, "tag", Decimal("21.0"))],
        override=(Decimal("23.5"), None),
    )
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("23.5")


def test_a_zone_switched_off_stays_off_during_a_vacation(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "urlaub-aus",
        points=[(1, 360, "tag", Decimal("21.0"))],
        operating_mode="off",
        frost_protection=Decimal("16.0"),
    )
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")


def test_a_vacation_starting_in_the_future_does_not_yet_apply(session: Session) -> None:
    zone = zone_with_schedule(
        session, "urlaub-zukunft", points=[(1, 360, "tag", Decimal("21.0"))]
    )
    create_vacation(
        session,
        start_date=date(2030, 1, 1),
        end_date=date(2030, 1, 10),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("21.0")


def test_a_vacation_that_has_ended_stops_applying_however_late_it_is_checked(
    session: Session,
) -> None:
    """The service was off for a while and only resumes long after the vacation's
    own end -- there is no separate 'close the vacation' step to have missed:
    `resolved_setpoint` recomputes the answer fresh from the stored window every
    time, exactly as it already does for an ordinary override."""
    zone = zone_with_schedule(
        session, "urlaub-verpasst", points=[(1, 360, "tag", Decimal("21.0"))]
    )
    vacation = create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 5),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    much_later = vacation.ends_at + timedelta(days=30)
    result = resolved_setpoint(session, zone, much_later)
    assert result.temperature_c == Decimal("21.0")


def test_create_vacation_across_the_spring_forward_gap_uses_real_utc_instants(
    session: Session,
) -> None:
    """2026-03-29 is the day Europe/Berlin's clocks jump from 02:00 CET to 03:00
    CEST -- the local day has only 23 hours. A vacation spanning it must still end
    at the correct UTC instant, not 72 real hours after its start."""
    zone_with_schedule(session, "urlaub-sommerzeit", points=[])
    vacation = create_vacation(
        session,
        start_date=date(2026, 3, 28),
        end_date=date(2026, 3, 30),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    # 28 March 00:00 CET -> 27 March 23:00 UTC; 31 March 00:00 CEST -> 30 March 22:00 UTC.
    assert vacation.starts_at == datetime(2026, 3, 27, 23, 0)
    assert vacation.ends_at == datetime(2026, 3, 30, 22, 0)
    assert vacation.ends_at - vacation.starts_at == timedelta(hours=71)


def test_schedule_forecast_shows_a_vacation_starting_mid_horizon(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "urlaub-vorschau-beginn",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("10.0"),
    )
    now = datetime(2026, 8, 31, 10, 0)
    vacation = create_vacation(
        session,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    assert now < vacation.starts_at < now + timedelta(hours=24)

    result = schedule_forecast(session, zone, now)

    assert len(result) == 2
    assert (result[0].starts_at, result[0].ends_at) == (now, vacation.starts_at)
    assert result[0].setpoint.temperature_c == Decimal("21.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        vacation.starts_at, now + timedelta(hours=24)
    )
    assert result[1].setpoint.temperature_c == Decimal("15.0")


def test_schedule_forecast_shows_a_vacation_ending_mid_horizon(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "urlaub-vorschau-ende",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("10.0"),
    )
    now = datetime(2026, 8, 31, 10, 0)
    vacation = create_vacation(
        session,
        start_date=date(2026, 8, 29),
        end_date=date(2026, 8, 31),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    assert vacation.starts_at < now < vacation.ends_at < now + timedelta(hours=24)

    result = schedule_forecast(session, zone, now)

    assert len(result) == 2
    assert (result[0].starts_at, result[0].ends_at) == (now, vacation.ends_at)
    assert result[0].setpoint.temperature_c == Decimal("15.0")
    assert (result[1].starts_at, result[1].ends_at) == (
        vacation.ends_at, now + timedelta(hours=24)
    )
    assert result[1].setpoint.temperature_c == Decimal("21.0")


def test_schedule_forecast_ignores_a_running_vacation_when_the_zone_is_off(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "urlaub-vorschau-aus",
        points=[(1, 360, "tag", Decimal("21.0"))],
        operating_mode="off",
        frost_protection=Decimal("16.0"),
    )
    now = datetime(2026, 8, 31, 10, 0)
    create_vacation(
        session,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 5),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
    )
    result = schedule_forecast(session, zone, now)
    assert len(result) == 1
    assert result[0].setpoint.temperature_c == Decimal("16.0")


# --- next_switch / jump_to_next_switch --------------------------------------------
#
# "Zur nächsten Schaltzeit springen": die nächste reguläre Zeitplanphase gilt sofort,
# aber nur bis zu dem Moment, an dem sie ohnehin regulär begonnen hätte. Der
# Wochenplan bleibt dabei unangetastet -- jeder Test, der eine Übersteuerung anlegt,
# prüft deshalb zusätzlich, dass die gespeicherten Schaltpunkte unverändert sind.


def _override_count(session: Session, zone_id: int) -> int:
    return len(session.query(ZoneOverride).filter_by(zone_id=zone_id).all())


def test_jump_to_next_switch_targets_the_next_phases_mode_and_temperature(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-modus",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
    )
    # Montag 10:00 lokal -- mitten in der Tagphase, die ab 06:00 gilt.
    now = datetime(2026, 8, 31, 8, 0)

    entry = jump_to_next_switch(session, zone, now=now)

    assert entry.temperature_c == Decimal("18.0")
    assert entry.zone_id == zone.id


def test_jump_to_next_switch_ends_exactly_at_the_switch_second(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-endzeit",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
    )
    now = datetime(2026, 8, 31, 8, 0)

    entry = jump_to_next_switch(session, zone, now=now)

    # Montag 20:00 lokal (Sommerzeit, UTC+2) -> 18:00 UTC.
    assert entry.ends_at == datetime(2026, 8, 31, 18, 0)


def test_jump_to_next_switch_leaves_the_weekly_schedule_untouched(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-unveraendert",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
    )
    before = _stored_snapshot(session, zone.id)

    jump_to_next_switch(session, zone, now=datetime(2026, 8, 31, 8, 0))

    after = _stored_snapshot(session, zone.id)
    assert after == before


def test_jump_to_next_switch_crosses_a_day_boundary(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-tagesgrenze",
        points=[(1, 1320, "nacht", Decimal("18.0")), (2, 360, "tag", Decimal("21.0"))],
    )
    # Montag 23:00 lokal -- in der Nachtphase, die ab 22:00 gilt.
    now = datetime(2026, 8, 31, 21, 0)

    entry = jump_to_next_switch(session, zone, now=now)

    assert entry.temperature_c == Decimal("21.0")
    # Dienstag 06:00 lokal -> 04:00 UTC.
    assert entry.ends_at == datetime(2026, 9, 1, 4, 0)


def test_jump_to_next_switch_crosses_a_weekday_change(session: Session) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-wochentag",
        points=[(7, 1320, "nacht", Decimal("18.0")), (1, 360, "tag", Decimal("21.0"))],
    )
    # Sonntag 23:00 lokal -- in der Nachtphase, die ab 22:00 gilt.
    now = datetime(2026, 9, 6, 21, 0)

    entry = jump_to_next_switch(session, zone, now=now)

    assert entry.temperature_c == Decimal("21.0")
    # Montag 06:00 lokal -> 04:00 UTC.
    assert entry.ends_at == datetime(2026, 9, 7, 4, 0)


def test_jump_to_next_switch_without_a_schedule_fails_and_creates_nothing(
    session: Session,
) -> None:
    zone = zone_with_schedule(session, "sprung-ohne-plan", points=[])

    with pytest.raises(ScheduleError, match="kein Zeitplan"):
        jump_to_next_switch(session, zone, now=datetime(2026, 8, 31, 8, 0))

    assert _override_count(session, zone.id) == 0


def test_jump_to_next_switch_without_a_setpoint_for_the_next_mode_fails(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session, "sprung-ohne-sollwert", points=[(1, 360, "tag", Decimal("21.0"))]
    )
    # Ein Modus ohne hinterlegte Temperatur für diese Zone -- die nächste Phase
    # kann so keinen Sollwert liefern.
    leerer_modus = create_mode(session, "ohne-temperatur")
    session.add(SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=1200, setpoint_mode_id=leerer_modus.id,
    ))
    session.flush()
    now = datetime(2026, 8, 31, 8, 0)

    with pytest.raises(ScheduleError):
        jump_to_next_switch(session, zone, now=now)

    assert _override_count(session, zone.id) == 0


def test_jump_to_next_switch_with_a_running_override_refuses_a_second_one(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-laeuft-schon",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
        override=(Decimal("24.0"), None),
    )
    now = datetime(2026, 8, 31, 8, 0)
    laufend = running_override(session, zone, now)
    assert laufend is not None

    with pytest.raises(OverrideAlreadyRunning) as exc_info:
        jump_to_next_switch(session, zone, now=now)

    assert exc_info.value.running_override.id == laufend.id
    assert _override_count(session, zone.id) == 1


def test_jump_to_next_switch_with_replace_ends_the_old_override_first(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "sprung-ersetzen",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
        override=(Decimal("24.0"), None),
    )
    now = datetime(2026, 8, 31, 8, 0)
    alte = running_override(session, zone, now)
    assert alte is not None

    neue = jump_to_next_switch(session, zone, now=now, replace=True)

    session.refresh(alte)
    assert alte.cancelled_at is not None
    assert neue.id != alte.id
    assert running_override(session, zone, now) is not None
    assert running_override(session, zone, now).id == neue.id
    assert neue.temperature_c == Decimal("18.0")


def test_jump_to_next_switch_stores_the_switch_time_as_utc_for_berlin(
    session: Session,
) -> None:
    """`zone_with_schedule` setzt die Anlage auf Europe/Berlin -- die Endzeit muss
    trotzdem als UTC gespeichert sein und zur lokalen Schaltzeit passen (Sommerzeit,
    UTC+2)."""
    zone = zone_with_schedule(
        session,
        "sprung-zeitzone",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 900, "nacht", Decimal("18.0"))],
    )
    now = datetime(2026, 8, 31, 8, 0)

    entry = jump_to_next_switch(session, zone, now=now)

    # Montag 15:00 lokal (900 Minuten) -> 13:00 UTC.
    assert entry.ends_at == datetime(2026, 8, 31, 13, 0)


def test_next_switch_writes_nothing_and_matches_the_subsequent_jump(
    session: Session,
) -> None:
    zone = zone_with_schedule(
        session,
        "vorschau-liest-nur",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
    )
    now = datetime(2026, 8, 31, 8, 0)

    before = _override_count(session, zone.id)
    preview = next_switch(session, zone, now)
    assert isinstance(preview, NextSwitch)
    assert _override_count(session, zone.id) == before

    entry = jump_to_next_switch(session, zone, now=now)

    assert preview.at == entry.ends_at
    assert preview.setpoint.temperature_c == entry.temperature_c


def test_next_switch_ignores_a_running_override(session: Session) -> None:
    """Der eigentliche Grund, warum `next_switch` nicht `resolved_setpoint`
    benutzt: eine laufende Übersteuerung auf 24 °C darf das Ziel nicht verfälschen
    -- es muss der Zeitplanwert der nächsten Phase bleiben."""
    zone = zone_with_schedule(
        session,
        # Kurz halten: `zone_with_schedule` baut daraus `frost-<name>` für
        # `setpoint_mode.code`, und die Spalte fasst 32 Zeichen. Unter SQLite
        # ginge ein längerer Name durch, MariaDB lehnt ihn ab -- genau der Fall,
        # für den die Suite gegen beide Datenbanken läuft.
        "vorschau-ohne-ueberst",
        points=[(1, 360, "tag", Decimal("21.0")), (1, 1200, "nacht", Decimal("18.0"))],
        override=(Decimal("24.0"), None),
    )
    now = datetime(2026, 8, 31, 8, 0)

    preview = next_switch(session, zone, now)

    assert preview is not None
    assert preview.setpoint.temperature_c == Decimal("18.0")


def test_next_switch_does_not_skip_a_phase_that_lasts_a_single_minute(
    session: Session,
) -> None:
    """Der Grund für den Abstand von einer Sekunde statt einer Minute.

    Schaltpunkte sind minutengenau, zwei davon können also unmittelbar
    aufeinanderfolgen. Wird der Sollwert eine Minute nach dem Schaltzeitpunkt
    abgefragt, liegt die Abfrage bereits in der übernächsten Phase -- der Sprung zöge
    dann den falschen Wert vor, und zwar lautlos: es entstünde eine gültige
    Übersteuerung mit einer Temperatur, die zu diesem Zeitpunkt gar nicht gilt.
    """
    zone = zone_with_schedule(
        session,
        "minutenphase",
        [
            (1, 6 * 60, "nacht", Decimal("18.0")),
            (1, 8 * 60, "kurz", Decimal("23.0")),
            (1, 8 * 60 + 1, "spaeter", Decimal("20.0")),
        ],
    )
    # Montag, 07:00 Ortszeit (Europe/Berlin, Sommerzeit) = 05:00 UTC.
    result = next_switch(session, zone, datetime(2026, 8, 31, 5, 0))
    assert result is not None
    assert result.setpoint.temperature_c == Decimal("23.0")
