from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_zone,
    role,
    source,
)
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.device_assignment import swap_device


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {
        CSRF_HEADER: csrf_token(
            http_session, get_settings().secret_key.get_secret_value()
        )
    }


def _assign(session: Session, zone_id: int, device_id: int, rollencode: str) -> None:
    session.add(
        ZoneDevice(
            zone_id=zone_id,
            device_id=device_id,
            device_role_id=role(session, rollencode).id,
        )
    )
    session.flush()


def test_a_swap_keeps_the_zone_configuration_and_takes_over_every_role(
    session: Session,
) -> None:
    source(session)
    zone = create_zone(session, "wohnzimmer")
    zone.hysteresis_k = Decimal("0.45")
    zone.min_on_seconds = 420
    zone.sensor_timeout_seconds = 900
    mode = create_mode(session, "tag")
    session.add_all(
        [
            ZoneSetpoint(
                zone_id=zone.id,
                setpoint_mode_id=mode.id,
                temperature_c=Decimal("21.5"),
            ),
            SchedulePoint(
                zone_id=zone.id,
                weekday=1,
                minute_of_day=360,
                setpoint_mode_id=mode.id,
            ),
        ]
    )
    old = create_device(session, "alt")
    neues = create_device(session, "neu")
    _assign(session, zone.id, old.id, "actuator")
    _assign(session, zone.id, old.id, "controller")
    session.flush()
    vorher_setpoints = list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    )
    vorher_schedule = list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    )
    vorher_parameter = (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    )

    swap_device(session, zone, old, neues, actor_id=None)

    assert list(
        session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id == zone.id)
        )
    ) == vorher_setpoints
    assert list(
        session.execute(
            select(
                SchedulePoint.zone_id,
                SchedulePoint.weekday,
                SchedulePoint.minute_of_day,
                SchedulePoint.setpoint_mode_id,
            ).where(SchedulePoint.zone_id == zone.id)
        )
    ) == vorher_schedule
    assert (
        zone.hysteresis_k,
        zone.min_on_seconds,
        zone.min_off_seconds,
        zone.sensor_timeout_seconds,
        zone.temperature_offset_k,
        zone.window_resume_delay_seconds,
    ) == vorher_parameter
    new_rolen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    assert new_rolen == {role(session, "actuator").id, role(session, "controller").id}
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == old.id
        )
    ) is None


def test_a_swap_leaves_the_measurement_history_with_the_old_device(session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    old = create_device(session, "sensor-alt")
    neues = create_device(session, "sensor-neu")
    zone.temperature_source_device_id = old.id
    capability = DeviceCapability(code="temperature", label="Temperatur")
    session.add(capability)
    session.flush()
    measurement = Measurement(
        device_id=old.id,
        capability_id=capability.id,
        value_numeric=Decimal("19.750"),
        measured_at=datetime(2026, 8, 29, 10, 0),
        received_at=datetime(2026, 8, 29, 10, 0),
    )
    session.add(measurement)
    session.flush()

    swap_device(session, zone, old, neues, actor_id=None)

    assert zone.temperature_source_device_id == neues.id
    assert session.get(Measurement, measurement.id).device_id == old.id
    assert session.scalar(
        select(Measurement).where(Measurement.device_id == neues.id)
    ) is None


def test_a_swap_in_one_zone_leaves_a_second_zone_untouched(session: Session) -> None:
    source(session)
    zone_a = create_zone(session, "a")
    zone_b = create_zone(session, "b")
    old = create_device(session, "alt-gemeinsam")
    neues = create_device(session, "neu-a")
    _assign(session, zone_a.id, old.id, "window_contact")
    _assign(session, zone_b.id, old.id, "window_contact")

    swap_device(session, zone_a, old, neues, actor_id=None)

    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == old.id
        )
    ) is not None
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == neues.id
        )
    ) is None


