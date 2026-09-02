"""Which of a zone's actuators expect a plain on/off command.

Two kinds of device can carry the `actuator` role (`domain/device_assignment.py`): a
self-regulating thermostatic valve, told a setpoint (`domain/self_regulating.py`), and
an ordinary switch, told on or off by the hysteresis decision in
`domain/control_loop.py`. This module lists the second kind — the devices that still
need `regelung.entscheiden()`'s `heating` to reach them at all before anything
physically moves.

`switch_commands()` below is deliberately narrow: only the `switch` capability.
`thermostat_commands()` covers the other half of what `domain/device_assignment.py`
lets fill the actuator slot — the `thermostat` capability alone (a Zigbee2MQTT TRV run
without self-regulation, driven through `Zigbee2MqttThermostat` instead of a plain
on/off relay).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import (
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    ZoneDevice,
)
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, Integration
from thermoctl.db.models.zone import Zone


@dataclass(frozen=True)
class SwitchCommand:
    """One ordinary actuator of a zone, and the binding it is reachable through.

    `integration_code` decides which adapter builds the actual command
    (`services/publishing.py`) — this module only says which devices qualify, it
    never touches an adapter itself (`tests/test_architecture.py` enforces that the
    domain stays free of adapters).
    """

    device: Device
    integration_code: str


def switch_commands(session: Session, zone: Zone) -> list[SwitchCommand]:
    """Every ordinary (non-self-regulating) actuator of this zone with a switch output.

    A device without the `switch` capability recorded, or one marked
    `self_regulating`, is left out — sending it an on/off command would either do
    nothing the device understands, or fight the device's own regulation loop
    (rule 1 of the assignment: a self-regulating valve never also gets a switch
    command).
    """
    actuator = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    switch = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    if actuator is None or switch is None:
        return []

    rows = session.execute(
        select(Device, Integration.code)
        .join(ZoneDevice, ZoneDevice.device_id == Device.id)
        .join(Integration, Integration.id == Device.integration_id)
        .join(
            DeviceCapabilityLink,
            (DeviceCapabilityLink.device_id == Device.id)
            & (DeviceCapabilityLink.capability_id == switch.id),
        )
        .where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_role_id == actuator.id,
            ZoneDevice.self_regulating.is_(False),
        )
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    )
    return [SwitchCommand(device=device, integration_code=code) for device, code in rows]


@dataclass(frozen=True)
class ThermostatCommand:
    """One ordinary (non-self-regulating) Zigbee2MQTT thermostatic actuator of a zone.

    `has_system_mode` says whether the device declared `system_mode` as a writable
    property — the same `device_property` rows `domain/self_regulating.py` reads for
    the setpoint. See `Zigbee2MqttThermostat` (`integrations/actuators.py`) for why
    that changes what "off" means for this device.
    """

    device: Device
    integration_code: str
    has_system_mode: bool


def thermostat_commands(session: Session, zone: Zone) -> list[ThermostatCommand]:
    """Every ordinary (non-self-regulating) actuator of this zone with a thermostat output.

    Counterpart to `switch_commands()` above for the other capability
    `REQUIRED_CAPABILITY["actuator"]` (`domain/device_assignment.py`) accepts. A device
    is only listed here when it does **not** also carry the `switch` capability: the
    two are meant to be exclusive on real hardware (a valve has either an on/off relay
    or a heating-setpoint output, never both), and a device misconfigured with both
    goes through `switch_commands()` instead of getting two conflicting kinds of
    command from the same cycle.
    """
    actuator = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    thermostat = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    switch = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    if actuator is None or thermostat is None:
        return []

    rows = session.execute(
        select(Device, Integration.code)
        .join(ZoneDevice, ZoneDevice.device_id == Device.id)
        .join(Integration, Integration.id == Device.integration_id)
        .join(
            DeviceCapabilityLink,
            (DeviceCapabilityLink.device_id == Device.id)
            & (DeviceCapabilityLink.capability_id == thermostat.id),
        )
        .where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_role_id == actuator.id,
            ZoneDevice.self_regulating.is_(False),
        )
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    )

    commands: list[ThermostatCommand] = []
    for device, code in rows:
        if switch is not None and (
            session.scalar(
                select(DeviceCapabilityLink).where(
                    DeviceCapabilityLink.device_id == device.id,
                    DeviceCapabilityLink.capability_id == switch.id,
                )
            )
            is not None
        ):
            continue
        has_system_mode = (
            session.scalar(
                select(DeviceProperty).where(
                    DeviceProperty.device_id == device.id,
                    DeviceProperty.name == "system_mode",
                    DeviceProperty.is_writable.is_(True),
                )
            )
            is not None
        )
        commands.append(
            ThermostatCommand(
                device=device, integration_code=code, has_system_mode=has_system_mode
            )
        )
    return commands
