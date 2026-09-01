"""Which of a zone's actuators expect a plain on/off command.

Two kinds of device can carry the `actuator` role (`domain/device_assignment.py`): a
self-regulating thermostatic valve, told a setpoint (`domain/self_regulating.py`), and
an ordinary switch, told on or off by the hysteresis decision in
`domain/control_loop.py`. This module lists the second kind — the devices that still
need `regelung.entscheiden()`'s `heating` to reach them at all before anything
physically moves.

**Deliberately narrow: only the `switch` capability.** `domain/device_assignment.py`
also lets a device fill the actuator slot with the `thermostat` capability alone (a
Zigbee2MQTT TRV run without self-regulation, meant for `Zigbee2MqttThermostat`). That
path is not covered here — see the note in docs/offene-entscheidungen.md on why wiring
it was left open rather than guessed at under time pressure, on the most safety-relevant
change in this project.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
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
