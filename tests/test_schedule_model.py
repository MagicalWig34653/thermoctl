import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_mode, create_zone
from thermoctl.db.models.schedule import SchedulePoint


def test_zwei_punkte_zur_selben_zeit_sind_ausgeschlossen(session: Session) -> None:
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
def test_wochentag_ausserhalb_1_bis_7_wird_abgewiesen(session: Session, weekday: int) -> None:
    zone = create_zone(session, f"z{weekday}")
    mode = create_mode(session, f"m{weekday}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=weekday, minute_of_day=0,
                              setpoint_mode_id=mode.id))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


@pytest.mark.parametrize("minute", [-1, 1440, 5000])
def test_minute_ausserhalb_des_tages_wird_abgewiesen(session: Session, minute: int) -> None:
    zone = create_zone(session, f"zm{minute}")
    mode = create_mode(session, f"mm{minute}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=minute,
                              setpoint_mode_id=mode.id))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_dieselbe_zeit_in_zwei_zonen_ist_erlaubt(session: Session) -> None:
    mode = create_mode(session, "tag2")
    for name in ("bad", "kueche"):
        zone = create_zone(session, name)
        session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                                  setpoint_mode_id=mode.id))
    session.flush()
