from dataclasses import FrozenInstanceError
from datetime import datetime
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
from thermoctl.domain.schedule import (
    MINUTES_PER_WEEK,
    DaySegment,
    ScheduleError,
    Setpoint,
    cancel_override,
    change_schedule_point_mode,
    copy_schedule_day,
    create_override,
    current_point,
    next_point,
    paint_schedule_interval,
    resolved_setpoint,
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


def test_current_and_next_point_use_weekday_and_minute_components() -> None:
    tuesday = SchedulePoint(weekday=2, minute_of_day=123, setpoint_mode_id=1)
    friday = SchedulePoint(weekday=5, minute_of_day=754, setpoint_mode_id=2)

    moment = datetime(2026, 9, 4, 12, 33)
    assert current_point([tuesday, friday], moment) is tuesday
    assert next_point([tuesday, friday], moment) == datetime(2026, 9, 4, 12, 34)
    assert next_point([tuesday, friday], datetime(2026, 9, 6, 23, 59)) == datetime(
        2026, 9, 8, 2, 3
    )


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


def test_operating_mode_off_results_in_frost_protection(session: Session) -> None:
    zone = zone_with_schedule(session, "aus", points=[(1, 360, "tag", Decimal("21.0"))],
                             operating_mode="off", frost_protection=Decimal("16.0"))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")


def test_an_override_beats_the_schedule(session: Session) -> None:
    zone = zone_with_schedule(session, "ueber", points=[(1, 360, "tag", Decimal("21.0"))],
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


def test_the_reason_names_the_decision(session: Session) -> None:
    """Principle 5: traceable, why this setpoint applies."""
    zone = zone_with_schedule(session, "grund", points=[(1, 360, "tag", Decimal("21.0"))])
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert "Tag" in result.reason and "06:00" in result.reason


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
    zone = create_zone(session, "keine-ueber")
    assert cancel_override(session, zone) is None


def test_an_override_on_a_mode_without_a_fixed_temperature(session: Session) -> None:
    """An override can point to a mode instead of a fixed temperature —
    the setpoint then comes from the zone's temperature for that mode."""
    zone = zone_with_schedule(session, "modus-ueber", points=[(1, 360, "tag", Decimal("21.0"))])
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
