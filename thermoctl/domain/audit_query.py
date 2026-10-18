"""Lesen des Audit-Protokolls mit Filtern und Seiten -- eine Abfrage für HTML und JSON.

Keine Rechteprüfung hier (`audit.read` prüft der Adapter), wohl aber die **Raumeinschränkung**:
`zone_ids` ist der Bereich eines auf Räume beschränkten `audit.read` (`None` = anlagenweit,
alles). Der Filter steht in der Abfrage selbst, vor Seite und Obergrenze -- nach dem Laden
gefiltert stimmten Seitenlänge und `has_more` nicht mehr. Welche Ereignisse einem Raum
gehören, steht bei `_scope_clause`. Die Tagesgrenzen sind **lokale**
Kalendertage der Anlage: `occurred_at` steht als naive UTC in der Datenbank, wer „von/bis"
tippt, meint seine Wanduhr. Der Schnitt bei UTC-Mitternacht ließ die ersten ein, zwei
Stunden eines lokalen Tages unter den Vortag fallen.
"""

from collections.abc import Collection
from datetime import date, timedelta

from sqlalchemy import ColumnElement, Select, false, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.time import local_day_start_utc

ENTRIES_PER_PAGE = 50
#: Obergrenze der Freitextsuche (Zeichen) und der Zeilen eines CSV-Exports.
MAX_TEXT_LENGTH = 100
MAX_EXPORT_ROWS = 5000

_Rows = list[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]]

#: Objektarten, deren `object_id` die Zonen-ID ist.
_ZONE_KEYED_TYPES = (
    "zone", "zone_settings", "zone_setpoint", "zone_temperature_source", "schedule",
)


def _scope_clause(session: Session, zone_ids: Collection[int]) -> ColumnElement[bool]:
    """Welche Ereignisse zu den Räumen `zone_ids` gehören.

    Eindeutig einem Raum zuzuordnen sind nur Ereignisse, deren Objekt der Raum selbst oder
    etwas ist, das an genau einem Raum hängt:

    * Objektarten mit der Zonen-ID als `object_id` (`_ZONE_KEYED_TYPES`),
    * `schedule_point` (`object_id` = `<Zonen-ID>:<Punkt-ID>`, bleibt auch nach dem Löschen
      des Punkts zuzuordnen),
    * `zone_device` (Zuordnung Gerät/Zone; über die Zuordnungstabelle -- eine gelöschte
      Zuordnung ist nicht mehr zuzuordnen).

    Alles andere (Benutzer, Gruppen, Tokens, Einstellungen, Modi, Abwesenheit, Urlaub, Bedien-
    geräte, Rücknahmen ...) gehört niemandem im Besonderen und ist **nur anlagenweit**
    sichtbar.
    """
    ids = sorted(zone_ids)
    if not ids:  # pragma: no cover - ein Bereich ohne Räume liefert `permission_scope` nie
        return false()
    assignments = [
        str(assignment_id)
        for assignment_id in session.scalars(
            select(ZoneDevice.id).where(ZoneDevice.zone_id.in_(ids))
        )
    ]
    clauses: list[ColumnElement[bool]] = [
        AuditEvent.object_type.in_(_ZONE_KEYED_TYPES)
        & AuditEvent.object_id.in_([str(i) for i in ids]),
        (AuditEvent.object_type == "schedule_point")
        & or_(*(AuditEvent.object_id.startswith(f"{i}:", autoescape=True) for i in ids)),
    ]
    if assignments:
        clauses.append(
            (AuditEvent.object_type == "zone_device") & AuditEvent.object_id.in_(assignments)
        )
    return or_(*clauses)


