"""What device does what, where -- as a picture instead of a list.

The assignment of devices to zones lives in three tables: `zone.temperature_source_device_id`
for the temperature source, `zone_device` with a role for actuators and window contacts,
and `device_capability_link` for what a device can do at all. Anyone wanting to know why
a room stays cold has to piece these three together in their head.

The path through the plant is always the same, and always in one direction:

    Bridge → temperature source ─┐
                                 ├→ zone (current, setpoint, decision) → actuators
       window contacts ─────────┘

This function depicts exactly that. It computes nothing that does not already exist --
it assembles what is scattered across five queries, and names the gaps: a zone without
a temperature source cannot control anything, one without an actuator cannot act on
anything, and a device without a zone does nothing at all.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.device_assignment import REQUIRED_CAPABILITY, TEMPERATURE_SOURCE


@dataclass(frozen=True)
class DevicePicture:
    id: int
    name: str
    modell: str | None
    capabilities: list[str]
    active: bool
    # Only set when this device demonstrably cannot do what is required of it at this
    # slot. The check at assignment time prevents new such cases; the old ones already
    # sit in the database and would otherwise never stand out.
    ungeeignet: str | None = None
    # The id of the `zone_device` row through which this device hangs at this slot --
    # None for the temperature source (that is a column on the zone, not a row) and
    # for devices without a zone. The interface needs it to be able to pull a device
    # back out.
    assignment_id: int | None = None


@dataclass(frozen=True)
class ZonePicture:
    zone_id: int
    name: str
    temperature_source: DevicePicture | None
    window_contacts: list[DevicePicture] = field(default_factory=list)
    actuators: list[DevicePicture] = field(default_factory=list)
    controllers: list[DevicePicture] = field(default_factory=list)

    @property
    def maengel(self) -> list[str]:
        """What is stopping this zone from controlling anything. Empty means: fully wired."""
        fehlt = []
        if self.temperature_source is None:
            fehlt.append("keine Messquelle — ohne Ist-Wert entscheidet die Regelung nichts")
        if not self.actuators:
            fehlt.append("kein Aktor — die Entscheidung erreicht kein Ventil")
        for device in [self.temperature_source, *self.window_contacts, *self.actuators]:
            if device is not None and device.ungeeignet:
                fehlt.append(device.ungeeignet)
        return fehlt


@dataclass(frozen=True)
class PlantDiagram:
    zones: list[ZonePicture]
    without_zone: list[DevicePicture]


def _picture(
    device: Device,
    capabilities: dict[int, list[str]],
    codes: dict[int, set[str]],
    slot: str | None = None,
    assignment_id: int | None = None,
) -> DevicePicture:
    # `None` means "no requirement" -- that is how devices without a zone are shown,
    # since nothing is required of them. Without this distinction, an ownerless valve
    # would have been flagged as "misst keine Temperatur".
    verlangt = REQUIRED_CAPABILITY.get(slot or "")
    ungeeignet = None
    kann = codes.get(device.id, set())
    if verlangt is not None and kann and verlangt[0] not in kann:
        ungeeignet = (
            f"'{device.display_name}' {verlangt[1]} — diese Zuordnung wirkt nicht"
        )
    return DevicePicture(
        id=device.id,
        name=device.display_name,
        modell=device.model,
        capabilities=sorted(capabilities.get(device.id, [])),
        active=device.is_enabled,
        ungeeignet=ungeeignet,
        assignment_id=assignment_id,
    )


def plant_diagram(session: Session, zones: list[Zone]) -> PlantDiagram:
    """The path through the plant, per zone -- and what lies outside every zone."""
    devices = {g.id: g for g in session.scalars(select(Device))}
    capabilities: dict[int, list[str]] = {}
    for device_id, label in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.label).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        capabilities.setdefault(device_id, []).append(label)

    # The codes kept separate from the labels: one set is for the reader, the other
    # for comparison against REQUIRED_CAPABILITY.
    codes: dict[int, set[str]] = {}
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        codes.setdefault(device_id, set()).add(code)

    roles = {r.id: r.code for r in session.scalars(select(DeviceRole))}
    by_zone: dict[int, dict[str, list[DevicePicture]]] = {
        zone.id: {"actuator": [], "window_contact": [], "controller": []} for zone in zones
    }
    zugeordnet: set[int] = set()
    for assignment in session.scalars(
        select(ZoneDevice)
        .where(ZoneDevice.zone_id.in_([zone.id for zone in zones]))
        .order_by(ZoneDevice.sort_order, ZoneDevice.id)
    ):
        device = devices.get(assignment.device_id)
        role = roles.get(assignment.device_role_id)
        if device is None or role not in by_zone[assignment.zone_id]:
            continue
        by_zone[assignment.zone_id][role].append(
            _picture(device, capabilities, codes, role, assignment.id)
        )
        zugeordnet.add(device.id)

    pictures = []
    for zone in zones:
        source = devices.get(zone.temperature_source_device_id or 0)
        if source is not None:
            zugeordnet.add(source.id)
        pictures.append(
            ZonePicture(
                zone_id=zone.id,
                name=zone.display_name,
                temperature_source=(
                    _picture(source, capabilities, codes, TEMPERATURE_SOURCE) if source else None
                ),
                window_contacts=by_zone[zone.id]["window_contact"],
                actuators=by_zone[zone.id]["actuator"],
                controllers=by_zone[zone.id]["controller"],
            )
        )

    # Devices assigned to no zone. They do report values, but control logic does not
    # see them -- and that is the most common reason a newly connected sensor "does
    # not show up".
    without_zone = [
        _picture(device, capabilities, codes)
        for device in sorted(devices.values(), key=lambda g: g.display_name)
        if device.id not in zugeordnet
    ]
    return PlantDiagram(zones=pictures, without_zone=without_zone)
