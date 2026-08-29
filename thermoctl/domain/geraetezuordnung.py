from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import Device, ZoneDevice
from thermoctl.db.models.lookup import DeviceRole
from thermoctl.db.models.zone import Zone


class ZuordnungBereitsVorhanden(Exception):
    """Das Geraet hat diese Rolle in der Zone bereits."""


def geraet_zuordnen(
    session: Session,
    zone: Zone,
    geraet: Device,
    rolle: DeviceRole,
    *,
    akteur_id: int | None,
    quelle: str = "web",
) -> ZoneDevice:
    vorhanden = session.scalar(
        select(ZoneDevice.id).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_id == geraet.id,
            ZoneDevice.device_role_id == rolle.id,
        )
    )
    if vorhanden is not None:
        raise ZuordnungBereitsVorhanden
    zuordnung = ZoneDevice(
        zone_id=zone.id, device_id=geraet.id, device_role_id=rolle.id
    )
    session.add(zuordnung)
    session.flush()
    audit.record(
        session,
        source=quelle,
        action="assign",
        object_type="zone_device",
        object_id=str(zuordnung.id),
        summary=(
            f"Gerät '{geraet.display_name}' als {rolle.label} "
            f"zu '{zone.display_name}' zugeordnet"
        ),
        user_id=akteur_id,
    )
    return zuordnung


def geraet_loesen(
    session: Session,
    zone: Zone,
    zuordnung: ZoneDevice,
    *,
    akteur_id: int | None,
    quelle: str = "web",
) -> None:
    if zuordnung.zone_id != zone.id:
        raise ValueError("Die Zuordnung gehört nicht zu dieser Zone.")
    geraet = session.get(Device, zuordnung.device_id)
    rolle = session.get(DeviceRole, zuordnung.device_role_id)
    session.delete(zuordnung)
    audit.record(
        session,
        source=quelle,
        action="unassign",
        object_type="zone_device",
        object_id=str(zuordnung.id),
        summary=(
            f"Gerät '{geraet.display_name if geraet else zuordnung.device_id}' als "
            f"{rolle.label if rolle else zuordnung.device_role_id} aus "
            f"'{zone.display_name}' gelöst"
        ),
        user_id=akteur_id,
    )


def messquelle_setzen(
    session: Session,
    zone: Zone,
    geraet: Device | None,
    *,
    akteur_id: int | None,
    quelle: str = "web",
) -> None:
    zone.temperature_source_device_id = geraet.id if geraet is not None else None
    audit.record(
        session,
        source=quelle,
        action="assign" if geraet is not None else "unassign",
        object_type="zone_temperature_source",
        object_id=str(zone.id),
        summary=(
            f"Messquelle von '{zone.display_name}' auf '{geraet.display_name}' gesetzt"
            if geraet is not None
            else f"Messquelle von '{zone.display_name}' gelöst"
        ),
        user_id=akteur_id,
    )


def geraet_tauschen(
    session: Session,
    zone: Zone,
    altes: Device,
    neues: Device,
    *,
    akteur_id: int | None,
    quelle: str = "web",
) -> None:
    """Ersetzt ein Geraet ausschliesslich in seinen Zuordnungen zu dieser Zone."""
    if altes.id == neues.id:
        raise ValueError("Altes und neues Gerät müssen verschieden sein.")

    alte_zuordnungen = list(
        session.scalars(
            select(ZoneDevice).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == altes.id
            )
        )
    )
    war_messquelle = zone.temperature_source_device_id == altes.id
    if not alte_zuordnungen and not war_messquelle:
        raise ValueError("Das alte Gerät ist dieser Zone nicht zugeordnet.")

    vorhandene_rollen = set(
        session.scalars(
            select(ZoneDevice.device_role_id).where(
                ZoneDevice.zone_id == zone.id, ZoneDevice.device_id == neues.id
            )
        )
    )
    for zuordnung in alte_zuordnungen:
        if zuordnung.device_role_id not in vorhandene_rollen:
            session.add(
                ZoneDevice(
                    zone_id=zone.id,
                    device_id=neues.id,
                    device_role_id=zuordnung.device_role_id,
                    sort_order=zuordnung.sort_order,
                )
            )
        session.delete(zuordnung)
    if war_messquelle:
        zone.temperature_source_device_id = neues.id

    audit.record(
        session,
        source=quelle,
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