def test_tausch_schreibt_audit(session: Session) -> None:
    source(session)
    zone = create_zone(session, "audit-zone")
    old = create_device(session, "audit-alt")
    neues = create_device(session, "audit-neu")
    _assign(session, zone.id, old.id, "actuator")

    swap_device(session, zone, old, neues, actor_id=None)

    entry = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "replace"
        )
    )
    assert entry is not None
    assert "audit-alt" in entry.summary
    assert "audit-neu" in entry.summary


def test_the_page_shows_assignments_and_the_temperature_source(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "anzeige")
    device = create_device(session, "thermostat")
    zone.temperature_source_device_id = device.id
    _assign(session, zone.id, device.id, "controller")

    response = client_als([("device.read", zone.id)]).get(
        f"/zones/{zone.id}/devices"
    )

    assert response.status_code == 200
    assert "thermostat" in response.text
    assert "controller" in response.text
    assert "Gerät tauschen" not in response.text


def test_a_duplicate_role_shows_an_understandable_message(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "doppelt")
    device = create_device(session, "kontakt")
    devicesrolle = role(session, "window_contact")
    _assign(session, zone.id, device.id, "window_contact")
    client = client_als([("device.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/devices/assign",
        data={"device_id": device.id, "role_id": devicesrolle.id},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "in dieser Rolle bereits zugeordnet" in response.text


def test_the_writing_routes_and_their_permissions(client_als, session: Session) -> None:
    source(session)
    eigene = create_zone(session, "eigene")
    fremde = create_zone(session, "fremde")
    old = create_device(session, "weg")
    neues = create_device(session, "hin")
    client = client_als([("device.manage", eigene.id)])
    head = _csrf(client)

    zuordnen = client.post(
        f"/zones/{eigene.id}/devices/assign",
        data={"device_id": old.id, "role_id": role(session, "actuator").id},
        headers=head,
        follow_redirects=False,
    )
    assert zuordnen.status_code == 303
    assignment = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == eigene.id)
    )
    assert assignment is not None

    temperature_source = client.post(
        f"/zones/{eigene.id}/devices/source",
        data={"device_id": old.id},
        headers=head,
        follow_redirects=False,
    )
    assert temperature_source.status_code == 303
    assert eigene.temperature_source_device_id == old.id

    swap = client.post(
        f"/zones/{eigene.id}/devices/swap",
        data={"old_device_id": old.id, "new_device_id": neues.id},
        headers=head,
        follow_redirects=False,
    )
    assert swap.status_code == 303
    new_assignment = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == eigene.id, ZoneDevice.device_id == neues.id
        )
    )
    assert new_assignment is not None

    # The identifier lives in the body, not in the path: hx-boost reads a form's
    # `action` once, so the table and the drag-out use the same endpoint.
    loesen = client.post(
        f"/zones/{eigene.id}/devices/detach",
        data={"assignment_id": str(new_assignment.id)},
        headers=head,
        follow_redirects=False,
    )
    assert loesen.status_code == 303
    assert session.get(ZoneDevice, new_assignment.id) is None

    assert client.get(f"/zones/{fremde.id}/devices").status_code == 404
    assert client.post(
        f"/zones/{fremde.id}/devices/source",
        data={"device_id": neues.id},
        headers=head,
    ).status_code == 404


def test_invalid_input_when_assigning(client_als, session: Session) -> None:
    """Every error path of the assignment page — until now only the success case
    was covered."""
    source(session)
    zone = create_zone(session, "zone-fehlerwege")
    device = create_device(session, "vorhanden")
    client = client_als([("device.manage", None), ("device.read", None)])
    head = _csrf(client)

    # Unknown device, unknown role, no data at all.
    for data in (
        {"device_id": "999999", "role_id": str(role(session, "actuator").id)},
        {"device_id": str(device.id), "role_id": "999999"},
        {"device_id": "", "role_id": ""},
        {"device_id": "kein Geraet", "role_id": "1"},
    ):
        response = client.post(f"/zones/{zone.id}/devices/assign", data=data, headers=head)
        assert response.status_code == 200, data
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, data


