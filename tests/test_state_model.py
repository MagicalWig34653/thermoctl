from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tests.helpers import create_shadow_decision, create_zone, create_zone_state
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone


def test_zone_state_disappears_with_the_zone(session: Session) -> None:
    zone = create_zone(session, "zustand-kaskade")
    create_zone_state(session, zone)
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.flush()
    assert session.query(ZoneState).count() == 0


def test_shadow_log_follows_the_zone_on_deletion(session: Session) -> None:
    """Otherwise a zone could no longer be deleted once a shadow run had happened.

    Found while building zone administration: `shadow_decision.zone_id` was the only
    zone reference without CASCADE. The fact that the zone was deleted is recorded in
    the audit log — that record is meant to outlive the zone, and it does not hang off it.
    """
    zone = create_zone(session, "zone-mit-protokoll")
    create_shadow_decision(session, zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 1

    session.delete(zone)
    session.flush()
    assert session.scalar(select(func.count()).select_from(ShadowDecision)) == 0
