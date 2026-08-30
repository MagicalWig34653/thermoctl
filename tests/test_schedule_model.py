import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_mode, create_zone
from thermoctl.db.models.schedule import SchedulePoint


def test_two_points_at_the_same_time_are_excluded(session: Session) -> None:
    zone = create_zone(session, "wohnzimmer")
    mode = create_mode(session, "tag")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=mode.id))
    session.flush()
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=mode.id))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_weekday_outside_1_to_7_is_rejected(session: Session, weekday: int) -> None:
    zone = create_zone(session, f"z{weekday}")
    mode = create_mode(session, f"m{weekday}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=weekday, minute_of_day=0,
                              setpoint_mode_id=mode.id))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


@pytest.mark.parametrize("minute", [-1, 1440, 5000])
def test_minute_outside_the_day_is_rejected(session: Session, minute: int) -> None:
    zone = create_zone(session, f"zm{minute}")
    mode = create_mode(session, f"mm{minute}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=minute,
                              setpoint_mode_id=mode.id))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_the_same_time_in_two_zones_is_allowed(session: Session) -> None:
    mode = create_mode(session, "tag2")
    for name in ("bad", "kueche"):
        zone = create_zone(session, name)
        session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                                  setpoint_mode_id=mode.id))
    session.flush()
