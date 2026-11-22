"""Das Sollwertrad: eine befristete Übersteuerung, die von selbst endet.

`set_temporary_setpoint` ist, was ein Rad an einer Brücke (Matter) bewirkt. Anders als
`set_setpoint` verstellt es keinen Modus, sondern legt eine Übersteuerung an -- und
ersetzt dabei eine laufende, statt eine weitere danebenzustapeln. Feste Zeiten per
`now=`, nie die echte Uhr.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, operating_mode, source
from tests.test_remote_control import MONDAY_EIGHT, _zone_with_plan
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import remote_control
from thermoctl.domain.absence import start_absence
from thermoctl.domain.modes import DomainError
from thermoctl.domain.remote_control import (
    RemoteControlError,
    set_setpoint,
    set_temporary_setpoint,
)
from thermoctl.domain.schedule import (
    cancel_override,
    create_override,
    resolved_setpoint,
    temperature_for_mode,
)


def _rows(session: Session, zone: Zone) -> list[ZoneOverride]:
    return list(
        session.scalars(
            select(ZoneOverride).where(ZoneOverride.zone_id == zone.id).order_by(ZoneOverride.id)
        )
    )


def _running(session: Session, zone: Zone, now: datetime) -> list[ZoneOverride]:
    return [
        row for row in _rows(session, zone)
        if row.cancelled_at is None
        and row.starts_at <= now
        and (row.ends_at is None or row.ends_at > now)
    ]


def _setting(session: Session) -> Setting:
    row = session.get(Setting, 1)
    assert row is not None
    return row


def test_the_dial_sets_an_override_that_ends_at_the_next_switch(session: Session) -> None:
    zone, day, _ = _zone_with_plan(session)

    result = set_temporary_setpoint(session, zone, Decimal("23.5"), MONDAY_EIGHT, source="system")

    # 22:00 the same day: the point that would have come next on schedule.
    assert result.temperature == Decimal("23.5")
    assert result.ends_at == datetime(2026, 8, 31, 22, 0)
    (row,) = _rows(session, zone)
    assert (row.temperature_c, row.starts_at, row.ends_at) == (
        Decimal("23.5"), MONDAY_EIGHT, datetime(2026, 8, 31, 22, 0),
    )
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("23.5")
    # The mode was not touched -- that is `set_setpoint`'s job, not the dial's.
    assert temperature_for_mode(session, zone, day.id) == Decimal("21.0")


def test_after_the_next_switch_the_schedule_applies_by_itself(session: Session) -> None:
    zone, _, _ = _zone_with_plan(session)
    set_temporary_setpoint(session, zone, Decimal("23.5"), MONDAY_EIGHT, source="system")

    assert resolved_setpoint(
        session, zone, datetime(2026, 8, 31, 21, 59)
    ).temperature_c == Decimal("23.5")
    after = resolved_setpoint(session, zone, datetime(2026, 8, 31, 22, 0))
    assert after.temperature_c == Decimal("18.0")
    assert "Zeitplan" in after.reason


def test_turning_the_dial_twice_replaces_instead_of_stacking(session: Session) -> None:
    zone, _, _ = _zone_with_plan(session)
    second_moment = MONDAY_EIGHT + timedelta(minutes=5)

    set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")
    set_temporary_setpoint(session, zone, Decimal("24.0"), second_moment, source="system")

    first, second = _rows(session, zone)
    assert _running(session, zone, second_moment) == [second]
    assert second.temperature_c == Decimal("24.0")
    # The first one is ended the usual way -- never deleted -- at the moment of the turn.
    assert first.cancelled_at == second_moment
    assert resolved_setpoint(session, zone, second_moment).temperature_c == Decimal("24.0")


def test_ending_the_override_after_two_turns_leaves_the_schedule_not_the_old_value(
    session: Session,
) -> None:
    """The defect of stacking: each turn added a running row, and ending 'the' override
    uncovered the previous dial value instead of the schedule."""
    zone, _, _ = _zone_with_plan(session)
    set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")
    later = MONDAY_EIGHT + timedelta(minutes=5)
    set_temporary_setpoint(session, zone, Decimal("24.0"), later, source="system")

    cancel_override(session, zone, now=later + timedelta(minutes=1))

    moment = later + timedelta(minutes=2)
    assert _running(session, zone, moment) == []
    assert resolved_setpoint(session, zone, moment).temperature_c == Decimal("21.0")


def test_a_zone_without_a_schedule_gets_the_fallback_not_an_unbounded_override(
    session: Session,
) -> None:
    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "planlos")

    result = set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")

    assert result.ends_at == MONDAY_EIGHT + timedelta(minutes=120)
    (row,) = _rows(session, zone)
    assert row.ends_at == MONDAY_EIGHT + timedelta(minutes=120)


def test_a_fixed_duration_ends_after_those_minutes_even_with_a_schedule(
    session: Session,
) -> None:
    zone, _, _ = _zone_with_plan(session)
    settings = _setting(session)
    settings.temporary_setpoint_until_next_switch = 0
    settings.temporary_setpoint_minutes = 45

    result = set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")

    assert result.ends_at == MONDAY_EIGHT + timedelta(minutes=45)
    assert resolved_setpoint(
        session, zone, MONDAY_EIGHT + timedelta(minutes=44)
    ).temperature_c == Decimal("22.0")
    assert resolved_setpoint(
        session, zone, MONDAY_EIGHT + timedelta(minutes=45)
    ).temperature_c == Decimal("21.0")


def test_the_setting_applies_to_the_next_turn_and_only_to_it(session: Session) -> None:
    zone, _, _ = _zone_with_plan(session)
    set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")
    settings = _setting(session)
    settings.temporary_setpoint_until_next_switch = 0
    settings.temporary_setpoint_minutes = 30
    later = MONDAY_EIGHT + timedelta(minutes=10)

    set_temporary_setpoint(session, zone, Decimal("23.0"), later, source="system")

    first, second = _rows(session, zone)
    assert first.ends_at == datetime(2026, 8, 31, 22, 0)  # the old end is not rewritten
    assert second.ends_at == later + timedelta(minutes=30)


def test_the_fallback_follows_the_setting_when_there_is_no_next_switch(
    session: Session,
) -> None:
    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "planlos")
    _setting(session).temporary_setpoint_minutes = 20

    result = set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")

    assert result.ends_at == MONDAY_EIGHT + timedelta(minutes=20)


def test_a_zone_that_is_off_refuses_the_dial_and_creates_nothing(session: Session) -> None:
    zone, _, _ = _zone_with_plan(session)
    zone.operating_mode = operating_mode(session, "off")
    session.flush()

    with pytest.raises(RemoteControlError, match="ausgeschaltet"):
        set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")

    assert _rows(session, zone) == []


def test_a_zone_that_is_off_keeps_a_running_override_untouched_when_refusing(
    session: Session,
) -> None:
    zone, _, _ = _zone_with_plan(session)
    create_override(session, zone, Decimal("19.0"), None, now=MONDAY_EIGHT, source="system")
    zone.operating_mode = operating_mode(session, "off")
    session.flush()

    with pytest.raises(RemoteControlError):
        set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")

    (row,) = _rows(session, zone)
    assert row.cancelled_at is None


@pytest.mark.parametrize("value", ["99", "35.1", "-20.1", "21.55", "NaN", "Infinity"])
def test_a_value_outside_the_limits_or_with_too_many_decimals_is_refused_and_changes_nothing(
    session: Session, value: str
) -> None:
    """The check runs before the running override is ended: a refused turn must not
    leave the zone without the override it had."""
    zone, _, _ = _zone_with_plan(session)
    create_override(session, zone, Decimal("19.0"), None, now=MONDAY_EIGHT, source="system")

    with pytest.raises(DomainError):
        set_temporary_setpoint(session, zone, Decimal(value), MONDAY_EIGHT, source="system")

    (row,) = _rows(session, zone)
    assert row.cancelled_at is None and row.temperature_c == Decimal("19.0")
    assert not session.scalars(select(AuditEvent)).all()


def test_an_absence_override_stays_and_applies_again_when_the_dial_override_ends(
    session: Session,
) -> None:
    zone, _, _ = _zone_with_plan(session)
    absence_end = MONDAY_EIGHT + timedelta(days=2)
    start_absence(session, [zone], Decimal("17.0"), absence_end, now=MONDAY_EIGHT)
    dial_moment = MONDAY_EIGHT + timedelta(minutes=1)

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), dial_moment, source="system")

    absence_row = session.scalars(
        select(ZoneOverride).where(ZoneOverride.absence_id.is_not(None))
    ).one()
    assert absence_row.cancelled_at is None
    assert resolved_setpoint(session, zone, dial_moment).temperature_c == Decimal("23.0")
    # Once the dial override has run out, the setback of the absence is back -- not the
    # schedule, which would be 18.0 at this point.
    assert resolved_setpoint(session, zone, result.ends_at).temperature_c == Decimal("17.0")
    # A second turn replaces the dial override but still leaves the absence alone.
    set_temporary_setpoint(
        session, zone, Decimal("24.0"), dial_moment + timedelta(minutes=1), source="system"
    )
    assert absence_row.cancelled_at is None
    assert len(_running(session, zone, dial_moment + timedelta(minutes=2))) == 2


@pytest.mark.parametrize(
    ("zone_clock", "points", "expected_end_utc"),
    [
        # Spring forward 2026-03-29 (02:00 CET -> 03:00 CEST): the local day has 23 hours.
        # Local Sun 00:30 CET; next switch 04:00 CEST = 02:00 UTC, not 03:00 UTC.
        (datetime(2026, 3, 28, 23, 30), (240, 600), datetime(2026, 3, 29, 2, 0)),
        # Fall back 2026-10-25 (03:00 CEST -> 02:00 CET): the local day has 25 hours.
        # Local Sun 00:00 CEST; next switch 03:30 CET = 02:30 UTC, not 01:30 UTC.
        (datetime(2026, 10, 24, 22, 0), (210, 600), datetime(2026, 10, 25, 2, 30)),
    ],
)
def test_the_end_is_right_across_a_daylight_saving_change(
    session: Session,
    zone_clock: datetime,
    points: tuple[int, int],
    expected_end_utc: datetime,
) -> None:
    create_settings(session).timezone = "Europe/Berlin"
    source(session, "system")
    zone = create_zone(session, "sommerzeit")
    night = create_mode(session, "nacht")
    day = create_mode(session, "tag")
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=7, minute_of_day=points[0], setpoint_mode_id=day.id
            ),
            SchedulePoint(
                zone_id=zone.id, weekday=7, minute_of_day=points[1], setpoint_mode_id=night.id
            ),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), zone_clock, source="system")

    assert result.ends_at == expected_end_utc
    assert resolved_setpoint(
        session, zone, expected_end_utc - timedelta(minutes=1)
    ).temperature_c == Decimal("23.0")
    assert resolved_setpoint(session, zone, expected_end_utc).temperature_c == Decimal("21.0")


def test_the_dial_is_audited_like_the_setpoint_command(session: Session) -> None:
    zone, _, _ = _zone_with_plan(session)

    set_temporary_setpoint(
        session, zone, Decimal("23.5"), MONDAY_EIGHT, user_id=None, source="system"
    )

    (event,) = session.scalars(select(AuditEvent)).all()
    # `zone_setpoint` keyed by the zone id: the object kind the audit scope of a room
    # already knows, so a room-limited auditor sees it too.
    assert (event.action, event.object_type, event.object_id) == (
        "update", "zone_setpoint", str(zone.id),
    )
    assert "23,5" in event.summary and zone.display_name in event.summary
    assert event.detail is not None and "2026-08-31T22:00:00" in event.detail


def test_the_dial_without_settings_says_why(session: Session) -> None:
    zone = create_zone(session, "unfertig")
    with pytest.raises(RemoteControlError, match="unvollständig"):
        set_temporary_setpoint(session, zone, Decimal("22.0"), MONDAY_EIGHT, source="system")


def test_set_setpoint_still_changes_the_mode_and_creates_no_override(session: Session) -> None:
    """Regression: the dial of Home Assistant keeps its meaning next to the new function."""
    zone, day, _ = _zone_with_plan(session)

    set_setpoint(session, zone, Decimal("22.5"), MONDAY_EIGHT, source="system")

    assert temperature_for_mode(session, zone, day.id) == Decimal("22.5")
    assert _rows(session, zone) == []


# -- Wiederholstunde der Zeitumstellung ---------------------------------------------------


def _berlin_zone_with_point_at(session: Session, weekday: int, minute: int) -> Zone:
    create_settings(session).timezone = "Europe/Berlin"
    source(session, "system")
    zone = create_zone(session, "wiederholstunde")
    night = create_mode(session, "nacht")
    day = create_mode(session, "tag")
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=weekday, minute_of_day=minute,
                setpoint_mode_id=night.id,
            ),
            SchedulePoint(
                zone_id=zone.id, weekday=weekday, minute_of_day=600, setpoint_mode_id=day.id
            ),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    return zone


def test_in_the_repeated_hour_the_dial_ends_at_the_schedule_point(session: Session) -> None:
    """2026-10-25, Europe/Berlin: 02:00-03:00 happens twice. now = 01:15 UTC is 02:15 CET,
    the *second* pass; the schedule point at 02:30 fired in the first pass (00:30 UTC) and
    does not fire again, so the next switch is the point after the repeated hour, 10:00 CET
    (09:00 UTC). It used to be read as its first occurrence (before now), so the fallback
    applied; the end is the schedule point itself, and the override applies and runs."""
    zone = _berlin_zone_with_point_at(session, weekday=7, minute=150)
    first = set_temporary_setpoint(
        session, zone, Decimal("22.0"), datetime(2026, 10, 24, 12, 0), source="system"
    )
    assert first.ends_at > datetime(2026, 10, 24, 12, 0)
    now = datetime(2026, 10, 25, 1, 15)
    next_switch_after_the_hour = datetime(2026, 10, 25, 9, 0)

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), now, source="system")

    assert result.ends_at == next_switch_after_the_hour
    assert resolved_setpoint(session, zone, now).temperature_c == Decimal("23.0")
    assert _running(session, zone, now + timedelta(minutes=1)) != []
    assert _running(session, zone, datetime(2026, 10, 25, 1, 30)) != []
    assert _running(session, zone, next_switch_after_the_hour - timedelta(seconds=1)) != []
    assert _running(session, zone, next_switch_after_the_hour) == []


@pytest.mark.parametrize("offset", [timedelta(hours=-1), timedelta(0)], ids=["before", "equal"])
def test_the_fallback_catches_an_end_that_is_not_after_now(
    session: Session, monkeypatch: pytest.MonkeyPatch, offset: timedelta
) -> None:
    """The fallback span is only a safety net now: should `end_of_next_switch` ever
    return a moment that is not after `now`, the override must not end before it starts
    (it would end the running one and never apply itself)."""
    zone = _berlin_zone_with_point_at(session, weekday=7, minute=150)
    _setting(session).temporary_setpoint_minutes = 45
    now = datetime(2026, 10, 25, 1, 15)
    monkeypatch.setattr(
        remote_control, "end_of_next_switch", lambda session, zone, now: now + offset
    )

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), now, source="system")

    assert result.ends_at == now + timedelta(minutes=45)
    assert resolved_setpoint(session, zone, now).temperature_c == Decimal("23.0")


def test_one_hour_earlier_the_end_is_still_the_next_switch(session: Session) -> None:
    """Counter-check: the first pass of the same night (00:15 UTC = 02:15 CEST) has an
    end in the future, and it stays the schedule point, not the fallback."""
    zone = _berlin_zone_with_point_at(session, weekday=7, minute=150)
    now = datetime(2026, 10, 25, 0, 15)

    result = set_temporary_setpoint(session, zone, Decimal("23.0"), now, source="system")

    assert result.ends_at == datetime(2026, 10, 25, 0, 30)