def test_the_temperature_source_can_be_detached_again(client_als, session: Session) -> None:
    """An empty field means 'no measurement source' — the zone then counts as having no source."""
    source(session)
    zone = create_zone(session, "zone-source-removed")
    device = create_device(session, "quelle-weg")
    client = client_als([("device.manage", None), ("device.read", None)])
    head = _csrf(client)
    client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": str(device.id)},
        headers=head,
    )
    assert zone.temperature_source_device_id == device.id
    response = client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": ""},
        headers=head, follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.temperature_source_device_id is None


def test_an_unknown_temperature_source_has_no_effect(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "zone-source-unknown")
    client = client_als([("device.manage", None), ("device.read", None)])
    response = client.post(
        f"/zones/{zone.id}/devices/source", data={"device_id": "999999"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert zone.temperature_source_device_id is None


def test_a_swap_with_nonsensical_devices_reports_understandably(
    client_als, session: Session
) -> None:
    """Three cases: the same device, an unknown device, a device with no assignment."""
    source(session)
    zone = create_zone(session, "zone-tausch-unsinn")
    eines = create_device(session, "eines")
    anderes = create_device(session, "anderes")
    client = client_als([("device.manage", None), ("device.read", None)])
    head = _csrf(client)

    for data in (
        {"old_device_id": str(eines.id), "new_device_id": str(eines.id)},
        {"old_device_id": str(eines.id), "new_device_id": "999999"},
        {"old_device_id": str(eines.id), "new_device_id": str(anderes.id)},
    ):
        response = client.post(f"/zones/{zone.id}/devices/swap", data=data, headers=head)
        assert response.status_code == 200, data
        assert session.scalar(
            select(ZoneDevice).where(ZoneDevice.zone_id == zone.id)
        ) is None, data


def test_a_foreign_assignment_cannot_be_detached(client_als, session: Session) -> None:
    """An assignment belonging to another zone yields 404, not 403."""
    source(session)
    eigene = create_zone(session, "eigene-loesen")
    fremde = create_zone(session, "fremde-loesen")
    device = create_device(session, "fremdgeraet")
    foreign_assignment = ZoneDevice(
        zone_id=fremde.id, device_id=device.id,
        device_role_id=role(session, "actuator").id,
    )
    session.add(foreign_assignment)
    session.flush()
    client = client_als([("device.manage", None), ("device.read", None)])
    response = client.post(
        f"/zones/{eigene.id}/devices/detach",
        data={"assignment_id": str(foreign_assignment.id)},
        headers=_csrf(client),
    )
    assert response.status_code == 404
    assert session.get(ZoneDevice, foreign_assignment.id) is not None


def test_detaching_a_foreign_assignment_is_refused_in_the_domain(
    session: Session,
) -> None:
    """The view already catches this case with a 404. The domain checks it anyway:

    It is also called later by REST and MCP, and a rule that lives in only one
    adapter does not apply to the others.
    """
    import pytest

    from thermoctl.domain.device_assignment import detach_device

    source(session)
    eine = create_zone(session, "zone-loesen-a")
    others = create_zone(session, "zone-loesen-b")
    device = create_device(session, "geraet-loesen")
    assignment = ZoneDevice(
        zone_id=others.id, device_id=device.id,
        device_role_id=role(session, "actuator").id,
    )
    session.add(assignment)
    session.flush()
    with pytest.raises(ValueError, match="gehört nicht zu dieser Zone"):
        detach_device(session, eine, assignment, actor_id=None)
    assert session.get(ZoneDevice, assignment.id) is not None


def test_drop_targets_only_with_device_manage(client_als, session: Session) -> None:
    """Dragging is a second way of operating the same change -- it must depend on
    the same permission check as the forms. A drop target that is visible and
    may not be used is an invitation to a 403."""
    zone = create_zone(session, "ziehzone")
    # Without a device there is nothing to drag -- the cards are built from the list.
    create_device(session, "ziehbares-geraet")

    darf = client_als([("device.read", None), ("device.manage", zone.id), ("zone.read", None)])
    page = darf.get(f"/zones/{zone.id}/devices")
    assert page.status_code == 200
    assert 'data-target="temperature_source"' in page.text
    assert "tc-draggable" in page.text

    read_only = client_als([("device.read", None), ("zone.read", None)])
    page = read_only.get(f"/zones/{zone.id}/devices")
    assert page.status_code == 200
    assert "data-target=" not in page.text
    assert "tc-draggable" not in page.text


def test_the_plant_diagram_offers_no_drop_targets(client_als, session: Session) -> None:
    """Counter-check: on the plant diagram, a drop target would be a promise the
    page does not keep -- there are no forms there that could submit it."""
    create_zone(session, "bildzone")
    page = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)]).get(
        "/plant"
    )
    assert page.status_code == 200
    assert "data-target=" not in page.text


