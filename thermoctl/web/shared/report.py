"""Problem melden: gemeinsamer Schreibweg von Alt-View und /ui-api."""
from fastapi import Request
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import settings as control_settings
from thermoctl.domain.fault_notice import notice_enabled
from thermoctl.domain.principal import Principal
from thermoctl.domain.problem_report import build_report
from thermoctl.integrations import notification


async def submit_report(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    kind: str,
    note: str,
) -> str:
    """Der gemeinsame Schreibweg von HTML-View und `/ui-api` (Grundsatz 6).

    Baut die Meldung, schreibt sie ins Audit und stellt sie zu; liefert den Satz, der dem
    Melder gesagt wird. Wirft `DomainError` bei einer ungültigen Eingabe, bevor etwas
    geschrieben wurde.
    """
    user_record = session.get(User, principal.user_id)
    notice = build_report(session, zone, kind, note, now=utcnow(), user=user_record)
    audit.record(
        session,
        source="web",
        action="report.created",
        object_type="zone",
        object_id=str(zone.id),
        summary=notice.title,
        detail=notice.text,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )

    env_settings = get_settings()
    row = control_settings(session)
    if env_settings.notify_webhook is None or not notice_enabled(notice.kind, row):
        return (
            "Die Meldung wurde aufgenommen. Eine automatische Weiterleitung "
            "ist für diese Anlage nicht eingerichtet."
        )
    await notification.deliver(request.app.state.session_factory, env_settings, notice)
    return "Die Meldung wurde weitergegeben."
