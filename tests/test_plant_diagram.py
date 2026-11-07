"""The plant diagram -- which device does what, where.

The value of this view lies in the **gaps**: a zone without a temperature
source controls nothing, one without an actuator does nothing, and a device
without a zone does nothing at all. A plain device list does not show any of
that.
"""

from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_zone, role, zone_mit_fuehler
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.domain.plant_diagram import plant_diagram


def test_a_fully_wired_zone_has_no_deficiencies(session: Session) -> None:
    zone = create_zone(session, "vollstaendig")
    sensor = create_device(session, "thermometer")
    valve = create_device(session, "ventil")
    zone_mit_fuehler(session, zone, sensor)
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()

    picture = plant_diagram(session, [zone]).zones[0]
    assert [s.name for s in picture.temperature_sources] == [sensor.display_name]
    assert [a.name for a in picture.actuators] == [valve.display_name]
    assert picture.defects == []


def test_a_zone_without_a_temperature_source_reports_the_deficiency(session: Session) -> None:
    zone = create_zone(session, "blind")
    valve = create_device(session, "ventil-blind")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.temperature_sources == []
    assert any("Messquelle" in m for m in picture.defects)


def test_a_zone_without_an_actuator_reports_the_deficiency(session: Session) -> None:
    zone = create_zone(session, "ohnmaechtig")
    sensor = create_device(session, "thermometer-ohnmaechtig")
    zone_mit_fuehler(session, zone, sensor)
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert any("Aktor" in m for m in picture.defects)


def test_window_contacts_appear_under_their_zone(session: Session) -> None:
    zone = create_zone(session, "mit-fenster")
    contact = create_device(session, "fensterkontakt")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=contact.id,
            device_role_id=role(session, "window_contact").id,
        )
    )
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert [k.name for k in picture.window_contacts] == [contact.display_name]
    assert picture.actuators == []


def test_devices_without_a_zone_are_listed_separately(session: Session) -> None:
    """The most common reason a newly added sensor "doesn't come through": it
    reports in, but no zone uses it."""
    zone = create_zone(session, "eine-zone")
    used = create_device(session, "benutzt")
    unused = create_device(session, "herrenlos")
    zone_mit_fuehler(session, zone, used)
    session.flush()

    picture = plant_diagram(session, [zone])
    assert [g.name for g in picture.without_zone] == [unused.display_name]


def test_capabilities_appear_on_the_device(session: Session) -> None:
    """They answer *why* a device sits in this spot -- a valve without a switch
    output would be an actuator that can do nothing."""
    zone = create_zone(session, "faehig")
    sensor = create_device(session, "faehiger-sensor")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=capability(session, "temperature").id
        )
    )
    zone_mit_fuehler(session, zone, sensor)
    session.flush()
    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.temperature_sources[0].capabilities


def test_an_old_misassignment_is_reported_as_a_deficiency(session: Session) -> None:
    """The check at assignment time prevents new cases like this. The old ones
    already sit in the database and would otherwise never be noticed -- the
    plant diagram would show a complete path, and yet nothing would ever have
    switched."""
    zone = create_zone(session, "altlast")
    sensor = create_device(session, "sensor-als-aktor")
    session.add(
        DeviceCapabilityLink(
            device_id=sensor.id, capability_id=capability(session, "temperature").id
        )
    )
    # Deliberately bypassing the domain function: this is what an existing
    # record looks like if it predates the check.
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=sensor.id,
            device_role_id=role(session, "actuator").id,
        )
    )
    session.flush()

    picture = plant_diagram(session, [zone]).zones[0]
    assert picture.actuators[0].unsuitable is not None
    assert any("Schaltausgang" in m or "keinen Schaltausgang" in m for m in picture.defects)


def test_an_orphaned_valve_does_not_count_as_unsuitable(session: Session) -> None:
    """Counter-check for the distinction "temperature source" versus "no
    requirement": nobody places a requirement on a device without a zone.
    Without this, every unassigned valve would be marked "does not measure
    temperature"."""
    create_zone(session, "leerzone")
    valve = create_device(session, "herrenloses-ventil")
    session.add(
        DeviceCapabilityLink(
            device_id=valve.id, capability_id=capability(session, "switch").id
        )
    )
    session.flush()

    picture = plant_diagram(session, [])
    free_device = next(g for g in picture.without_zone if g.name == valve.display_name)
    assert free_device.unsuitable is None


def test_an_assignment_in_a_role_the_diagram_does_not_show_is_skipped(
    session: Session,
) -> None:
    """The plant diagram draws three slots; a device in a fourth role is not one of them.

    Roles are rows in a lookup table, so a fourth can exist -- through a migration, or
    through data from an older version. It must not end up in a column it does not
    belong to, and it must not stop the diagram either: the zones around it are still
    drawn.
    """
    from thermoctl.db.models.device import ZoneDevice
    from thermoctl.db.models.lookup import DeviceRole

    zone = create_zone(session, "sonderrolle")
    device = create_device(session, "sondergeraet")
    unknown_role = DeviceRole(code="beobachter", label="Beobachter")
    session.add(unknown_role)
    session.flush()
    session.add(
        ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=unknown_role.id)
    )
    session.flush()

    zone_picture = plant_diagram(session, [zone]).zones[0]
    shown = [
        g.display_name
        for g in (
            *zone_picture.actuators,
            *zone_picture.window_contacts,
            *zone_picture.controllers,
        )
    ]
    assert device.display_name not in shown


def test_several_temperature_sources_appear_in_order(session: Session) -> None:
    zone = create_zone(session, "zwei-fuehler")
    first = create_device(session, "fuehler-a")
    second = create_device(session, "fuehler-b")
    zone_mit_fuehler(session, zone, first)
    zone_mit_fuehler(session, zone, second)

    diagram = plant_diagram(session, [zone])

    assert [s.name for s in diagram.zones[0].temperature_sources] == [
        first.display_name,
        second.display_name,
    ]
    assert diagram.without_zone == []
