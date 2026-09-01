from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone


class AssignmentAlreadyExists(Exception):
    """The device already has this role in the zone."""


class CapabilityMissing(Exception):
    """The device cannot do what the role requires of it."""

    def __init__(self, notice: str) -> None:
        super().__init__(notice)
        self.notice = notice


# What a slot in the plant requires from a device. The keys are the roles' codes,
# plus `"temperature_source"` -- it is not a role in the sense of `device_role`, but a column
# on the zone, yet needs the same check.
#
# `controller` is deliberately absent here: a device that only displays does not need
# any ability that shows up in the capabilities. A caller passing `None` is asking
# about a slot with no requirement.
TEMPERATURE_SOURCE = "temperature_source"

# The actuator slot accepts either of two capabilities: a plain switch output (a
# smart-plug valve, e.g. Meross) or a thermostat (a Zigbee2MQTT TRV such as the
# WT-A03E, driven through `system_mode` and `occupied_heating_setpoint` instead of
# an on/off `state`). Both move a real valve; which one a device has decides only
# which adapter builds its command, not whether it may fill this slot.
REQUIRED_CAPABILITY: dict[str, tuple[frozenset[str], str]] = {
    TEMPERATURE_SOURCE: (frozenset({"temperature"}), "misst keine Temperatur"),
    "actuator": (frozenset({"switch", "thermostat"}), "hat keinen Schaltausgang"),
    "window_contact": (frozenset({"contact"}), "meldet keinen Kontakt"),
}


def _capabilities(session: Session, device: Device) -> set[str]:
    return set(
        session.scalars(
            select(DeviceCapability.code)
            .join(
                DeviceCapabilityLink,
                DeviceCapabilityLink.capability_id == DeviceCapability.id,
            )
            .where(DeviceCapabilityLink.device_id == device.id)
        )
    )


def check_capability(session: Session, device: Device, slot: str | None) -> None:
    """Rejects a device that can demonstrably not fill this slot.

    Previously, a temperature sensor could be assigned as an actuator. The assignment
    then looked correct, the plant diagram showed a complete path, and yet nothing
    would ever actually switch -- a bug that only shows up in winter and then looks
    like a control-logic bug.

    **Only on demonstrable contradiction.** A device for which no capability at all is
    known is let through: the capabilities come from the bridge's device list, and
    whoever connects a device that describes itself sparsely there should still be
    able to set up their plant. It is only rejected where something is known **and**
    the required thing is not among it.
    """
    required = REQUIRED_CAPABILITY.get(slot or "")
    if required is None:
        return
    codes, defect = required
    present = _capabilities(session, device)
    if not present or codes & present:
        return
    labels = list(
        session.scalars(
            select(DeviceCapability.label)
            .where(DeviceCapability.code.in_(codes))
            .order_by(DeviceCapability.code)
        )
    )
    label_text = " oder ".join(labels) if labels else " oder ".join(sorted(codes))
    raise CapabilityMissing(
        f"'{device.display_name}' {defect} — für diese Stelle wird "
        f"'{label_text}' gebraucht."
    )


def assign_device(
    session: Session,
    zone: Zone,
    device: Device,
    role: DeviceRole,
    *,
    actor_id: int | None,
    source: str = "web",
) -> ZoneDevice:
    existing = session.scalar(
        select(ZoneDevice.id).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_id == device.id,
            ZoneDevice.device_role_id == role.id,
        )
    )
    if existing is not None:
        raise AssignmentAlreadyExists
    check_capability(session, device, role.code)
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=role.id
    )
    session.add(assignment)
    session.flush()
    audit.record(
        session,
        source=source,
        action="assign",
        object_type="zone_device",
        object_id=str(assignment.id),
        summary=(
            f"Gerät '{device.display_name}' als {role.label} "
            f"zu '{zone.display_name}' zugeordnet"
        ),
        user_id=actor_id,
    )
    return assignment


def detach_device(
    session: Session,
    zone: Zone,
    assignment: ZoneDevice,
    *,
    actor_id: int | None,
    source: str = "web",
) -> None:
    if assignment.zone_id != zone.id:
        raise ValueError("Die Zuordnung gehört nicht zu dieser Zone.")
    device = session.get(Device, assignment.device_id)
    role = session.get(DeviceRole, assignment.device_role_id)
    session.delete(assignment)
    audit.record(
        session,
        source=source,
        action="unassign",
        object_type="zone_device",
        object_id=str(assignment.id),
        summary=(
            f"Gerät '{device.display_name if device else assignment.device_id}' als "
            f"{role.label if role else assignment.device_role_id} aus "
            f"'{zone.display_name}' gelöst"
        ),
        user_id=actor_id,
    )


