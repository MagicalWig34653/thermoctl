from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tests.hilfen import schattenentscheidung_anlegen, zone_anlegen, zonenzustand_anlegen
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ShadowDecision, ZoneState


def test_zonenzustand_verschwindet_mit_der_zone(session: Session) -> None:
    zone = zone_anlegen(session, "zustand-kaskade")
    zonenzustand_anlegen(session, zone)
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.flush()
    assert session.query(ZoneState).count() == 0


def test_schattenprotokoll_folgt_der_zone_beim_loeschen(session: Session) -> None:
    """Sonst laesst sich eine Zone nicht mehr loeschen, sobald ein Schattenlauf lief.

    Aufgefallen beim Bau der Zonenverwaltung: `shadow_decision.zone_id` war der einzige
    Zonenbezug ohne CASCADE. Dass die Zone geloescht wurde, steht im Audit-Protokoll —
    das ist die Aufzeichnung, die ueberdauern soll, und sie haengt nicht an der Zone.
    """
    zone = zone_anlegen(session, "zone-mit-protokoll")
    schattenentscheidung_anlegen(session, zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 1

    session.delete(zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 0
