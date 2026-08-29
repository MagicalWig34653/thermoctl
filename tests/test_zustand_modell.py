from sqlalchemy import delete
from sqlalchemy.orm import Session

from tests.hilfen import zone_anlegen, zonenzustand_anlegen
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ZoneState


def test_zonenzustand_verschwindet_mit_der_zone(session: Session) -> None:
    zone = zone_anlegen(session, "zustand-kaskade")
    zonenzustand_anlegen(session, zone)
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.flush()
    assert session.query(ZoneState).count() == 0
