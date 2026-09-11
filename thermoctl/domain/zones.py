from dataclasses import dataclass
from decimal import Decimal

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


class ZoneNameTaken(Exception):
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
    solar_gain_factor: Decimal = Decimal(0),
    source: str = "web",
) -> Zone:
    """Creates the zone and its audit entry atomically, even under a name collision."""
    if _name_taken(session, name):
        raise ZoneNameTaken
    zone = Zone(
        name=name,
        display_name=display_name,
        operating_mode_id=operating_mode_id,
        sort_order=sort_order,
        temperature_source_device_id=temperature_source_device_id,
        solar_gain_factor=solar_gain_factor,
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
        raise ZoneNameTaken from exc
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
    solar_gain_factor: Decimal | None = None,
    source: str = "web",
) -> None:
    """Changes the zone and its audit entry atomically, even under a name collision."""
    if _name_taken(session, name, zone.id):
        raise ZoneNameTaken
    try:
        with session.begin_nested():
            zone.name = name
            zone.display_name = display_name
            zone.operating_mode_id = operating_mode_id
            zone.sort_order = sort_order
            zone.temperature_source_device_id = temperature_source_device_id
            # `None` means "leave as is": the web form edits this field on the
            # parameters page, not on the zone form, and must not reset it on every
            # save of the zone's name.
            if solar_gain_factor is not None:
                zone.solar_gain_factor = solar_gain_factor
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
        raise ZoneNameTaken from exc


def zone_dependencies(session: Session, zone_id: int) -> ZoneDependencies:
    def count(model: type[object]) -> int:
        return session.scalar(
            select(func.count()).select_from(model).where(model.zone_id == zone_id)  # type: ignore[attr-defined]
        ) or 0

    return ZoneDependencies(
        schedule_points=count(SchedulePoint),
        devices=count(ZoneDevice),
        setpoints=count(ZoneSetpoint),
        overrides=count(ZoneOverride),
        shadow_decisions=count(ShadowDecision),
    )


def latest_decisions_by_zone(
    session: Session, zone_ids: list[int]
) -> dict[int, ShadowDecision]:
    """The single most recent `ShadowDecision` per zone -- without reading the
    zone's whole decision history to find it.

    Shared domain logic (principle 6), not a page-local helper: both the plant
    overview (`web/start_views.py`) and the operations page (`web/control_views.py`)
    need exactly this, and having each own its own copy is how the operations page
    ended up with the slow, un-`LIMIT`ed version after the overview was already
    fixed. It started life in `web/start_views.py` alone -- moving it here means
    neither web module has to import the other just to share one query.

    `shadow_decision_retention_days` defaults to 365 (`db/models/operations.py`),
    and the control loop writes one row per zone every `shadow_interval_seconds`
    (60 by default) -- a plant running for months can hold hundreds of thousands
    of rows per zone. The query this replaced (`select(ShadowDecision).where(zone_id
    .in_(...)).order_by(decided_at.desc(), id.desc())`) had no `LIMIT`: it fetched
    *every* one of those rows for every visible zone over the network merely to
    keep the first one per zone in Python and discard the rest -- on the real,
    MariaDB-backed installation this dwarfed every other cost on both pages.

    Instead: find each zone's newest `decided_at` with a `GROUP BY zone_id`, which
    the composite index `ix_shadow_decision_zone_decided_id` on
    `(zone_id, decided_at, id)` answers by seeking straight to the last entry of
    each zone's index range (a "loose index scan") -- no row-by-row scan of the
    history. A second, equally cheap grouped step resolves the `id` tiebreak
    (mirrors `ZoneOverride`'s: MariaDB's `DATETIME` only has second precision, so
    two decisions in the same zone within the same second must not leave the
    database an arbitrary choice) before the final query fetches exactly one row
    per zone by its `id`.
    """
    if not zone_ids:
        return {}
    latest_per_zone = (
        select(
            ShadowDecision.zone_id.label("zone_id"),
            func.max(ShadowDecision.decided_at).label("decided_at"),
        )
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .group_by(ShadowDecision.zone_id)
        .subquery()
    )
    latest_ids = (
        select(func.max(ShadowDecision.id).label("id"))
        .join(
            latest_per_zone,
            (ShadowDecision.zone_id == latest_per_zone.c.zone_id)
            & (ShadowDecision.decided_at == latest_per_zone.c.decided_at),
        )
        .group_by(ShadowDecision.zone_id)
        .subquery()
    )
    rows = session.scalars(
        select(ShadowDecision).where(ShadowDecision.id.in_(select(latest_ids.c.id)))
    )
    return {row.zone_id: row for row in rows}


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
    actor_id: int | None,
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
    before = zone.operating_mode.label
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
        detail=f"{before} → {kind.label}",
        user_id=actor_id,
    )
    return True
