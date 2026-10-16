"""Geraetezuordnung einer Zone: Kontext fuer Alt-View und /ui-api."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone
from thermoctl.domain.controller import seen_actions
from thermoctl.domain.device_assignment import REQUIRED_CAPABILITY
from thermoctl.domain.plant_diagram import plant_diagram
from thermoctl.domain.thermostat_mode import MODE_DESCRIPTIONS, MODE_LABELS, MODES


def assignment_context(session: Session, zone: Zone, **extra: object) -> dict[str, object]:
    devices = session.scalars(select(Device).order_by(Device.display_name, Device.id)).all()
    roles = session.scalars(select(DeviceRole).order_by(DeviceRole.id)).all()
    assignments = session.execute(
        select(ZoneDevice, Device, DeviceRole)
        .join(Device, Device.id == ZoneDevice.device_id)
        .join(DeviceRole, DeviceRole.id == ZoneDevice.device_role_id)
        .where(ZoneDevice.zone_id == zone.id)
        .order_by(DeviceRole.id, ZoneDevice.sort_order, Device.display_name)
    ).all()
    temperature_source = (
        session.get(Device, zone.temperature_source_device_id)
        if zone.temperature_source_device_id is not None
        else None
    )
    capabilities: dict[int, list[str]] = {}
    for device_id, code in session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code).join(
            DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id
        )
    ):
        capabilities.setdefault(device_id, []).append(code)

    # Per controller in this zone: which buttons it has sent and what they do.
    # Without this list, someone would have to know what their model calls its
    # buttons -- `single_plus`, `button_1_single`, `up_open`, depending on manufacturer.
    controllers = [
        (device, seen_actions(session, device))
        for assignment, device, role in assignments
        if role.code == "controller"
    ]

    return {
        "zone": zone,
        "capabilities": capabilities,
        "thermostat_modes": [
            (code, MODE_LABELS[code], MODE_DESCRIPTIONS[code]) for code in MODES
        ],
        # Welche Fähigkeit eine Stelle verlangt, entscheidet die Domäne -- die
        # Vorlage schreibt es nicht noch einmal hin. Die Aktor-Stelle nimmt seit den
        # Thermostatventilen `switch` **oder** `thermostat`; stand das in der Vorlage
        # weiter als einzelnes Wort, wies das Ziehen ein Ventil ab, das die Domäne
        # angenommen hätte.
        "required_capabilities": {
            slot: " ".join(sorted(codes)) for slot, (codes, _) in REQUIRED_CAPABILITY.items()
        },
        "controllers": controllers,
        "commands": session.scalars(
            select(ControllerCommand).order_by(ControllerCommand.id)
        ).all(),
        # The same plant diagram as on /anlage, here for this one zone. It sits above
        # the forms because it answers the question you arrive with -- what's wired
        # up here and what's missing -- before you change anything.
        "picture": plant_diagram(session, [zone]).zones[0],
        "devices": devices,
        "roles": roles,
        "assignments": assignments,
        "temperature_source": temperature_source,
        "errors": {},
        **extra,
    }
