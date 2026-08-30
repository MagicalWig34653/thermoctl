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
    """Die verlangte Betriebsart gibt es nicht."""


class ZonennameVergeben(Exception):
    """Der technische Zonenname ist bereits vergeben."""


@dataclass(frozen=True)
class ZoneDependencies:
    schedule_points: int
    devices: int
    setpoints: int
    overrides: int
    shadow_decisions: int


def _name_taken(session: Session, name: str, except_zone_id: int | None = None) -> bool:
    anfrage = select(Zone.id).where(Zone.name == name)
    if except_zone_id is not None:
        anfrage = anfrage.where(Zone.id != except_zone_id)
    return session.scalar(anfrage.limit(1)) is not None


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
    """Legt Zone und Audit-Eintrag atomar an, auch bei konkurrierendem Namen."""
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
    """Aendert Zone und Audit-Eintrag atomar, auch bei konkurrierendem Namen."""
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
    """Loescht eine Zone; der Audit-Eintrag ueberdauert ihre Kaskaden."""
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
    """Setzt die Betriebsart einer Zone. Gibt zurueck, ob sich etwas geaendert hat.

    Eigene Funktion neben `zone_aendern`, das alle Felder auf einmal nimmt: Ein Befehl von
    aussen -- aus Home Assistant etwa -- kennt nur die Betriebsart und wuerde mit
    `zone_aendern` alles andere mit den Werten ueberschreiben, die der Aufrufer gerade
    zufaellig zur Hand hat.
    """
    kind = session.scalar(select(OperatingMode).where(OperatingMode.code == code))
    if kind is None:
        raise UnknownOperatingMode(f"Die Betriebsart '{code}' gibt es nicht.")
    if zone.operating_mode_id == kind.id:
        return False
    vorher = zone.operating_mode.label
    # Die Beziehung setzen, nicht den Fremdschluessel: Wer nur `operating_mode_id`
    # umschreibt, laesst ein bereits geladenes `zone.operating_mode` unveraendert
    # stehen -- SQLAlchemy laedt es erst nach dem naechsten Commit neu. Der Dienst
    # meldet den neuen Zustand aber sofort nach dem Befehl an Home Assistant, also
    # noch vor dem Commit: Dort kam die alte Betriebsart an, und es sah aus, als
    # liesse sie sich nicht umstellen.
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
