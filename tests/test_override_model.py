from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_mode, create_zone, source
from thermoctl.db.base import utcnow
from thermoctl.db.models.override import ZoneOverride


def test_entweder_modus_oder_temperatur_aber_nicht_beides(session: Session) -> None:
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


def test_weder_modus_noch_temperatur_wird_abgewiesen(session: Session) -> None:
    zone = create_zone(session, "z2")
    session.add(
        ZoneOverride(zone_id=zone.id, starts_at=utcnow(), source_id=source(session, "web").id)
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_dauerhafte_uebersteuerung_hat_kein_ende(session: Session) -> None:
    zone = create_zone(session, "z3")
    ueber = ZoneOverride(
        zone_id=zone.id,
        temperature_c=Decimal("23.0"),
        starts_at=utcnow(),
        ends_at=None,
        source_id=source(session, "web").id,
    )
    session.add(ueber)
    session.flush()
    assert ueber.ends_at is None
    assert ueber.cancelled_at is None


def test_uebersteuerung_bleibt_als_historie_erhalten(session: Session) -> None:
    """Aufheben loescht nicht, es setzt cancelled_at."""
    zone = create_zone(session, "z4")
    ueber = ZoneOverride(
        zone_id=zone.id,
        temperature_c=Decimal("19.0"),
        starts_at=utcnow(),
        source_id=source(session, "web").id,
    )
    session.add(ueber)
    session.flush()
    ueber.cancelled_at = utcnow()
    session.flush()
    assert session.query(ZoneOverride).filter_by(zone_id=zone.id).count() == 1