def _filtered(
    *,
    from_day: date | None,
    to_day: date | None,
    user: str,
    action_code: str,
    source: str,
    object: str,  # noqa: A002 - Filtername der Oberfläche
    text: str,
    timezone_name: str | None,
    scope: ColumnElement[bool] | None = None,
) -> Select[tuple[AuditEvent, ActorSource, User, ApiToken]]:
    query = (
        select(AuditEvent, ActorSource, User, ApiToken)
        .join(ActorSource, ActorSource.id == AuditEvent.source_id)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(ApiToken, ApiToken.id == AuditEvent.actor_token_id)
    )
    if scope is not None:
        query = query.where(scope)
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
    if text:
        # Teilstring ohne Platzhalter: `autoescape` maskiert %, _ und das Fluchtzeichen, die
        # Eingabe ist also nie ein LIKE-Muster. Beide Seiten klein, damit SQLite und MariaDB
        # (je nach Kollation) gleich antworten.
        needle = text.lower()
        query = query.where(
            or_(
                func.lower(AuditEvent.summary).contains(needle, autoescape=True),
                func.lower(AuditEvent.detail).contains(needle, autoescape=True),
                func.lower(AuditEvent.object_type).contains(needle, autoescape=True),
                func.lower(AuditEvent.object_id).contains(needle, autoescape=True),
            )
        )
    return query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())


def list_audit(
    session: Session,
    *,
    from_day: date | None = None,
    to_day: date | None = None,
    user: str = "",
    action_code: str = "",
    source: str = "",
    object: str = "",  # noqa: A002 - Filtername der Oberfläche
    text: str = "",
    page: int = 1,
    timezone_name: str | None = None,
    zone_ids: Collection[int] | None = None,
) -> tuple[_Rows, bool]:
    """Eine Seite Einträge (neueste zuerst) und ob es eine weitere gibt."""
    query = _filtered(
        from_day=from_day,
        to_day=to_day,
        user=user,
        action_code=action_code,
        source=source,
        object=object,
        text=text,
        timezone_name=timezone_name,
        scope=None if zone_ids is None else _scope_clause(session, zone_ids),
    )
    rows = session.execute(
        query.offset((page - 1) * ENTRIES_PER_PAGE).limit(ENTRIES_PER_PAGE + 1)
    ).all()
    return list(rows[:ENTRIES_PER_PAGE]), len(rows) > ENTRIES_PER_PAGE


def export_audit(
    session: Session,
    *,
    from_day: date | None = None,
    to_day: date | None = None,
    user: str = "",
    action_code: str = "",
    source: str = "",
    object: str = "",  # noqa: A002 - Filtername der Oberfläche
    text: str = "",
    timezone_name: str | None = None,
    zone_ids: Collection[int] | None = None,
) -> tuple[_Rows, bool]:
    """Alle passenden Einträge, höchstens `MAX_EXPORT_ROWS`; zweiter Wert: gekürzt?"""
    query = _filtered(
        from_day=from_day,
        to_day=to_day,
        user=user,
        action_code=action_code,
        source=source,
        object=object,
        text=text,
        timezone_name=timezone_name,
        scope=None if zone_ids is None else _scope_clause(session, zone_ids),
    )
    rows = session.execute(query.limit(MAX_EXPORT_ROWS + 1)).all()
    return list(rows[:MAX_EXPORT_ROWS]), len(rows) > MAX_EXPORT_ROWS


def scoped_actions(session: Session, zone_ids: Collection[int] | None) -> list[str]:
    """Die vorkommenden Aktionscodes (Auswahl des Filters), nur aus den sichtbaren Ereignissen.

    Ungefiltert verriete die Liste Aktionen unsichtbarer Ereignisse (etwa `user.created`).
    """
    query = select(AuditEvent.action).distinct().order_by(AuditEvent.action)
    if zone_ids is not None:
        query = query.where(_scope_clause(session, zone_ids))
    return list(session.scalars(query))


def scoped_source_codes(session: Session, zone_ids: Collection[int] | None) -> set[str] | None:
    """Quellen, die in den sichtbaren Ereignissen vorkommen; `None` = alle (anlagenweit)."""
    if zone_ids is None:
        return None
    return set(
        session.scalars(
            select(ActorSource.code)
            .join(AuditEvent, AuditEvent.source_id == ActorSource.id)
            .where(_scope_clause(session, zone_ids))
            .distinct()
        )
    )
