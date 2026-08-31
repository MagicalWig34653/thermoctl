"""What a physical dial does from the outside.

Two decisions are on trial here: that the thermostat adjusts the *mode* and
not "right now", and that boost brings forward the next switch instead of
heating to some guessed value.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.remote_control import RemoteControlError, boost, set_setpoint
from thermoctl.domain.schedule import (
    create_override,
    resolved_setpoint,
    temperature_for_mode,
)

# A Monday, 08:00 UTC. The settings are in UTC, so this is local time too.
MONDAY_EIGHT = datetime(2026, 8, 31, 8, 0)


def _zone_with_plan(session: Session) -> tuple[object, object, object]:
    """A zone with day from 06:00 and night from 22:00 -- and setpoints for both."""
    # Timezone UTC, so that in the test the plan's local time and the results'
    # UTC are the same number. The conversion itself is checked by the
    # schedule tests.
    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "planzone")
    day = create_mode(session, "tag")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=day.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    return zone, day, night


def test_the_setpoint_changes_the_currently_active_mode(session: Session) -> None:
    zone, day, night = _zone_with_plan(session)

    set_setpoint(session, zone, Decimal("22.5"), MONDAY_EIGHT, source="system")

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == day.id
        )
    ) == Decimal("22.5")
    # Counter-check: the other mode stays as it was -- exactly one was changed.
    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == night.id
        )
    ) == Decimal("18.0")
    # And no override is created that would lapse after the next point.
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()


def test_a_running_override_is_changed_instead(session: Session) -> None:
    """Then there is no mode to change.

    Without this case, the controller would jump back to the override's
    value at the next status report and look as if it had swallowed the
    command.
    """
    zone, day, _ = _zone_with_plan(session)
    # `now=` matters here: without it the override starts at the real clock and is not
    # yet running at MONDAY_EIGHT. The test then took the *other* branch -- it changed
    # the day mode, arrived at the same 23.0, and passed while the case it describes
    # never happened.
    create_override(
        session, zone, Decimal("19.0"), None, now=MONDAY_EIGHT, user_id=None,
        source="system",
    )
    before = temperature_for_mode(session, zone, day.id)

    set_setpoint(session, zone, Decimal("23.0"), MONDAY_EIGHT, source="system")

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("23.0")
    entry = session.scalar(
        select(ZoneOverride)
        .where(ZoneOverride.zone_id == zone.id)
        .order_by(ZoneOverride.id.desc())
    )
    assert entry is not None and entry.temperature_c == Decimal("23.0")
    # And the mode was left alone -- that is what "no mode to change" means.
    assert temperature_for_mode(session, zone, day.id) == before


def test_boost_brings_forward_the_next_switch_point(session: Session) -> None:
    """From now on, whatever would come next applies -- until exactly that point in time."""
    zone, _, night = _zone_with_plan(session)

    result = boost(session, zone, MONDAY_EIGHT, source="system")

    assert result.mode_code == "nacht"
    assert result.temperature == Decimal("18.0")
    # 22:00 the same day: the point that would have come next on schedule.
    assert result.bis == datetime(2026, 8, 31, 22, 0)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("18.0")


def test_after_the_boost_the_schedule_takes_over_by_itself(session: Session) -> None:
    """The counter-check: nothing is left behind that anyone would need to clean up."""
    zone, _, _ = _zone_with_plan(session)
    boost(session, zone, MONDAY_EIGHT, source="system")

    afterward = datetime(2026, 8, 31, 22, 30)
    # From 22:00 night applies anyway -- but the reason must be the schedule
    # again, not the override, or it would have outlived it.
    assert "Zeitplan" in resolved_setpoint(session, zone, afterward).reason


def test_boost_without_a_schedule_says_why(session: Session) -> None:
    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "planlos")
    with pytest.raises(RemoteControlError, match="keinen Zeitplan"):
        boost(session, zone, MONDAY_EIGHT, source="system")


def test_boost_without_a_stored_temperature_says_why(session: Session) -> None:
    """A mode with no setpoint in this zone: there is nothing to bring forward."""
    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "temperaturlos")
    day = create_mode(session, "tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=day.id)
    )
    session.flush()
    with pytest.raises(RemoteControlError, match="keine Temperatur"):
        boost(session, zone, MONDAY_EIGHT, source="system")


def test_boost_without_settings_says_why(session: Session) -> None:
    zone = create_zone(session, "unfertig")
    with pytest.raises(RemoteControlError, match="unvollständig"):
        boost(session, zone, MONDAY_EIGHT, source="system")


def test_a_boost_starts_at_the_moment_it_was_decided_for(session: Session) -> None:
    """The override must start at the caller's `now`, not at whatever the clock says.

    `create_override` used to stamp `starts_at` from the real clock while every caller
    passed its own moment. In normal operation the two are milliseconds apart and
    nothing shows; but `resolved_setpoint` only counts an override whose start has been
    reached, so a boost stamped a hair later than the moment it was decided for simply
    does not apply -- the user presses the button and nothing happens.

    The same defect was fixed once in `end_of_next_switch`. This pins the other half:
    the recorded start is the moment that was handed in, whatever time it is now.
    """
    zone, _, _ = _zone_with_plan(session)

    boost(session, zone, MONDAY_EIGHT, source="system")

    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None
    assert entry.starts_at == MONDAY_EIGHT
    # And therefore it applies at that moment, which is the whole point.
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("18.0")
