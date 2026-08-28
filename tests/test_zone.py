from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint


def _betriebsart(session: Session) -> OperatingMode:
    art = session.query(OperatingMode).filter_by(code="auto").one_or_none()
    if art is None:
        art = OperatingMode(code="auto", label="Automatik")
        session.add(art)
        session.flush()
    return art


def test_regelparameter_sind_standardmaessig_leer(session: Session) -> None:
    zone = Zone(name="wohnzimmer", display_name="Wohnzimmer",
                operating_mode_id=_betriebsart(session).id)
    session.add(zone)
    session.flush()
    assert zone.hysteresis_k is None
    assert zone.min_on_seconds is None
    assert zone.sensor_timeout_seconds is None


def test_zonenname_ist_eindeutig(session: Session) -> None:
    art = _betriebsart(session).id
    session.add(Zone(name="bad", display_name="Bad", operating_mode_id=art))
    session.flush()
    session.add(Zone(name="bad", display_name="Bad oben", operating_mode_id=art))
    with pytest.raises(IntegrityError):
        session.flush()


def test_ein_sollwert_je_zone_und_modus(session: Session) -> None:
    zone = Zone(name="kueche", display_name="Kueche",
                operating_mode_id=_betriebsart(session).id)
    modus = SetpointMode(code="tag", name="Tag")
    session.add_all([zone, modus])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("21.0")))
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("22.0")))
    with pytest.raises(IntegrityError):
        session.flush()


def test_nachkommastelle_bleibt_erhalten(session: Session) -> None:
    zone = Zone(name="flur", display_name="Flur",
                operating_mode_id=_betriebsart(session).id)
    modus = SetpointMode(code="nacht", name="Nacht")
    session.add_all([zone, modus])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("18.5")))
    session.commit()
    geladen = session.query(ZoneSetpoint).filter_by(zone_id=zone.id).one()
    assert geladen.temperature_c == Decimal("18.5")