# --- Capability check --------------------------------------------------------


def _with_capability(session: Session, name: str, *codes: str):
    """A device whose capabilities are known."""
    from tests.helpers import capability
    from thermoctl.db.models.device import DeviceCapabilityLink

    device = create_device(session, name)
    for code in codes:
        session.add(
            DeviceCapabilityLink(
                device_id=device.id, capability_id=capability(session, code).id
            )
        )
    session.flush()
    return device


def test_a_sensor_cannot_be_assigned_as_an_actuator(session: Session) -> None:
    """This used to be possible. The assignment then looked correct, the plant
    diagram showed a complete path, and yet nothing would ever have switched --
    an error that only shows up in winter and then looks like a control-logic bug."""
    from thermoctl.domain.device_assignment import CapabilityMissing, assign_device

    zone = create_zone(session, "faehigkeitszone")
    sensor = _with_capability(session, "nur-thermometer", "temperature", "battery")
    with pytest.raises(CapabilityMissing, match="Schaltausgang"):
        assign_device(
            session, zone, sensor, role(session, "actuator"), actor_id=None
        )


def test_a_valve_can_be_assigned_as_an_actuator(session: Session) -> None:
    """Counter-check. Without it, the test above would also be satisfied by a
    version that rejects every assignment."""
    from thermoctl.domain.device_assignment import assign_device

    zone = create_zone(session, "ventilzone")
    ventil = _with_capability(session, "echtes-ventil", "switch")
    assignment = assign_device(
        session, zone, ventil, role(session, "actuator"), actor_id=None
    )
    assert assignment.device_id == ventil.id


def test_a_thermostat_can_be_assigned_as_an_actuator(session: Session) -> None:
    """A Zigbee2MQTT TRV such as the WT-A03E has no switch output -- it is driven
    through `system_mode` and `occupied_heating_setpoint` instead. It still moves a
    real valve, so it must be able to fill the actuator slot."""
    from thermoctl.domain.device_assignment import assign_device

    zone = create_zone(session, "thermostatzone")
    trv = _with_capability(session, "echtes-thermostatventil", "thermostat")
    assignment = assign_device(
        session, zone, trv, role(session, "actuator"), actor_id=None
    )
    assert assignment.device_id == trv.id


def test_a_device_without_known_capabilities_is_let_through(session: Session) -> None:
    """The capabilities come from the bridge's device list. Anyone integrating a
    device that describes itself sparsely there should still be able to set up
    their plant -- only a demonstrable contradiction is rejected."""
    from thermoctl.domain.device_assignment import assign_device

    zone = create_zone(session, "unbekanntzone")
    schweigsam = create_device(session, "sagt-nichts-ueber-sich")
    assign_device(session, zone, schweigsam, role(session, "actuator"), actor_id=None)


def test_a_temperature_source_must_measure_temperature(session: Session) -> None:
    from thermoctl.domain.device_assignment import CapabilityMissing, set_temperature_source

    zone = create_zone(session, "temperature-source-zone")
    ventil = _with_capability(session, "valve-as-temperature-source", "switch")
    with pytest.raises(CapabilityMissing, match="Temperatur"):
        set_temperature_source(session, zone, ventil, actor_id=None)


