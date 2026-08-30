from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.zone import Zone


class AssignmentAlreadyExists(Exception):
    """Das Geraet hat diese Rolle in der Zone bereits."""


class CapabilityMissing(Exception):
    """Das Geraet kann nicht, was die Rolle von ihm verlangt."""

    def __init__(self, notice: str) -> None:
        super().__init__(notice)
        self.notice = notice


# Was eine Stelle in der Anlage von einem Geraet verlangt. Die Schluessel sind die Codes
# der Rollen, dazu `"messquelle"` -- sie ist keine Rolle im Sinne von `device_role`,
# sondern eine Spalte an der Zone, braucht aber dieselbe Pruefung.
#
# `controller` (Bediengeraet) steht bewusst nicht darin: Ein Geraet, das nur anzeigt,
# muss dafuer nichts koennen, was sich in den Faehigkeiten niederschlaegt. Ein Aufrufer
# mit `None` fragt nach einer Stelle ohne Anforderung.
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


def check_capability(session: Session, device: Device, stelle: str | None) -> None:
    """Weist ein Geraet ab, das die Stelle nachweislich nicht ausfuellen kann.

    Vorher liess sich ein Temperatursensor als Aktor zuordnen. Die Zuordnung sah danach
    richtig aus, das Anlagenbild zeigte einen vollstaendigen Weg, und geschaltet haette
    trotzdem nie etwas -- ein Fehler, der erst im Winter auffaellt und dann nach einem
    Regelungsfehler aussieht.

    **Nur bei nachweislichem Widerspruch.** Ein Geraet, von dem ueberhaupt keine
    Faehigkeit bekannt ist, wird durchgelassen: Die Faehigkeiten stammen aus der
    Geraeteliste der Bruecke, und wer ein Geraet einbindet, das sich dort sparsam
    beschreibt, soll seine Anlage trotzdem einrichten koennen. Abgewiesen wird nur, wo
    etwas bekannt ist **und** das Noetige nicht dabei ist.
    """
    verlangt = REQUIRED_CAPABILITY.get(stelle or "")
    if verlangt is None:
        return
    code, mangel = verlangt
    vorhanden = _capabilities(session, device)
    if not vorhanden or code in vorhanden:
        return
    bezeichnung = session.scalar(
        select(DeviceCapability.label).where(DeviceCapability.code == code)
    )
    raise CapabilityMissing(
        f"'{device.display_name}' {mangel} — für diese Stelle wird "
        f"'{bezeichnung or code}' gebraucht."
    )


def assign_device(
    session: Session,
    zone: Zone,
    device: Device,
    rolle: DeviceRole,
    *,
    akteur_id: int | None,
    source: str = "web",
) -> ZoneDevice:
    vorhanden = session.scalar(
        select(ZoneDevice.id).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_id == device.id,
            ZoneDevice.device_role_id == rolle.id,
        )
    )
    if vorhanden is not None:
        raise AssignmentAlreadyExists
    check_capability(session, device, rolle.code)
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=rolle.id
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
            f"Gerät '{device.display_name}' als {rolle.label} "
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
    rolle = session.get(DeviceRole, assignment.device_role_id)
    session.delete(assignment)
    audit.record(
        session,
        source=source,
        action="unassign",
        object_type="zone_device",
        object_id=str(assignment.id),
        summary=(
            f"Gerät '{device.display_name if device else assignment.device_id}' als "
            f"{rolle.label if rolle else assignment.device_role_id} aus "
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
    """Ersetzt ein Geraet ausschliesslich in seinen Zuordnungen zu dieser Zone."""
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

    # Vor dem ersten Schreibzugriff, und fuer **jede** Stelle, die uebergeht: Der Tausch
    # ist der stillste Weg, ein unpassendes Geraet an eine Stelle zu setzen -- man waehlt
    # zwei Namen aus und sieht gar nicht, welche Rollen dabei mitgehen. Erst pruefen,
    # dann schreiben, sonst bliebe der Tausch nach einer Ablehnung halb ausgefuehrt.
    if war_temperature_source:
        check_capability(session, neues, TEMPERATURE_SOURCE)
    for assignment in old_assignments:
        rolle = session.get(DeviceRole, assignment.device_role_id)
        if rolle is not None:
            check_capability(session, neues, rolle.code)

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
