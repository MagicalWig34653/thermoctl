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
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.device_assignment import (
    CapabilityMissing,
    detach_device,
    set_temperature_source,
    set_thermostat_mode,
    swap_device,
)


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
    new_link = create_device(session, "neu")
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

    swap_device(session, zone, old, new_link, actor_id=None)

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
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == new_link.id
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
    new_link = create_device(session, "sensor-neu")
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

    swap_device(session, zone, old, new_link, actor_id=None)

    assert zone.temperature_source_device_id == new_link.id
    assert session.get(Measurement, measurement.id).device_id == old.id
    assert session.scalar(
        select(Measurement).where(Measurement.device_id == new_link.id)
    ) is None


def test_a_swap_in_one_zone_leaves_a_second_zone_untouched(session: Session) -> None:
    source(session)
    zone_a = create_zone(session, "a")
    zone_b = create_zone(session, "b")
    old = create_device(session, "alt-gemeinsam")
    new_link = create_device(session, "neu-a")
    _assign(session, zone_a.id, old.id, "window_contact")
    _assign(session, zone_b.id, old.id, "window_contact")

    swap_device(session, zone_a, old, new_link, actor_id=None)

    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == old.id
        )
    ) is not None
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone_b.id, ZoneDevice.device_id == new_link.id
        )
    ) is None


def test_tausch_schreibt_audit(session: Session) -> None:
    source(session)
    zone = create_zone(session, "audit-zone")
    old = create_device(session, "audit-alt")
    new_link = create_device(session, "audit-neu")
    _assign(session, zone.id, old.id, "actuator")

    swap_device(session, zone, old, new_link, actor_id=None)

    entry = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "replace"
        )
    )
    assert entry is not None
    assert "audit-alt" in entry.summary
    assert "audit-neu" in entry.summary


def test_detaching_names_the_device_and_role_in_the_audit_entry(session: Session) -> None:
    source(session)
    zone = create_zone(session, "detach-audit-zone")
    device = create_device(session, "detach-audit-device")
    actuator = role(session, "actuator")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=actuator.id
    )
    session.add(assignment)
    session.flush()

    detach_device(session, zone, assignment, actor_id=None)

    entry = session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_device", AuditEvent.action == "unassign"
        )
    )
    assert entry is not None
    assert device.display_name in entry.summary
    assert actuator.label in entry.summary


















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
    eine = create_zone(session, "zone-lösen-a")
    others = create_zone(session, "zone-lösen-b")
    device = create_device(session, "geraet-lösen")
    assignment = ZoneDevice(
        zone_id=others.id, device_id=device.id,
        device_role_id=role(session, "actuator").id,
    )
    session.add(assignment)
    session.flush()
    with pytest.raises(ValueError, match="gehört nicht zu dieser Zone"):
        detach_device(session, eine, assignment, actor_id=None)
    assert session.get(ZoneDevice, assignment.id) is not None




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

    zone = create_zone(session, "fähigkeitszone")
    sensor = _with_capability(session, "nur-thermometer", "temperature", "battery")
    with pytest.raises(CapabilityMissing, match="Schaltausgang.*switch oder thermostat"):
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
    schweigsam = create_device(session, "sagt-nichts-über-sich")
    assign_device(session, zone, schweigsam, role(session, "actuator"), actor_id=None)


def test_a_temperature_source_must_measure_temperature(session: Session) -> None:
    from thermoctl.domain.device_assignment import CapabilityMissing, set_temperature_source

    zone = create_zone(session, "temperature-source-zone")
    ventil = _with_capability(session, "valve-as-temperature-source", "switch")
    with pytest.raises(CapabilityMissing, match="Temperatur"):
        set_temperature_source(session, zone, ventil, actor_id=None)


def test_temperature_source_audit_distinguishes_assignment_from_removal(
    session: Session,
) -> None:
    source(session)
    zone = create_zone(session, "temperature-source-audit")
    sensor = _with_capability(session, "temperature-source-sensor", "temperature")

    set_temperature_source(session, zone, sensor, actor_id=None)
    set_temperature_source(session, zone, None, actor_id=None)

    entries = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_type == "zone_temperature_source")
            .order_by(AuditEvent.id)
        )
    )
    assert [(entry.action, entry.summary) for entry in entries] == [
        (
            "assign",
            f"Messquelle von '{zone.display_name}' auf '{sensor.display_name}' gesetzt",
        ),
        ("unassign", f"Messquelle von '{zone.display_name}' gelöst"),
    ]


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


