from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint


def _operating_mode(session: Session) -> OperatingMode:
    kind = session.query(OperatingMode).filter_by(code="auto").one_or_none()
    if kind is None:
        kind = OperatingMode(code="auto", label="Automatik")
        session.add(kind)
        session.flush()
    return kind


def test_control_parameters_are_empty_by_default(session: Session) -> None:
    zone = Zone(name="wohnzimmer", display_name="Wohnzimmer",
                operating_mode_id=_operating_mode(session).id)
    session.add(zone)
    session.flush()
    assert zone.hysteresis_k is None
    assert zone.min_on_seconds is None
    assert zone.sensor_timeout_seconds is None


def test_zone_name_is_unique(session: Session) -> None:
    kind = _operating_mode(session).id
    session.add(Zone(name="bad", display_name="Bad", operating_mode_id=kind))
    session.flush()
    session.add(Zone(name="bad", display_name="Bad oben", operating_mode_id=kind))
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_setpoint_per_zone_and_mode(session: Session) -> None:
    zone = Zone(name="kueche", display_name="Kueche",
                operating_mode_id=_operating_mode(session).id)
    mode = SetpointMode(code="tag", name="Tag")
    session.add_all([zone, mode])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id,
                             temperature_c=Decimal("21.0")))
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id,
                             temperature_c=Decimal("22.0")))
    with pytest.raises(IntegrityError):
        session.flush()


def test_decimal_place_is_preserved(session: Session) -> None:
    zone = Zone(name="flur", display_name="Flur",
                operating_mode_id=_operating_mode(session).id)
    mode = SetpointMode(code="nacht", name="Nacht")
    session.add_all([zone, mode])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id,
                             temperature_c=Decimal("18.5")))
    session.commit()
    # Without expire_all() the query would return the object from memory --
    # the session is built with expire_on_commit=False. The test would then
    # pass even if the database swallowed the decimal place. Only after
    # expiring it is the row genuinely reloaded.
    session.expire_all()
    loaded = session.query(ZoneSetpoint).filter_by(zone_id=zone.id).one()
    assert loaded.temperature_c == Decimal("18.5")