def test_a_window_contact_must_report_a_contact(session: Session) -> None:
    from thermoctl.domain.device_assignment import CapabilityMissing, assign_device

    zone = create_zone(session, "kontaktzone")
    ventil = _with_capability(session, "ventil-als-kontakt", "switch")
    with pytest.raises(CapabilityMissing, match="Kontakt"):
        assign_device(
            session, zone, ventil, role(session, "window_contact"), actor_id=None
        )


def test_a_swap_checks_every_place_that_transfers(session: Session) -> None:
    """The quietest way to put an unsuitable device in a place: you pick two
    names and never even see which roles come along with them."""
    from thermoctl.domain.device_assignment import (
        CapabilityMissing,
        assign_device,
        swap_device,
    )

    zone = create_zone(session, "tauschzone")
    ventil = _with_capability(session, "altes-ventil", "switch")
    sensor = _with_capability(session, "neuer-sensor", "temperature")
    assign_device(session, zone, ventil, role(session, "actuator"), actor_id=None)

    with pytest.raises(CapabilityMissing, match="Schaltausgang"):
        swap_device(session, zone, ventil, sensor, actor_id=None)


def test_a_refused_swap_leaves_nothing_half_done(session: Session) -> None:
    """Check first, then write. Otherwise the measurement source would stay with
    the new device and the role with the old one."""
    from thermoctl.domain.device_assignment import (
        CapabilityMissing,
        assign_device,
        set_temperature_source,
        swap_device,
    )

    zone = create_zone(session, "halbzone")
    kombi = _with_capability(session, "kann-beides", "temperature", "switch")
    nur_sensor = _with_capability(session, "kann-nur-messen", "temperature")
    set_temperature_source(session, zone, kombi, actor_id=None)
    assign_device(session, zone, kombi, role(session, "actuator"), actor_id=None)

    with pytest.raises(CapabilityMissing):
        swap_device(session, zone, kombi, nur_sensor, actor_id=None)
    session.expire_all()
    assert zone.temperature_source_device_id == kombi.id


def test_the_view_shows_the_reason_instead_of_an_error(client_als, session: Session) -> None:
    """A 500 would be the worst answer here: the user did nothing wrong except
    the wrong thing, and they should learn what was missing."""
    zone = create_zone(session, "ansichtszone")
    sensor = _with_capability(session, "ansichts-sensor", "temperature")
    c = client_als([("device.read", None), ("device.manage", None), ("zone.read", None)])
    response = c.post(
        f"/zones/{zone.id}/devices/assign",
        data={"device_id": str(sensor.id), "role_id": str(role(session, "actuator").id)},
        headers=_csrf(c),
    )
    assert response.status_code == 200
    assert "Schaltausgang" in response.text


