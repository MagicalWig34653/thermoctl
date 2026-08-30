# ruff: noqa: E501
"""Konfigurierbare Werte zwischen thermoctl und Bediengeraeten."""
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
from thermoctl.domain.remote_control import set_setpoint
from thermoctl.domain.zones import set_operating_mode

WRITE_KINDS = {"sensor_temperature", "zone_temperature", "zone_setpoint", "fixed"}
READ_KINDS = {"zone_setpoint", "operating_mode"}
log = logging.getLogger(__name__)


class ControllerChannelError(ValueError):
    """Ein Kanal ist fachlich ungueltig oder koennte einen Aktor umgehen."""


def _hat_rolle(session: Session, device: Device, code: str) -> bool:
    return session.scalar(
        select(ZoneDevice.id).join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.device_id == device.id, DeviceRole.code == code).limit(1)
    ) is not None


def darf_beschrieben_werden(session: Session, device: Device) -> bool:
    """Ob auf dieses Geraet ein Anzeigewert geschrieben werden darf.

    Zwei Bedingungen, nicht eine. Dass das Geraet **irgendwo** Bediengeraet ist, reicht
    nicht: Ein Thermostat kann in einer Zone Aktor sein und in einer anderen als
    Bediengeraet haengen -- es zeigt ja einen Sollwert an. Ein Schreibkanal auf sein
    `occupied_heating_setpoint` waere dann als Anzeige angemeldet und bewegte trotzdem
    ein Ventil, mit `switches=False` an beiden Riegeln des Trockenlaufs vorbei.

    Deshalb: Bediengeraet ja, Aktor nirgends. Wer ein Geraet wirklich beides sein lassen
    will, muss die Aktorrolle loesen -- und sieht dabei, was er tut.
    """
    return _hat_rolle(session, device, "controller") and not _hat_rolle(
        session, device, "actuator"
    )


def configure_channel(
    session: Session, device: Device, property_name: str, direction: str, kind_code: str,
    *, zone_id: int | None = None, source_device_id: int | None = None,
    fixed_text: str | None = None, fixed_number: Decimal | None = None,
) -> ControllerChannel:
    """Prueft und speichert einen Kanal; Schreibziele muessen Bediengeraete sein."""
    property_model = session.scalar(select(DeviceProperty).where(
        DeviceProperty.device_id == device.id, DeviceProperty.name == property_name))
    if property_model is None:
        raise ControllerChannelError("Das Gerät bietet dieses Merkmal nicht an.")
    if direction not in {"read", "write"}:
        raise ControllerChannelError("Die Kanalrichtung ist ungültig.")
    if direction == "write" and (not property_model.is_writable or not darf_beschrieben_werden(session, device)):
        raise ControllerChannelError(
            "Schreibkanäle sind nur auf Bediengeräten erlaubt, die nirgends Aktor sind."
        )
    if direction == "read" and not property_model.is_readable:
        raise ControllerChannelError("Dieses Merkmal ist nicht lesbar.")
    kind = session.scalar(select(ChannelKind).where(ChannelKind.code == kind_code))
    if kind is None or kind_code not in (WRITE_KINDS if direction == "write" else READ_KINDS):
        raise ControllerChannelError("Diese Kanalart passt nicht zur Richtung.")
    if kind_code in {"zone_temperature", "zone_setpoint", "operating_mode"} and session.get(Zone, zone_id) is None:
        raise ControllerChannelError("Für diese Kanalart ist eine Zone erforderlich.")
    if kind_code == "sensor_temperature" and session.get(Device, source_device_id) is None:
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
    """Wendet alle in der Nachricht vorhandenen Lesekanaele einmal an."""
    for channel, kind in session.execute(select(ControllerChannel, ChannelKind).join(ChannelKind).where(
        ControllerChannel.device_id == device.id, ControllerChannel.direction == "read")):
        if channel.property_name not in values or channel.zone_id is None:
            continue
        zone = session.get(Zone, channel.zone_id)
        if zone is None:  # pragma: no cover - Fremdschluessel haelt dagegen
            continue
        raw = values[channel.property_name]
        try:
            if kind.code == "zone_setpoint":
                set_setpoint(session, zone, Decimal(str(raw)), now, source="system")
            elif kind.code == "operating_mode":
                set_operating_mode(session, zone, str(raw), akteur_id=None, source="system")
        except ValueError as exc:
            log.warning("Wert aus Bediengeraetekanal abgewiesen", extra={"geraet": device.display_name, "merkmal": channel.property_name, "fehler": str(exc)})
