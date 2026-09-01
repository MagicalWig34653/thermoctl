from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent


def record(
    session: Session, *, source: str, action: str, object_type: str,
    summary: str, object_id: str | None = None, user_id: int | None = None,
    token_id: int | None = None, detail: str | None = None,
) -> AuditEvent:
    """Writes an audit entry into the same transaction as the change.

    This way there is no entry for a change that was rolled back — and no change
    without an entry.
    """
    source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
    if source_id is None:
        # Otherwise a NULL would go into a NOT-NULL column and the caller would get an
        # IntegrityError about `audit_event.source_id` -- a message that mentions the
        # actual bug (a typo in `source`) nowhere. Since the source is passed through
        # from the adapter, this is a reachable case.
        raise ValueError(f"Unbekannte Audit-Quelle {source!r}")
    event = AuditEvent(
        occurred_at=utcnow(), source_id=source_id, action=action,
        object_type=object_type, object_id=object_id, summary=summary,
        actor_user_id=user_id, actor_token_id=token_id, detail=detail,
    )
    session.add(event)
    return event
