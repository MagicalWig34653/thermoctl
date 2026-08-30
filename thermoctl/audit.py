from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent


def record(
    session: Session, *, source: str, action: str, object_type: str,
    summary: str, object_id: str | None = None, user_id: int | None = None,
    token_id: int | None = None, detail: str | None = None,
) -> None:
    """Schreibt einen Audit-Eintrag in dieselbe Transaktion wie die Aenderung.

    Damit gibt es keinen Eintrag zu einer Aenderung, die zurueckgerollt wurde — und keine
    Aenderung ohne Eintrag.
    """
    source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
    if source_id is None:
        # Sonst ginge eine NULL in eine NOT-NULL-Spalte und der Aufrufer bekaeme einen
        # IntegrityError ueber `audit_event.source_id` -- eine Meldung, die den
        # eigentlichen Fehler (ein Tippfehler in `source`) nirgends nennt. Seit die
        # Quelle vom Adapter durchgereicht wird, ist das ein erreichbarer Fall.
        raise ValueError(f"Unbekannte Audit-Quelle {source!r}")
    session.add(
        AuditEvent(
            occurred_at=utcnow(), source_id=source_id, action=action,
            object_type=object_type, object_id=object_id, summary=summary,
            actor_user_id=user_id, actor_token_id=token_id, detail=detail,
        )
    )
