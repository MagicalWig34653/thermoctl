from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.absence import Absence
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import Zone
from thermoctl.domain.schedule import create_override


def start_absence(
    session: Session,
    zones: list[Zone],
    temperature_c: Decimal,
    ends_at: datetime,
    *,
    now: datetime | None = None,
    user_id: int | None = None,
    token_id: int | None = None,
    source: str = "web",
) -> Absence:
    """Startet eine Abwesenheit samt genau einer Übersteuerung je Zone.

    Eine schon laufende Abwesenheit desselben Benutzers wird abgelehnt. So bleibt
    die bestehende Abwesenheit unverändert, falls das Anlegen der neuen später
    scheitert. Der verschachtelte Transaktionsblock macht Klammer,
    Zonenübersteuerungen und Audit-Eintrag atomar: Eine halb abgesenkte Wohnung,
    deren unvollständige Klammer niemand zuverlässig auflösen kann, darf nicht
    entstehen.
    """
    if not zones:
        raise ValueError("Für eine Abwesenheit muss mindestens eine Zone gewählt sein.")
    moment = now if now is not None else utcnow()
    if ends_at <= moment:
        raise ValueError("Das Ende der Abwesenheit muss in der Zukunft liegen.")
    if user_id is not None and running_absence(session, user_id, moment) is not None:
        raise ValueError("Für diesen Benutzer läuft bereits eine Abwesenheit.")

    with session.begin_nested():
        source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
        if source_id is None:
            raise ValueError(f"Unbekannte Quelle {source!r}")
        absence = Absence(
            starts_at=moment, ends_at=ends_at, setback_temperature_c=temperature_c,
            created_by_user_id=user_id, created_by_token_id=token_id, source_id=source_id,
        )
        session.add(absence)
        session.flush()
        for zone in zones:
            override = create_override(
                session, zone, temperature_c, ends_at, now=moment,
                user_id=user_id, token_id=token_id, source=source,
            )
            override.absence_id = absence.id
        audit.record(
            session, source=source, action="create", object_type="absence",
            object_id=str(absence.id), summary="Abwesenheit für eigene Räume angesetzt",
            detail=f"{len(zones)} Zonen bis {ends_at.isoformat()}, {temperature_c} °C",
            user_id=user_id, token_id=token_id,
        )
        session.flush()
    return absence


def end_absence(
    session: Session, absence: Absence, *, now: datetime | None = None
) -> None:
    """Beendet die Abwesenheit und nur ihre noch laufenden Übersteuerungen.

    cancel_override wird bewusst nicht verwendet: Es sucht die jüngste
    Übersteuerung einer Zone, die inzwischen unabhängig von dieser Abwesenheit
    entstanden sein kann. Die Zuordnung über absence_id trifft ausschließlich die
    ursprüngliche Gruppe.
    """
    moment = now if now is not None else utcnow()
    absence.cancelled_at = moment
    overrides = session.scalars(
        select(ZoneOverride).where(
            ZoneOverride.absence_id == absence.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= moment,
            ZoneOverride.ends_at > moment,
        )
    )
    for override in overrides:
        override.cancelled_at = moment
    source = session.scalar(select(ActorSource.code).where(ActorSource.id == absence.source_id))
    if source is None:
        raise ValueError("Die Quelle der Abwesenheit ist nicht mehr vorhanden.")
    audit.record(
        session, source=source, action="update", object_type="absence",
        object_id=str(absence.id),
        summary="Abwesenheit für eigene Räume vorzeitig beendet",
        user_id=absence.created_by_user_id, token_id=absence.created_by_token_id,
    )


def running_absence(session: Session, user_id: int, now: datetime) -> Absence | None:
    """Liefert die derzeit laufende Abwesenheit genau dieses Benutzers."""
    return session.scalars(
        select(Absence)
        .where(
            Absence.created_by_user_id == user_id,
            Absence.cancelled_at.is_(None),
            Absence.starts_at <= now,
            Absence.ends_at > now,
        )
        .order_by(Absence.starts_at.desc(), Absence.id.desc())
    ).first()


def absence_zones(session: Session, absence: Absence) -> list[Zone]:
    """Liefert die Zonen einer Abwesenheit in stabiler Anzeigenreihenfolge."""
    return list(
        session.scalars(
            select(Zone)
            .join(ZoneOverride, ZoneOverride.zone_id == Zone.id)
            .where(ZoneOverride.absence_id == absence.id)
            .order_by(Zone.sort_order, Zone.display_name, Zone.id)
        ).unique()
    )
