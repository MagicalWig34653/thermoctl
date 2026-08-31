"""What thermoctl writes to a thermostatic valve that regulates itself.

Two ways to run a radiator thermostat, and the difference is who decides:

* **thermoctl decides** (the default). It computes on/off with hysteresis, minimum
  switching duration and window-open, and drives the valve. The thermostat is a dumb
  switch that happens to speak a thermostat's language.
* **The valve decides** (this module). thermoctl only says what to aim for. The valve
  runs its own loop against its own sensor, and where it accepts one, against the room
  temperature thermoctl measures elsewhere.

The second is what a radiator thermostat is built for, and it has one concrete
advantage: its own sensor sits on the radiator, where it reads several degrees too
warm. Fed the temperature of a sensor on the far wall, it regulates against the room
instead of against itself.

**Writing a setpoint here moves a valve.** It is not a display value. Everything that
leaves this module therefore travels as a switching message and through both dry-run
bolts -- the same as an on/off command, because the physical effect is the same.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.schedule import frost_protection_temperature, resolved_setpoint

# The property a valve accepts its target on. Same name as in the capability
# detection -- a valve without it is not recognised as one in the first place.
SETPOINT_PROPERTY = "occupied_heating_setpoint"

# Where a measured room temperature can be written, in the order we would rather use
# them. Zigbee2MQTT does not agree on one name across manufacturers: a WT-A03E takes
# `external_temperature_input`, others `external_temperature` or `remote_temperature`.
# Only what the device actually reports as writable is used -- this list decides the
# order, never whether a property exists.
TEMPERATURE_PROPERTIES = (
    "external_temperature_input",
    "external_temperature",
    "remote_temperature",
    "external_measured_room_sensor",
)


@dataclass(frozen=True)
class ValveCommand:
    """What one self-regulating valve should be told, and why.

    `reason` is not decoration: it goes into the log beside the message, and "why is
    this valve being told 7 degrees" is the question someone asks in front of a cold
    room in January.
    """

    device: Device
    setpoint_c: Decimal
    temperature_c: Decimal | None
    temperature_property: str | None
    reason: str


def _writable_properties(session: Session, device: Device) -> dict[str, DeviceProperty]:
    return {
        row.name: row
        for row in session.scalars(
            select(DeviceProperty).where(
                DeviceProperty.device_id == device.id,
                DeviceProperty.is_writable.is_(True),
            )
        )
    }


def _in_range(property_model: DeviceProperty, value: Decimal) -> bool:
    """Whether the device would accept this number at all.

    Asked before sending, not after: Zigbee2MQTT discards a payload outside the
    declared range without an error, so an unchecked value looks like a message that
    went out and did nothing.
    """
    if property_model.min_value is not None and value < property_model.min_value:
        return False
    return not (property_model.max_value is not None and value > property_model.max_value)


def valve_commands(session: Session, zone: Zone, now: datetime) -> list[ValveCommand]:
    """What every self-regulating valve of this zone should be told right now.

    The setpoint is the one the zone is aiming for -- schedule, override and boost
    already resolved, so a boost reaches these valves like any other.

    **An open window lowers it to frost protection.** Without that, "open window stops
    the heating" would quietly stop applying to exactly these zones: thermoctl no
    longer switches them, so the only way it can still act is through the number it
    writes. The valve keeps regulating -- towards a temperature it will not reach.
    """
    actuator = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    thermostat = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    if actuator is None or thermostat is None:
        return []

    state = session.get(ZoneState, zone.id)
    window_open = bool(state is not None and state.window_open)
    if window_open:
        setpoint = frost_protection_temperature(session, zone)
        reason = "Fenster offen — Frostschutz"
    else:
        resolved = resolved_setpoint(session, zone, now)
        setpoint, reason = resolved.temperature_c, resolved.reason

    measured = None if state is None else state.temperature_c

    commands: list[ValveCommand] = []
    for device in session.scalars(
        select(Device)
        .join(ZoneDevice, ZoneDevice.device_id == Device.id)
        .where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_role_id == actuator.id,
            ZoneDevice.self_regulating.is_(True),
        )
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ):
        writable = _writable_properties(session, device)
        target = writable.get(SETPOINT_PROPERTY)
        if target is None or not _in_range(target, setpoint):
            # Either not a valve we can aim, or a setpoint it would discard. Sending
            # it anyway would look like a command and change nothing.
            continue

        temperature_property = next(
            (name for name in TEMPERATURE_PROPERTIES if name in writable), None
        )
        temperature = measured
        if temperature_property is not None and temperature is not None:
            if not _in_range(writable[temperature_property], temperature):
                temperature_property, temperature = None, None
        else:
            temperature_property, temperature = None, None

        commands.append(
            ValveCommand(
                device=device,
                setpoint_c=setpoint,
                temperature_c=temperature,
                temperature_property=temperature_property,
                reason=reason,
            )
        )
    return commands
