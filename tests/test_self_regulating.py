"""What a self-regulating thermostatic valve is told.

The second way to run a radiator thermostat: thermoctl stops switching and only names
the target. Everything here decides a number that a valve then regulates towards, so
the questions are which number, and whether it is one the device accepts at all.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_settings,
    create_zone,
    create_zone_state,
    role,
    source,
)
from thermoctl.db.models.device import DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.self_regulating import valve_commands

NOW = datetime(2026, 8, 31, 8, 0)


def _capability(session: Session, code: str) -> DeviceCapability:
    found = session.scalar(select(DeviceCapability).where(DeviceCapability.code == code))
    if found is None:
        found = DeviceCapability(code=code, label=code)
        session.add(found)
        session.flush()
    return found


def _property(
    session: Session,
    device_id: int,
    name: str,
    *,
    minimum: str = "5",
    maximum: str = "30",
    writable: bool = True,
) -> None:
    session.add(
        DeviceProperty(
            device_id=device_id,
            name=name,
            value_type="numeric",
            unit="°C",
            min_value=Decimal(minimum),
            max_value=Decimal(maximum),
            is_readable=True,
            is_writable=writable,
        )
    )
    session.flush()


def _zone_with_valve(
    session: Session,
    name: str,
    *,
    self_regulating: bool = True,
    properties: tuple[str, ...] = ("occupied_heating_setpoint",),
) -> tuple[object, object]:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, name)
    mode = create_mode(session, f"tag-{name}")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    valve = create_device(session, f"{name}-ventil")
    session.add(
        DeviceCapabilityLink(
            device_id=valve.id, capability_id=_capability(session, "thermostat").id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=valve.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=self_regulating,
        )
    )
    for property_name in properties:
        # Sollwerte 5..30, Aussentemperatur -40..125 -- so gibt Zigbee2MQTT die
        # Grenzen fuer diese Merkmale an.
        low, high = ("5", "30") if "setpoint" in property_name else ("-40", "125")
        _property(session, valve.id, property_name, minimum=low, maximum=high)
    session.flush()
    return zone, valve


def test_a_self_regulating_valve_is_told_the_zone_setpoint(session: Session) -> None:
    """The number the zone is aiming for, not a switching decision."""
    zone, valve = _zone_with_valve(session, "regelzone")

    commands = valve_commands(session, zone, NOW)

    assert len(commands) == 1
    assert commands[0].device.id == valve.id
    assert commands[0].setpoint_c > 0
    assert commands[0].temperature_property is None  # kein Merkmal dafuer angeboten


def test_a_valve_that_thermoctl_switches_is_not_told_anything(session: Session) -> None:
    """The counter-check, and the one that matters most.

    In the default mode thermoctl drives the valve itself. Writing a setpoint on top
    would be a second hand on the same valve.
    """
    zone, _valve = _zone_with_valve(session, "schaltzone", self_regulating=False)

    assert valve_commands(session, zone, NOW) == []


def test_the_measured_temperature_is_written_where_the_valve_accepts_one(
    session: Session,
) -> None:
    """The reason the mode is worth having.

    A thermostat's own sensor sits on the radiator and reads several degrees too warm.
    Fed the temperature measured on the far wall, it regulates against the room.
    """
    zone, _valve = _zone_with_valve(
        session,
        "aussenfuehlerzone",
        properties=("occupied_heating_setpoint", "external_temperature_input"),
    )
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("19.5")
    session.flush()

    command = valve_commands(session, zone, NOW)[0]

    assert command.temperature_property == "external_temperature_input"
    assert command.temperature_c == Decimal("19.5")


def test_without_a_reading_no_temperature_is_invented(session: Session) -> None:
    """A zone that has never measured has no temperature, and zero is not a stand-in --
    a valve told 0 degrees would stop heating on the spot."""
    zone, _valve = _zone_with_valve(
        session,
        "ohnemesswert",
        properties=("occupied_heating_setpoint", "external_temperature_input"),
    )
    create_zone_state(session, zone)
    session.flush()

    command = valve_commands(session, zone, NOW)[0]
    assert command.temperature_c is None
    assert command.temperature_property is None


def test_an_open_window_lowers_the_valve_to_frost_protection(session: Session) -> None:
    """Otherwise "open window stops the heating" would quietly stop applying here.

    thermoctl no longer switches these valves, so the only way it can still act is
    through the number it writes. Without this the valve would keep regulating towards
    a comfortable temperature against an open window.
    """
    zone, _valve = _zone_with_valve(session, "fensterzone")
    # Die Frostschutz-Betriebsart legt `create_settings` schon an; hier bekommt sie
    # nur einen Sollwert fuer diese Zone.
    settings = session.get(Setting, 1)
    assert settings is not None
    session.add(
        ZoneSetpoint(
            zone_id=zone.id,
            setpoint_mode_id=settings.frost_protection_mode_id,
            temperature_c=Decimal("7.0"),
        )
    )
    state = create_zone_state(session, zone)
    state.window_open = True
    session.flush()

    command = valve_commands(session, zone, NOW)[0]

    assert command.setpoint_c == Decimal("7.0")
    assert "Fenster" in command.reason


def test_a_setpoint_the_device_would_discard_is_not_sent(session: Session) -> None:
    """Zigbee2MQTT drops a payload outside the declared range without an error.

    An unchecked value would look like a message that went out and did nothing --
    the worst kind, because the log says it was sent.
    """
    zone, valve = _zone_with_valve(session, "engzone")
    narrow = session.scalar(
        select(DeviceProperty).where(DeviceProperty.device_id == valve.id)
    )
    assert narrow is not None
    narrow.min_value, narrow.max_value = Decimal("22"), Decimal("30")
    session.flush()

    assert valve_commands(session, zone, NOW) == []


def test_a_valve_without_a_writable_setpoint_is_skipped(session: Session) -> None:
    """Nothing to aim it with -- and a command it cannot obey is not worth sending."""
    zone, valve = _zone_with_valve(session, "unbeschreibbar")
    only_readable = session.scalar(
        select(DeviceProperty).where(DeviceProperty.device_id == valve.id)
    )
    assert only_readable is not None
    only_readable.is_writable = False
    session.flush()

    assert valve_commands(session, zone, NOW) == []


def test_a_measured_temperature_outside_the_valve_s_range_is_left_out(
    session: Session,
) -> None:
    """Sent anyway, the whole payload would be discarded -- setpoint included.

    Zigbee2MQTT rejects a payload with a value outside the declared range, and it
    rejects the *message*, not just the one field. An implausible reading would
    therefore also cost the valve its setpoint, which is the part that matters.
    """
    zone, valve = _zone_with_valve(
        session,
        "engefuehlerzone",
        properties=("occupied_heating_setpoint", "external_temperature_input"),
    )
    external = session.scalar(
        select(DeviceProperty).where(
            DeviceProperty.device_id == valve.id,
            DeviceProperty.name == "external_temperature_input",
        )
    )
    assert external is not None
    external.min_value, external.max_value = Decimal("10"), Decimal("30")
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("-5.0")  # ausserhalb dessen, was das Ventil annimmt
    session.flush()

    command = valve_commands(session, zone, NOW)[0]
    assert command.temperature_property is None
    assert command.temperature_c is None
    assert command.setpoint_c > 0  # der Sollwert geht trotzdem hinaus
