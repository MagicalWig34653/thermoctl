from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.principal import Principal


class UnknownOperatingMode(Exception):
    """The requested operating mode does not exist."""


class ZonennameVergeben(Exception):
    """The technical zone name is already taken."""


@dataclass(frozen=True)
class ZoneDependencies:
    schedule_points: int
    devices: int
    setpoints: int
    overrides: int
    shadow_decisions: int


def _name_taken(session: Session, name: str, except_zone_id: int | None = None) -> bool:
    query = select(Zone.id).where(Zone.name == name)
    if except_zone_id is not None:
        query = query.where(Zone.id != except_zone_id)
    return session.scalar(query.limit(1)) is not None


def create_zone(
    session: Session,
    principal: Principal,
    *,
    name: str,
    display_name: str,
    operating_mode_id: int,
    sort_order: int,
    temperature_source_device_id: int | None,
    source: str = "web",
) -> Zone:
    """Creates the zone and its audit entry atomically, even under a name collision."""
    if _name_taken(session, name):
        raise ZonennameVergeben
    zone = Zone(
        name=name,
        display_name=display_name,
        operating_mode_id=operating_mode_id,
        sort_order=sort_order,
        temperature_source_device_id=temperature_source_device_id,
    )
    try:
        with session.begin_nested():
            session.add(zone)
            session.flush()
            audit.record(
                session,
                source=source,
                action="create",
                object_type="zone",
                object_id=str(zone.id),
                summary=f"Zone {zone.name} angelegt",
                user_id=principal.user_id,
                token_id=principal.token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ZonennameVergeben from exc
    return zone


def update_zone(
    session: Session,
    zone: Zone,
    principal: Principal,
    *,
    name: str,
    display_name: str,
    operating_mode_id: int,
    sort_order: int,
    temperature_source_device_id: int | None,
    source: str = "web",
) -> None:
    """Changes the zone and its audit entry atomically, even under a name collision."""
    if _name_taken(session, name, zone.id):
        raise ZonennameVergeben
    try:
        with session.begin_nested():
            zone.name = name
            zone.display_name = display_name
            zone.operating_mode_id = operating_mode_id
            zone.sort_order = sort_order
            zone.temperature_source_device_id = temperature_source_device_id
            audit.record(
                session,
                source=source,
                action="update",
                object_type="zone",
                object_id=str(zone.id),
                summary=f"Zone {zone.name} geändert",
                user_id=principal.user_id,
                token_id=principal.token_id,
            )
            session.flush()
    except IntegrityError as exc:
        raise ZonennameVergeben from exc


def zonedependencies(session: Session, zone_id: int) -> ZoneDependencies:
    def count(modell: type[object]) -> int:
        return session.scalar(
            select(func.count()).select_from(modell).where(modell.zone_id == zone_id)  # type: ignore[attr-defined]
        ) or 0

    return ZoneDependencies(
        schedule_points=count(SchedulePoint),
        devices=count(ZoneDevice),
        setpoints=count(ZoneSetpoint),
        overrides=count(ZoneOverride),
        shadow_decisions=count(ShadowDecision),
    )


def delete_zone(
    session: Session, zone: Zone, principal: Principal, *, source: str = "web"
) -> None:
    """Deletes a zone; the audit entry outlives its cascades."""
    zone_id = zone.id
    name = zone.name
    session.delete(zone)
    audit.record(
        session,
        source=source,
        action="delete",
        object_type="zone",
        object_id=str(zone_id),
        summary=f"Zone {name} gelöscht",
        user_id=principal.user_id,
        token_id=principal.token_id,
    )



def set_operating_mode(
    session: Session,
    zone: Zone,
    code: str,
    *,
    akteur_id: int | None,
    source: str = "web",
) -> bool:
    """Sets a zone's operating mode. Returns whether anything actually changed.

    Its own function next to `zone_aendern`, which takes all fields at once: a command
    from the outside -- from Home Assistant, say -- knows only the operating mode and
    would use `zone_aendern` to overwrite everything else with whatever values the
    caller happens to have on hand.
    """
    kind = session.scalar(select(OperatingMode).where(OperatingMode.code == code))
    if kind is None:
        raise UnknownOperatingMode(f"Die Betriebsart '{code}' gibt es nicht.")
    if zone.operating_mode_id == kind.id:
        return False
    vorher = zone.operating_mode.label
    # Set the relationship, not the foreign key: whoever only rewrites
    # `operating_mode_id` leaves an already loaded `zone.operating_mode` unchanged --
    # SQLAlchemy only reloads it after the next commit. But the service reports the
    # new state to Home Assistant right after the command, i.e. still before the
    # commit: the old operating mode arrived there, and it looked like it could not
    # be changed.
    zone.operating_mode = kind
    session.flush()
    audit.record(
        session,
        source=source,
        action="update",
        object_type="zone",
        object_id=str(zone.id),
        summary=f"Betriebsart von '{zone.display_name}' auf {kind.label} gesetzt",
        detail=f"{vorher} → {kind.label}",
        user_id=akteur_id,
    )
    return True
