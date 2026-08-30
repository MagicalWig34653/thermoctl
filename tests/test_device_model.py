import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_zone, integration, rolle
from thermoctl.db.models.device import Device, ZoneDevice


def test_address_is_unique_per_integration(session: Session) -> None:
    z2m = integration(session, "zigbee2mqtt")
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="A"))
    session.flush()
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="B"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_address_in_two_integrations_is_allowed(session: Session) -> None:
    for code in ("zigbee2mqtt", "meross"):
        session.add(
            Device(
                integration_id=integration(session, code).id,
                external_id="schalter",
                display_name=code,
            )
        )
    session.flush()


def test_a_zone_can_have_any_number_of_actuators(session: Session) -> None:
    zone = create_zone(session, "wohnzimmer")
    actuator = rolle(session, "actuator")
    for name in ("aktor_1", "aktor_2"):
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=create_device(session, name).id,
                device_role_id=actuator.id,
            )
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(zone_id=zone.id).count() == 2


def test_a_device_can_have_two_roles(session: Session) -> None:
    """An Aqara W100 measures and acts as a controller at the same time."""
    zone = create_zone(session, "bad")
    w100 = create_device(session, "w100_bad")
    for code in ("controller", "actuator"):
        session.add(
            ZoneDevice(zone_id=zone.id, device_id=w100.id, device_role_id=rolle(session, code).id)
        )
    session.flush()
    assert session.query(ZoneDevice).filter_by(device_id=w100.id).count() == 2


def test_the_same_role_twice_on_the_same_device_is_excluded(session: Session) -> None:
    zone = create_zone(session, "kueche")
    device = create_device(session, "aktor_kueche")
    actuator = rolle(session, "actuator")
    session.add(ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=actuator.id))
    session.flush()
    session.add(ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=actuator.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_zone_has_exactly_one_temperature_source(session: Session) -> None:
    """The cardinality lives in the column, not in an application rule."""
    zone = create_zone(session, "flur")
    zone.temperature_source_device_id = create_device(session, "sensor_flur").id
    session.flush()
    assert zone.temperature_source_device_id is not None


def test_swapping_a_device_leaves_the_zone_untouched(session: Session) -> None:
    zone = create_zone(session, "buero")
    old = create_device(session, "aktor_alt")
    new = create_device(session, "aktor_neu")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=old.id, device_role_id=rolle(session, "actuator").id
    )
    session.add(assignment)
    session.flush()
    assignment.device_id = new.id
    session.flush()
    assert zone.display_name == "Buero"
