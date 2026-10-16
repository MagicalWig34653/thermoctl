"""Lesen des Audit-Protokolls mit Filtern und Seiten -- eine Abfrage für HTML und JSON.

Keine Rechteprüfung hier (`audit.read` prüft der Adapter). Die Tagesgrenzen sind **lokale**
Kalendertage der Anlage: `occurred_at` steht als naive UTC in der Datenbank, wer „von/bis"
tippt, meint seine Wanduhr. Der Schnitt bei UTC-Mitternacht ließ die ersten ein, zwei
Stunden eines lokalen Tages unter den Vortag fallen.
"""

from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.time import local_day_start_utc

ENTRIES_PER_PAGE = 50


def list_audit(
    session: Session,
    *,
    from_day: date | None = None,
    to_day: date | None = None,
    user: str = "",
    action_code: str = "",
    source: str = "",
    object: str = "",  # noqa: A002 - Filtername der Oberfläche
    page: int = 1,
    timezone_name: str | None = None,
) -> tuple[list[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]], bool]:
    """Eine Seite Einträge (neueste zuerst) und ob es eine weitere gibt."""
    query = (
        select(AuditEvent, ActorSource, User, ApiToken)
        .join(ActorSource, ActorSource.id == AuditEvent.source_id)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(ApiToken, ApiToken.id == AuditEvent.actor_token_id)
    )
    if from_day is not None:
        query = query.where(AuditEvent.occurred_at >= local_day_start_utc(from_day, timezone_name))
    if to_day is not None:
        # Obere Grenze ausschließlich am Folgetag: schließt den ganzen "bis"-Tag ein,
        # ohne datenbankspezifische Datumsfunktionen.
        next_day = local_day_start_utc(to_day + timedelta(days=1), timezone_name)
        query = query.where(AuditEvent.occurred_at < next_day)
    if user:
        query = query.where(or_(User.username == user, ApiToken.name == user))
    if action_code:
        query = query.where(AuditEvent.action == action_code)
    if source:
        query = query.where(ActorSource.code == source)
    if object:
        object_type, separator, object_id = object.partition(":")
        query = query.where(AuditEvent.object_type == object_type)
        if separator:
            query = query.where(AuditEvent.object_id == object_id)
    rows = session.execute(
        query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .offset((page - 1) * ENTRIES_PER_PAGE)
        .limit(ENTRIES_PER_PAGE + 1)
    ).all()
    return list(rows[:ENTRIES_PER_PAGE]), len(rows) > ENTRIES_PER_PAGE
