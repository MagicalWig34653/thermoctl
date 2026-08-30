from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_mode, create_zone, source
from thermoctl.db.base import utcnow
from thermoctl.db.models.override import ZoneOverride


def test_either_mode_or_temperature_but_not_both(session: Session) -> None:
    zone = create_zone(session, "z1")
    mode = create_mode(session, "tag")
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            setpoint_mode_id=mode.id,
            temperature_c=Decimal("22.0"),
            starts_at=utcnow(),
            source_id=source(session, "web").id,
        )
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_neither_mode_nor_temperature_is_rejected(session: Session) -> None:
    zone = create_zone(session, "z2")
    session.add(
        ZoneOverride(zone_id=zone.id, starts_at=utcnow(), source_id=source(session, "web").id)
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_a_permanent_override_has_no_end(session: Session) -> None:
    zone = create_zone(session, "z3")
    override = ZoneOverride(
        zone_id=zone.id,
        temperature_c=Decimal("23.0"),
        starts_at=utcnow(),
        ends_at=None,
        source_id=source(session, "web").id,
    )
    session.add(override)
    session.flush()
    assert override.ends_at is None
    assert override.cancelled_at is None


def test_an_override_is_kept_as_history(session: Session) -> None:
    """Cancelling does not delete, it sets cancelled_at."""
    zone = create_zone(session, "z4")
    override = ZoneOverride(
        zone_id=zone.id,
        temperature_c=Decimal("19.0"),
        starts_at=utcnow(),
        source_id=source(session, "web").id,
    )
    session.add(override)
    session.flush()
    override.cancelled_at = utcnow()
    session.flush()
    assert session.query(ZoneOverride).filter_by(zone_id=zone.id).count() == 1
