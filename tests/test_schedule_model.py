import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.hilfen import CONSTRAINT_FEHLER, modus_anlegen, zone_anlegen
from thermoctl.db.models.schedule import SchedulePoint


def test_zwei_punkte_zur_selben_zeit_sind_ausgeschlossen(session: Session) -> None:
    zone = zone_anlegen(session, "wohnzimmer")
    modus = modus_anlegen(session, "tag")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=modus.id))
    session.flush()
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=modus.id))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("wochentag", [0, 8, -1])
def test_wochentag_ausserhalb_1_bis_7_wird_abgewiesen(session: Session, wochentag: int) -> None:
    zone = zone_anlegen(session, f"z{wochentag}")
    modus = modus_anlegen(session, f"m{wochentag}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=wochentag, minute_of_day=0,
                              setpoint_mode_id=modus.id))
    with pytest.raises(CONSTRAINT_FEHLER):
        session.flush()


@pytest.mark.parametrize("minute", [-1, 1440, 5000])
def test_minute_ausserhalb_des_tages_wird_abgewiesen(session: Session, minute: int) -> None:
    zone = zone_anlegen(session, f"zm{minute}")
    modus = modus_anlegen(session, f"mm{minute}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=minute,
                              setpoint_mode_id=modus.id))
    with pytest.raises(CONSTRAINT_FEHLER):
        session.flush()


def test_dieselbe_zeit_in_zwei_zonen_ist_erlaubt(session: Session) -> None:
    modus = modus_anlegen(session, "tag2")
    for name in ("bad", "kueche"):
        zone = zone_anlegen(session, name)
        session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                                  setpoint_mode_id=modus.id))
    session.flush()
