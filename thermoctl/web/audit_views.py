from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import is_partial_swap, templates

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

ENTRIES_PER_PAGE = 50


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

    query = (
        select(AuditEvent, ActorSource, User, ApiToken)
        .join(ActorSource, ActorSource.id == AuditEvent.source_id)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(ApiToken, ApiToken.id == AuditEvent.actor_token_id)
    )
    if not errors:
        if from_day is not None:
            query = query.where(
                AuditEvent.occurred_at >= datetime.combine(from_day, time.min)
            )
        if to_day is not None:
            # An exclusive bound on the following day includes the whole "to" day
            # and avoids database-specific date functions.
            next_day = datetime.combine(to_day, time.min) + timedelta(days=1)
            query = query.where(AuditEvent.occurred_at < next_day)
        if user:
            query = query.where(
                or_(User.username == user, ApiToken.name == user)
            )
        if action_code:
            query = query.where(AuditEvent.action == action_code)
        if source:
            query = query.where(ActorSource.code == source)
        if object:
            object_type, separator, objekt_id = object.partition(":")
            query = query.where(AuditEvent.object_type == object_type)
            if separator:
                query = query.where(AuditEvent.object_id == objekt_id)

    entries: Sequence[Row[tuple[AuditEvent, ActorSource, User, ApiToken]]] = ()
    has_more = False
    if not errors:
        rows = session.execute(
            query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .offset((page_number - 1) * ENTRIES_PER_PAGE)
            .limit(ENTRIES_PER_PAGE + 1)
        ).all()
        has_more = len(rows) > ENTRIES_PER_PAGE
        entries = rows[:ENTRIES_PER_PAGE]

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
            "is_htmx": is_partial_swap(request),
        },
    )