def test_a_swap_checks_the_temperature_source_before_writing(session: Session) -> None:
    source(session)
    zone = create_zone(session, "swap-temperature-source")
    sensor = _with_capability(session, "swap-old-sensor", "temperature")
    switch = _with_capability(session, "swap-new-switch", "switch")
    set_temperature_source(session, zone, sensor, actor_id=None)

    with pytest.raises(CapabilityMissing, match="Temperatur"):
        swap_device(session, zone, sensor, switch, actor_id=None)

    assert zone.temperature_source_device_id == sensor.id


def test_a_swap_merges_a_role_the_new_device_already_has(session: Session) -> None:
    source(session)
    zone = create_zone(session, "swap-existing-role")
    old = create_device(session, "swap-existing-old")
    new_link = create_device(session, "swap-existing-new")
    _assign(session, zone.id, old.id, "actuator")
    _assign(session, zone.id, new_link.id, "actuator")

    swap_device(session, zone, old, new_link, actor_id=None)

    assignments = list(
        session.scalars(
            select(ZoneDevice).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_id == new_link.id,
                ZoneDevice.device_role_id == role(session, "actuator").id,
            )
        )
    )
    assert len(assignments) == 1
    assert session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == old.id
        )
    ) is None


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






def test_without_device_manage_nothing_can_be_dragged_out(client_als, session: Session) -> None:
    """Counter-check: whoever may not change it sees the same card without a handle."""
    from thermoctl.db.models.device import ZoneDevice

    zone = create_zone(session, "lesezone")
    device = create_device(session, "lesegerät")
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
    device = create_device(session, "bildgerät")
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














def _thermostat_in_zone(session: Session, zone_name: str) -> tuple[object, object]:
    """A zone with a thermostatic valve assigned as its actuator."""
    zone = create_zone(session, zone_name)
    valve = _with_capability(session, f"{zone_name}-ventil", "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()
    assignment = session.scalar(
        select(ZoneDevice).where(
            ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == valve.id
        )
    )
    assert assignment is not None
    return zone, assignment


def _post_mode(client: TestClient, zone: Zone, assignment: ZoneDevice, mode: str | None):  # type: ignore[no-untyped-def]
    data = {"assignment_id": str(assignment.id)}
    if mode is not None:
        data["thermostat_mode"] = mode
    return client.post(
        f"/zones/{zone.id}/devices/regulation",
        data=data,
        headers=_csrf(client),
        follow_redirects=False,
    )






def test_a_plain_switch_cannot_be_told_to_regulate_itself(session: Session) -> None:
    """A socket has nothing to regulate with.

    Refused in the domain, not only hidden in the interface: the page is not the only
    caller, and offering the choice at all would promise something that cannot happen.
    """
    zone = create_zone(session, "steckdosenzone")
    socket = _with_capability(session, "steckdose", "switch")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=socket.id, device_role_id=role(session, "actuator").id
        )
    )
    session.flush()
    assignment = session.scalar(
        select(ZoneDevice).where(ZoneDevice.device_id == socket.id)
    )
    assert assignment is not None

    with pytest.raises(CapabilityMissing, match="kein"):
        set_thermostat_mode(session, zone, assignment, "self_regulating", actor_id=None)


def test_only_an_actuator_regulates_itself(session: Session) -> None:
    """The same device as a controller is a display, not a valve."""
    zone = create_zone(session, "anzeigenzone")
    valve = _with_capability(session, "ventil-als-anzeige", "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id, device_id=valve.id, device_role_id=role(session, "controller").id
        )
    )
    session.flush()
    assignment = session.scalar(select(ZoneDevice).where(ZoneDevice.device_id == valve.id))
    assert assignment is not None

    with pytest.raises(CapabilityMissing, match="Aktor"):
        set_thermostat_mode(session, zone, assignment, "self_regulating", actor_id=None)


def test_switching_the_regulation_mode_to_what_it_already_is_writes_nothing(
    session: Session,
) -> None:
    """No audit entry for a change that did not happen.

    Otherwise every page reload with the same value would leave another line in the
    log, and the entries that record a real decision would drown between them.
    """
    zone, assignment = _thermostat_in_zone(session, "unverändertzone")
    before = session.query(AuditEvent).count()

    set_thermostat_mode(session, zone, assignment, "controlled", actor_id=None)

    assert assignment.thermostat_mode == "controlled"
    assert session.query(AuditEvent).count() == before




def test_an_assignment_of_another_zone_is_refused_in_the_domain(session: Session) -> None:
    """The web route checks this too, but the domain is the one that must not be
    talked around -- REST and MCP reach the same function."""
    zone, _assignment = _thermostat_in_zone(session, "hierzone")
    other, foreign = _thermostat_in_zone(session, "dortzone")

    with pytest.raises(ValueError, match="gehört nicht zu dieser Zone"):
        set_thermostat_mode(session, zone, foreign, "self_regulating", actor_id=None)
    assert foreign.thermostat_mode == "controlled"




