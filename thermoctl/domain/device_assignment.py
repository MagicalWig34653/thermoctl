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
# plus `"messquelle"` -- it is not a role in the sense of `device_role`, but a column
# on the zone, yet needs the same check.
#
# `controller` is deliberately absent here: a device that only displays does not need
# any ability that shows up in the capabilities. A caller passing `None` is asking
# about a slot with no requirement.
TEMPERATURE_SOURCE = "messquelle"

REQUIRED_CAPABILITY: dict[str, tuple[str, str]] = {
    TEMPERATURE_SOURCE: ("temperature", "misst keine Temperatur"),
    "actuator": ("switch", "hat keinen Schaltausgang"),
    "window_contact": ("contact", "meldet keinen Kontakt"),
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
    verlangt = REQUIRED_CAPABILITY.get(slot or "")
    if verlangt is None:
        return
    code, mangel = verlangt
    vorhanden = _capabilities(session, device)
    if not vorhanden or code in vorhanden:
        return
    label = session.scalar(
        select(DeviceCapability.label).where(DeviceCapability.code == code)
    )
    raise CapabilityMissing(
        f"'{device.display_name}' {mangel} — für diese Stelle wird "
        f"'{label or code}' gebraucht."
    )


def assign_device(
    session: Session,
    zone: Zone,
    device: Device,
    role: DeviceRole,
    *,
    akteur_id: int | None,
    source: str = "web",
) -> ZoneDevice:
    vorhanden = session.scalar(
        select(ZoneDevice.id).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_id == device.id,
            ZoneDevice.device_role_id == role.id,
        )
    )
    if vorhanden is not None:
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
        user_id=akteur_id,
    )
    return assignment


def detach_device(
    session: Session,
    zone: Zone,
    assignment: ZoneDevice,
    *,
    akteur_id: int | None,
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
        user_id=akteur_id,
    )


def set_temperature_source(
    session: Session,
    zone: Zone,
    device: Device | None,
    *,
    akteur_id: int | None,
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
        user_id=akteur_id,
    )


def swap_device(
    session: Session,
    zone: Zone,
    altes: Device,
    neues: Device,
    *,
    akteur_id: int | None,
    source: str = "web",
) -> None:
    """Replaces a device only in its assignments to this zone."""
    if altes.id == neues.id:
        raise ValueError("Altes und neues Gerät müssen verschieden sein.")

    old_assignments = list(
        session.scalars(
            select(ZoneDevice).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == altes.id
            )
        )
    )
    war_temperature_source = zone.temperature_source_device_id == altes.id
    if not old_assignments and not war_temperature_source:
        raise ValueError("Das alte Gerät ist dieser Zone nicht zugeordnet.")

    # Before the first write, and for **every** slot being carried over: swapping is
    # the quietest way to put an unsuitable device into a slot -- you pick two names
    # and never even see which roles come along with them. Check first, then write,
    # otherwise the swap would be left half-done after a rejection.
    if war_temperature_source:
        check_capability(session, neues, TEMPERATURE_SOURCE)
    for assignment in old_assignments:
        role = session.get(DeviceRole, assignment.device_role_id)
        if role is not None:
            check_capability(session, neues, role.code)

    vorhandene_rolen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    for assignment in old_assignments:
        if assignment.device_role_id not in vorhandene_rolen:
            session.add(
                ZoneDevice(
                    zone_id=zone.id,
                    device_id=neues.id,
                    device_role_id=assignment.device_role_id,
                    sort_order=assignment.sort_order,
                )
            )
        session.delete(assignment)
    if war_temperature_source:
        zone.temperature_source_device_id = neues.id

    audit.record(
        session,
        source=source,
        action="replace",
        object_type="zone_device",
        object_id=str(zone.id),
        summary=(
            f"Gerät '{altes.display_name}' in '{zone.display_name}' durch "
            f"'{neues.display_name}' ersetzt"
        ),
        user_id=akteur_id,
        detail=f"altes_geraet_id={altes.id}; neues_geraet_id={neues.id}",
    )
    session.flush()