def test_zugeordnete_karten_tragen_ihre_kennung(client_als, session: Session) -> None:
    """Without it, a device could be dragged in, but not out again -- the way in
    and the way out would be two different actions."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "kennungszone")
    device = create_device(session, "kennungsgeraet")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
    )
    session.add(assignment)
    zone.temperature_source_device_id = device.id
    session.flush()

    client = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    )
    page = client.get(f"/zones/{zone.id}/devices")
    assert f'data-assignment="{assignment.id}"' in page.text
    assert 'data-source="yes"' in page.text
    assert 'data-target="detach"' in page.text


def test_without_device_manage_nothing_can_be_dragged_out(client_als, session: Session) -> None:
    """Counter-check: whoever may not change it sees the same card without a handle."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "lesezone")
    device = create_device(session, "lesegeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()

    page = client_als([("device.read", None), ("zone.read", None)]).get(
        f"/zones/{zone.id}/devices"
    )
    assert "tc-draggable" not in page.text
    assert 'data-target="detach"' not in page.text


def test_the_plant_diagram_carries_no_drag_handles(client_als, session: Session) -> None:
    """There are no forms there that could submit a drag-out."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "bildzone-griffe")
    device = create_device(session, "bildgeraet")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=device.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()

    page = client_als(
        [("device.read", None), ("device.manage", None), ("zone.read", None)]
    ).get("/plant")
    assert "tc-draggable" not in page.text


def _controller_commands(session: Session) -> None:
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS, ControllerCommand

    for code, label in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=label))
    session.add(DeviceCapability(code="action", label="Tastendruck"))
    session.flush()


def test_the_page_shows_the_buttons_that_actually_arrived(client_als, session: Session) -> None:
    """Nothing guessed: how a device names its buttons is decided by Zigbee2MQTT.

    Without this list, someone would have to read their model's datasheet -- and
    with a typo the button would silently do nothing.
    """
    import json

    from thermoctl.services.ingest import process_message

    _controller_commands(session)
    zone = create_zone(session, "tastenzone")
    device = create_device(session, "wandschalter")
    _assign(session, zone.id, device.id, "controller")
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "button_1_single"}).encode(),
        base="zigbee2mqtt",
        received_at=datetime(2026, 8, 31, 8, 0),
    )

    response = client_als([("device.read", zone.id)]).get(f"/zones/{zone.id}/devices")

    assert response.status_code == 200
    assert "Tastenbelegung" in response.text
    assert "button_1_single" in response.text
    assert "Nächste Schaltung vorziehen" in response.text


def test_without_a_controller_there_is_no_button_binding_section(
    client_als, session: Session
) -> None:
    """Counter-check: a section that appears for every zone carries no information."""
    _controller_commands(session)
    zone = create_zone(session, "tastenlos")
    device = create_device(session, "ventil")
    _assign(session, zone.id, device.id, "actuator")

    response = client_als([("device.read", zone.id)]).get(f"/zones/{zone.id}/devices")

    assert "Tastenbelegung" not in response.text


def test_a_button_can_be_bound_and_released_again(
    client_als, session: Session
) -> None:
    from thermoctl.db.models.device import ControllerBinding

    source(session)
    _controller_commands(session)
    zone = create_zone(session, "belegzone")
    device = create_device(session, "schalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    response = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id),
            "action_code": "single_plus",
            "command": "setpoint_up",
            "step_k": "1,0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    binding = session.scalars(select(ControllerBinding)).one()
    # Comma as usual in the form, period in the database.
    assert binding.step_k == Decimal("1.0")

    client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "single_plus", "command": ""},
        follow_redirects=False,
    )
    assert session.scalars(select(ControllerBinding)).all() == []


def test_unusable_button_bindings_are_refused(client_als, session: Session) -> None:
    source(session)
    _controller_commands(session)
    zone = create_zone(session, "fehlzone")
    device = create_device(session, "fehlschalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.manage", zone.id), ("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    ohne_aktion = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "", "command": "boost"},
    )
    assert ohne_aktion.status_code == 400

    krumme_number = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id), "action_code": "single_plus",
            "command": "setpoint_up", "step_k": "warm",
        },
    )
    assert "Zahl sein" in krumme_number.text

    zu_genau = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={
            "device_id": str(device.id), "action_code": "single_plus",
            "command": "setpoint_up", "step_k": "0,25",
        },
    )
    assert "Nachkommastelle" in zu_genau.text


def test_button_binding_needs_device_manage(client_als, session: Session) -> None:
    _controller_commands(session)
    zone = create_zone(session, "rechtezone")
    device = create_device(session, "rechteschalter")
    _assign(session, zone.id, device.id, "controller")
    client = client_als([("device.read", zone.id)])
    client.get(f"/zones/{zone.id}/devices")

    response = client.post(
        f"/zones/{zone.id}/devices/button",
        headers=_csrf(client),
        data={"device_id": str(device.id), "action_code": "single_plus", "command": "boost"},
    )
    assert response.status_code == 404
