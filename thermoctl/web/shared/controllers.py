# ruff: noqa: E501
"""Steuergeraete-Seite: Zonen-/Geraeteauswahl und Rechtepruefung -- geteilt von den /ui-api-Endpunkten."""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import (
    ControllerChannel,
    Device,
    DeviceProperty,
    DevicePropertyValue,
    ZoneDevice,
)
from thermoctl.db.models.lookup import ChannelKind, ControllerCommand, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import require, visible_zones
from thermoctl.domain.controller import seen_actions
from thermoctl.domain.principal import Principal


def zones_with_permission(session: Session, principal: Principal, permission: str) -> list[Zone]:
    return list(visible_zones(session, principal, permission))


def controller_devices(session: Session, zone_ids: list[int]) -> list[Device]:
    if not zone_ids:
        return []
    return list(session.scalars(select(Device).join(ZoneDevice).join(DeviceRole).where(
        ZoneDevice.zone_id.in_(zone_ids), DeviceRole.code == "controller"
    ).order_by(Device.display_name).distinct()))


def devices_in_zones(session: Session, zone_ids: list[int]) -> list[Device]:
    """Every device hanging in one of these zones, in any role -- plus each zone's
    temperature source.

    Deliberately not the whole device table: the source-device picker used to list
    every device in the installation, so a user with `device.read` for a single zone
    learned the names of all the others -- and device names in this project carry
    room, occupant and integration references.

    A plain room-temperature sensor never gets a `ZoneDevice` row: there is no
    `sensor` role for it (`DEVICE_ROLES` only holds `actuator`, `window_contact`,
    `controller`), and it hangs off a zone solely through
    `zone.temperature_source_device_id` (see `domain/device_assignment.py`'s
    `TEMPERATURE_SOURCE` and `set_temperature_source`). A join over `ZoneDevice`
    alone would show actuators and controllers here but never the sensor the
    'Temperaturquellen' pool exists for -- adding this second source instead of a
    new role, since the zone already names its temperature source unambiguously.
    """
    if not zone_ids:
        return []
    via_role = select(Device.id).join(ZoneDevice).where(ZoneDevice.zone_id.in_(zone_ids))
    via_temperature_source = select(Zone.temperature_source_device_id).where(
        Zone.id.in_(zone_ids), Zone.temperature_source_device_id.is_not(None)
    )
    device_ids = via_role.union(via_temperature_source)
    return list(session.scalars(select(Device).where(
        Device.id.in_(device_ids)).order_by(Device.display_name).distinct()))


def require_manageable_zone(session: Session, principal: Principal, zone_id: int) -> None:
    """The channel's target zone, checked against the principal -- not just the device.

    A read channel on `zone_setpoint` or `operating_mode` writes into whatever zone it
    names, so naming a zone here is control over that zone. Checking only that the
    *device* sits in a manageable zone let `device.manage` for zone A point a channel
    at zone B and switch B off from then on, on every turn of the dial.
    """
    require(principal, "device.manage", zone_id)


def require_readable_device(session: Session, principal: Principal, device_id: int) -> None:
    """A channel's source device must sit in a zone the principal may read.

    Same two sources as `devices_in_zones()`: a `ZoneDevice` role or a zone's temperature
    source. Checking only the former used to 404 a room sensor the page itself had
    just offered as a channel source.
    """
    zone_ids = [zone.id for zone in zones_with_permission(session, principal, "device.read")]
    if zone_ids and device_id in {
        device.id for device in devices_in_zones(session, zone_ids)
    }:
        return
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Quellgerät nicht gefunden")


def controllers_context(session: Session, principal: Principal, **extra: object) -> dict[str, object]:
    readable_zones = zones_with_permission(session, principal, "device.read")
    manageable_zones = zones_with_permission(session, principal, "device.manage")
    manageable_ids = {zone.id for zone in manageable_zones}
    controllers = controller_devices(session, [zone.id for zone in readable_zones])
    # `manageable_ids` above holds *zone* ids -- `device.manage` is a zone-scoped
    # permission, a controller device carries none of its own. The edit form in
    # `controllers.html` is about a specific *device*, so it needs the set of
    # device ids the principal may in fact manage: exactly the controllers that
    # sit in one of those zones. This mirrors `managed_device()` below, which
    # both POST endpoints already check independently of anything the template
    # decides to show -- this only controls visibility, it does not widen what a
    # POST accepts.
    manageable_device_ids = {
        device.id for device in controller_devices(session, [zone.id for zone in manageable_zones])
    }
    properties: dict[int, list[DeviceProperty]] = {}
    values: dict[int, list[str]] = {}
    channels: dict[tuple[int, str], ControllerChannel] = {}
    for property_model in session.scalars(select(DeviceProperty).where(DeviceProperty.device_id.in_([d.id for d in controllers])).order_by(DeviceProperty.name)):
        properties.setdefault(property_model.device_id, []).append(property_model)
        values[property_model.id] = list(session.scalars(select(DevicePropertyValue.value).where(DevicePropertyValue.property_id == property_model.id).order_by(DevicePropertyValue.sort_order)))
    for channel in session.scalars(select(ControllerChannel).where(ControllerChannel.device_id.in_([d.id for d in controllers]))):
        channels[(channel.device_id, channel.property_name)] = channel
    return {
        "controllers": controllers, "properties": properties, "property_values": values,
        "channels": channels, "zones": readable_zones, "manageable_ids": manageable_ids,
        "manageable_device_ids": manageable_device_ids,
        "devices": devices_in_zones(session, [zone.id for zone in readable_zones]),
        "kinds": {kind.id: kind for kind in session.scalars(select(ChannelKind))},
        "commands": session.scalars(select(ControllerCommand).order_by(ControllerCommand.id)).all(),
        "bindings": {device.id: seen_actions(session, device) for device in controllers},
        "errors": {}, **extra,
    }


def managed_device(session: Session, principal: Principal, device_id: int) -> Device:
    zones = zones_with_permission(session, principal, "device.manage")
    device = session.scalar(select(Device).join(ZoneDevice).join(DeviceRole).where(
        Device.id == device_id, ZoneDevice.zone_id.in_([z.id for z in zones]), DeviceRole.code == "controller"))
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bediengerät nicht gefunden")
    return device
