from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision
from thermoctl.domain.principal import Principal


class Betriebsartunbekannt(Exception):
    """Die verlangte Betriebsart gibt es nicht."""


class ZonennameVergeben(Exception):
    """Der technische Zonenname ist bereits vergeben."""


@dataclass(frozen=True)
class Zonenabhaengigkeiten:
    schaltpunkte: int
    geraete: int
    sollwerte: int
    uebersteuerungen: int
    schattenentscheidungen: int


def _name_vergeben(session: Session, name: str, ausser_zone_id: int | None = None) -> bool:
    anfrage = select(Zone.id).where(Zone.name == name)
    if ausser_zone_id is not None:
        anfrage = anfrage.where(Zone.id != ausser_zone_id)
    return session.scalar(anfrage.limit(1)) is not None


def zone_anlegen(
    session: Session,
    principal: Principal,
    *,
    name: str,
    display_name: str,
    operating_mode_id: int,
    sort_order: int,
    temperature_source_device_id: int | None,
    quelle: str = "web",
) -> Zone:
    """Legt Zone und Audit-Eintrag atomar an, auch bei konkurrierendem Namen."""
    if _name_vergeben(session, name):
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
                source=quelle,
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


def zone_aendern(
    session: Session,
    zone: Zone,
    principal: Principal,
    *,
    name: str,
    display_name: str,
    operating_mode_id: int,
    sort_order: int,
    temperature_source_device_id: int | None,
    quelle: str = "web",
) -> None:
    """Aendert Zone und Audit-Eintrag atomar, auch bei konkurrierendem Namen."""
    if _name_vergeben(session, name, zone.id):
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
                source=quelle,
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


def zonenabhaengigkeiten(session: Session, zone_id: int) -> Zonenabhaengigkeiten:
    def anzahl(modell: type[object]) -> int:
        return session.scalar(
            select(func.count()).select_from(modell).where(modell.zone_id == zone_id)  # type: ignore[attr-defined]
        ) or 0

    return Zonenabhaengigkeiten(
        schaltpunkte=anzahl(SchedulePoint),
        geraete=anzahl(ZoneDevice),
        sollwerte=anzahl(ZoneSetpoint),
        uebersteuerungen=anzahl(ZoneOverride),
        schattenentscheidungen=anzahl(ShadowDecision),
    )


def zone_loeschen(
    session: Session, zone: Zone, principal: Principal, *, quelle: str = "web"
) -> None:
    """Loescht eine Zone; der Audit-Eintrag ueberdauert ihre Kaskaden."""
    zone_id = zone.id
    name = zone.name
    session.delete(zone)
    audit.record(
        session,
        source=quelle,
        action="delete",
        object_type="zone",
        object_id=str(zone_id),
        summary=f"Zone {name} gelöscht",
        user_id=principal.user_id,
        token_id=principal.token_id,
    )



def betriebsart_setzen(
    session: Session,
    zone: Zone,
    code: str,
    *,
    akteur_id: int | None,
    quelle: str = "web",
) -> bool:
    """Setzt die Betriebsart einer Zone. Gibt zurueck, ob sich etwas geaendert hat.

    Eigene Funktion neben `zone_aendern`, das alle Felder auf einmal nimmt: Ein Befehl von
    aussen -- aus Home Assistant etwa -- kennt nur die Betriebsart und wuerde mit
    `zone_aendern` alles andere mit den Werten ueberschreiben, die der Aufrufer gerade
    zufaellig zur Hand hat.
    """
    art = session.scalar(select(OperatingMode).where(OperatingMode.code == code))
    if art is None:
        raise Betriebsartunbekannt(f"Die Betriebsart '{code}' gibt es nicht.")
    if zone.operating_mode_id == art.id:
        return False
    vorher = zone.operating_mode.label
    zone.operating_mode_id = art.id
    session.flush()
    audit.record(
        session,
        source=quelle,
        action="update",
        object_type="zone",
        object_id=str(zone.id),
        summary=f"Betriebsart von '{zone.display_name}' auf {art.label} gesetzt",
        detail=f"{vorher} → {art.label}",
        user_id=akteur_id,
    )
    return True
