# ruff: noqa: E501
"""Configurable values exchanged between thermoctl and controllers."""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import (
    ControllerChannel,
    Device,
    DeviceProperty,
    DevicePropertyValue,
    ZoneDevice,
)
from thermoctl.db.models.lookup import ChannelKind, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.modes import DomainError
from thermoctl.domain.remote_control import set_setpoint
from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode

WRITE_KINDS = {"sensor_temperature", "zone_temperature", "zone_setpoint", "fixed"}
READ_KINDS = {"zone_setpoint", "operating_mode"}
log = logging.getLogger(__name__)


class ControllerChannelError(ValueError):
    """A channel is invalid on domain grounds or could bypass an actuator."""


def _has_role(session: Session, device: Device, code: str) -> bool:
    return session.scalar(
        select(ZoneDevice.id).join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.device_id == device.id, DeviceRole.code == code).limit(1)
    ) is not None


def may_be_written(session: Session, device: Device) -> bool:
    """Whether a display value may be written to this device.

    Two conditions, not one. It is not enough that the device is a controller
    **somewhere**: a thermostat can be an actuator in one zone and hang as a
    controller in another -- after all, it does display a setpoint. A write channel
    onto its `occupied_heating_setpoint` would then be registered as a display value
    and would still move a valve, slipping past both of the dry run's bolts even with
    `switches=False`.

    Hence: controller yes, actuator nowhere. Whoever really wants a device to be both
    must remove the actuator role -- and sees what they are doing while doing it.
    """
    return _has_role(session, device, "controller") and not _has_role(
        session, device, "actuator"
    )


def configure_channel(
    session: Session, device: Device, property_name: str, direction: str, kind_code: str,
    *, zone_id: int | None = None, source_device_id: int | None = None,
    fixed_text: str | None = None, fixed_number: Decimal | None = None,
) -> ControllerChannel:
    """Validates and saves a channel; write targets must be controllers."""
    property_model = session.scalar(select(DeviceProperty).where(
        DeviceProperty.device_id == device.id, DeviceProperty.name == property_name))
    if property_model is None:
        raise ControllerChannelError("Das Gerät bietet dieses Merkmal nicht an.")
    if direction not in {"read", "write"}:
        raise ControllerChannelError("Die Kanalrichtung ist ungültig.")
    if direction == "write" and (not property_model.is_writable or not may_be_written(session, device)):
        raise ControllerChannelError(
            "Schreibkanäle sind nur auf Bediengeräten erlaubt, die nirgends Aktor sind."
        )
    if direction == "read" and not property_model.is_readable:
        raise ControllerChannelError("Dieses Merkmal ist nicht lesbar.")
    kind = session.scalar(select(ChannelKind).where(ChannelKind.code == kind_code))
    if kind is None or kind_code not in (WRITE_KINDS if direction == "write" else READ_KINDS):
        raise ControllerChannelError("Diese Kanalart passt nicht zur Richtung.")
    if kind_code in {"zone_temperature", "zone_setpoint", "operating_mode"} and (
        # `zone_id is None` first: `session.get(Zone, None)` looks up a NULL primary
        # key and SQLAlchemy warns about it -- correct result, noisy way to get there.
        zone_id is None or session.get(Zone, zone_id) is None
    ):
        raise ControllerChannelError("Für diese Kanalart ist eine Zone erforderlich.")
    if kind_code == "sensor_temperature" and (
        source_device_id is None or session.get(Device, source_device_id) is None
    ):
        raise ControllerChannelError("Für diese Kanalart ist ein Quellgerät erforderlich.")
    if kind_code == "fixed":
        value: object = fixed_number if property_model.value_type == "numeric" else fixed_text
        if value is None:
            raise ControllerChannelError("Der feste Wert fehlt.")
        allowed = set(session.scalars(select(DevicePropertyValue.value).where(DevicePropertyValue.property_id == property_model.id)))
        if allowed and str(value) not in allowed:
            raise ControllerChannelError("Der feste Wert ist für dieses Merkmal nicht erlaubt.")
        if fixed_number is not None and ((property_model.min_value is not None and fixed_number < property_model.min_value) or (property_model.max_value is not None and fixed_number > property_model.max_value)):
            raise ControllerChannelError("Der feste Wert liegt außerhalb des Wertebereichs.")
    channel = session.scalar(select(ControllerChannel).where(
        ControllerChannel.device_id == device.id, ControllerChannel.property_name == property_name))
    if channel is None:
        channel = ControllerChannel(device_id=device.id, property_name=property_name, direction=direction, kind_id=kind.id)
        session.add(channel)
    channel.direction, channel.kind_id = direction, kind.id
    channel.zone_id, channel.source_device_id = zone_id, source_device_id
    channel.fixed_text, channel.fixed_number = fixed_text or None, fixed_number
    session.flush()
    return channel


def apply_read_channels(session: Session, device: Device, values: dict[str, object], now: datetime) -> None:
    """Applies every read channel present in the message, once each."""
    for channel, kind in session.execute(select(ControllerChannel, ChannelKind).join(ChannelKind).where(
        ControllerChannel.device_id == device.id, ControllerChannel.direction == "read")):
        if channel.property_name not in values or channel.zone_id is None:
            continue
        zone = session.get(Zone, channel.zone_id)
        if zone is None:  # pragma: no cover - foreign key prevents this
            continue
        raw = values[channel.property_name]
        try:
            if kind.code == "zone_setpoint":
                set_setpoint(session, zone, Decimal(str(raw)), now, source="system")
            elif kind.code == "operating_mode":
                set_operating_mode(session, zone, str(raw), actor_id=None, source="system")
        except (ValueError, DomainError, UnknownOperatingMode) as exc:
            # `DomainError` and `UnknownOperatingMode` derive from `Exception`, not from
            # `ValueError` -- catching only the latter let a dial turned to 99 degrees
            # escape all the way out of the message handler. One rejected value would
            # then have aborted the whole payload, including the readings in it.
            log.warning("Wert aus Bediengeraetekanal abgewiesen", extra={"geraet": device.display_name, "merkmal": channel.property_name, "fehler": str(exc)})
