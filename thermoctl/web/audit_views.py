from collections.abc import Sequence
from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.domain.audit_query import list_audit
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import is_partial_swap, templates
from thermoctl.web.guards import admin_ui_only

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


def _datum(value: str, field: str, errors: dict[str, str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[field] = "Bitte ein gültiges Datum eingeben."
        return None


@router.get("/audit")
async def audit_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    user: str = "",
    action_code: str = "",
    object: str = "",  # noqa: A002 - query parameter, not the builtin function
    source: str = "",
    page: str = "1",
) -> Response:
    require(principal, "audit.read")

    errors: dict[str, str] = {}
    from_day = _datum(from_date, "from_date", errors)
    to_day = _datum(to_date, "to_date", errors)
    if from_day is not None and to_day is not None and to_day < from_day:
        errors["to_date"] = "Das Bis-Datum darf nicht vor dem Von-Datum liegen."
    try:
        page_number = max(1, int(page))
    except ValueError:
        page_number = 1
        errors["page"] = "Die Seitennummer muss eine ganze Zahl sein."

    # `occurred_at` is stored as naive UTC, but "from" and "to" are dates typed by a
    # person looking at their own wall clock. Cutting them at UTC midnight -- as this
    # used to -- reads a local day as starting one or two hours earlier than it does
    # in `Europe/Berlin`, so the first entries of a local day silently fell under the
    # previous day's filter, or vanished from both when only one side named that day.
    # A missing `setting` row (incomplete setup) falls back to UTC, the same default
    # `local_day_start_utc` uses for `timezone_name=None`.
    timezone_name = getattr(session.get(Setting, 1), "timezone", None)

    entries: Sequence[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]] = ()
    has_more = False
    if not errors:
        entries, has_more = list_audit(
            session,
            from_day=from_day,
            to_day=to_day,
            user=user,
            action_code=action_code,
            source=source,
            object=object,
            page=page_number,
            timezone_name=timezone_name,
        )

    sources = session.execute(
        select(ActorSource.code, ActorSource.label).order_by(ActorSource.label)
    ).all()
    actions = session.scalars(
        select(AuditEvent.action).distinct().order_by(AuditEvent.action)
    ).all()
    filter_values = {
        "from_date": from_date,
        "to_date": to_date,
        "user": user,
        "action_code": action_code,
        "object": object,
        "source": source,
    }
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "entries": entries,
            "sources": sources,
            "actions": actions,
            "filter": filter_values,
            "errors": errors,
            "page": page_number,
            "has_more": has_more,
            "base_parameters": urlencode(filter_values),
            "timezone": timezone_name,
            "is_htmx": is_partial_swap(request),
        },
    )
