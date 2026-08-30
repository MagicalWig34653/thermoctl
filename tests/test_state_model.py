from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tests.helpers import create_shadow_decision, create_zone, create_zone_state
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone


def test_zonenzustand_verschwindet_mit_der_zone(session: Session) -> None:
    zone = create_zone(session, "zustand-kaskade")
    create_zone_state(session, zone)
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.flush()
    assert session.query(ZoneState).count() == 0


def test_schattenprotokoll_folgt_der_zone_beim_loeschen(session: Session) -> None:
    """Sonst laesst sich eine Zone nicht mehr loeschen, sobald ein Schattenlauf lief.

    Aufgefallen beim Bau der Zonenverwaltung: `shadow_decision.zone_id` war der einzige
    Zonenbezug ohne CASCADE. Dass die Zone geloescht wurde, steht im Audit-Protokoll —
    das ist die Aufzeichnung, die ueberdauern soll, und sie haengt nicht an der Zone.
    """
    zone = create_zone(session, "zone-mit-protokoll")
    create_shadow_decision(session, zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 1

    session.delete(zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 0