def set_self_regulating(
    session: Session,
    zone: Zone,
    assignment: ZoneDevice,
    self_regulating: bool,
    *,
    actor_id: int | None,
    source: str = "web",
) -> None:
    """Switches a thermostatic valve between the two ways of running it.

    Only for the actuator role, and only for a device that is a thermostat: nothing
    else regulates on its own, and offering the choice there would promise something
    that cannot happen.

    Recorded in the audit log because it changes how a real valve is driven. In
    self-regulating mode thermoctl stops switching this device -- hysteresis, minimum
    switching duration and the window contact then act only through the setpoint that
    is written, not through an on/off command. That is a decision somebody should be
    able to look up afterwards.
    """
    if assignment.zone_id != zone.id:
        raise ValueError("Die Zuordnung gehört nicht zu dieser Zone.")
    role = session.get(DeviceRole, assignment.device_role_id)
    device = session.get(Device, assignment.device_id)
    if role is None or role.code != "actuator":
        raise CapabilityMissing("Nur ein Aktor kann selbst regeln.")
    if device is None or "thermostat" not in _capabilities(session, device):
        raise CapabilityMissing(
            "Nur ein Thermostatventil kann selbst regeln — dieses Gerät ist keines."
        )

    if assignment.self_regulating == self_regulating:
        return
    assignment.self_regulating = self_regulating
    audit.record(
        session,
        source=source,
        action="update",
        object_type="zone_device",
        object_id=str(assignment.id),
        summary=(
            f"'{device.display_name}' in '{zone.display_name}' regelt "
            + ("jetzt selbst" if self_regulating else "nicht mehr selbst")
        ),
        detail=(
            "thermoctl schreibt nur noch Soll- und Ist-Temperatur"
            if self_regulating
            else "thermoctl entscheidet wieder selbst über die Ein/Aus-Anforderung; "
            "derzeit ist dafür kein Aktor verdrahtet"
        ),
        user_id=actor_id,
    )


def set_temperature_source(
    session: Session,
    zone: Zone,
    device: Device | None,
    *,
    actor_id: int | None,
    source: str = "web",
) -> None:
    if device is not None:
        check_capability(session, device, TEMPERATURE_SOURCE)
    zone.temperature_source_device_id = device.id if device is not None else None
    audit.record(
        session,
        source=source,
        action="assign" if device is not None else "unassign",
        object_type="zone_temperature_source",
        object_id=str(zone.id),
        summary=(
            f"Messquelle von '{zone.display_name}' auf '{device.display_name}' gesetzt"
            if device is not None
            else f"Messquelle von '{zone.display_name}' gelöst"
        ),
        user_id=actor_id,
    )


def swap_device(
    session: Session,
    zone: Zone,
    old: Device,
    new_link: Device,
    *,
    actor_id: int | None,
    source: str = "web",
) -> None:
    """Replaces a device only in its assignments to this zone."""
    if old.id == new_link.id:
        raise ValueError("Altes und neues Gerät müssen verschieden sein.")

    old_assignments = list(
        session.scalars(
            select(ZoneDevice).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == old.id
            )
        )
    )
    war_temperature_source = zone.temperature_source_device_id == old.id
    if not old_assignments and not war_temperature_source:
        raise ValueError("Das alte Gerät ist dieser Zone nicht zugeordnet.")

    # Before the first write, and for **every** slot being carried over: swapping is
    # the quietest way to put an unsuitable device into a slot -- you pick two names
    # and never even see which roles come along with them. Check first, then write,
    # otherwise the swap would be left half-done after a rejection.
    if war_temperature_source:
        check_capability(session, new_link, TEMPERATURE_SOURCE)
    for assignment in old_assignments:
        role = session.get(DeviceRole, assignment.device_role_id)
        if role is not None:
            check_capability(session, new_link, role.code)

    existing_roles = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == new_link.id
            )
        )
    )
    for assignment in old_assignments:
        if assignment.device_role_id not in existing_roles:
            session.add(
                ZoneDevice(
                    zone_id=zone.id,
                    device_id=new_link.id,
                    device_role_id=assignment.device_role_id,
                    sort_order=assignment.sort_order,
                )
            )
        session.delete(assignment)
    if war_temperature_source:
        zone.temperature_source_device_id = new_link.id

    audit.record(
        session,
        source=source,
        action="replace",
        object_type="zone_device",
        object_id=str(zone.id),
        summary=(
            f"Gerät '{old.display_name}' in '{zone.display_name}' durch "
            f"'{new_link.display_name}' ersetzt"
        ),
        user_id=actor_id,
        detail=f"altes_geraet_id={old.id}; neues_geraet_id={new_link.id}",
    )
    session.flush()
