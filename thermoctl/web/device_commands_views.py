from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.lookup import ActorSource, CommandOutcome
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import DeviceCommand
from thermoctl.domain.authz import require
from thermoctl.domain.principal import Principal
from thermoctl.web import is_partial_swap, templates

# `include_in_schema=False`: see the identical remark in `audit_views.py` -- these
# routes deliver HTML for humans, not the REST contract.
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


@router.get("/device-commands")
async def device_command_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    zone: str = "",
    outcome: str = "",
    page: str = "1",
) -> Response:
    """The record of every command sent -- or withheld -- towards an actuator.

    Uses the same permission as the audit log (`audit.read`): both are protocol
    views over the whole plant, not over a single zone, and `audit.read` is
    already the permission for "may see what happened here across zones".
    """
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
        select(DeviceCommand, ActorSource, CommandOutcome)
        .join(ActorSource, ActorSource.id == DeviceCommand.source_id)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
    )
    if not errors:
        if from_day is not None:
            query = query.where(
                DeviceCommand.sent_at >= datetime.combine(from_day, time.min)
            )
        if to_day is not None:
            # An exclusive bound on the following day includes the whole "to" day
            # and avoids database-specific date functions -- as in `audit_views.py`.
            next_day = datetime.combine(to_day, time.min) + timedelta(days=1)
            query = query.where(DeviceCommand.sent_at < next_day)
        if zone:
            # Matched against the name snapshot, not a join to `zone`: a deleted
            # zone has no row to join to anymore, and the snapshot is exactly what
            # lets this filter still find its entries.
            query = query.where(DeviceCommand.zone_name == zone)
        if outcome:
            query = query.where(CommandOutcome.code == outcome)

    entries: Sequence[Row[tuple[DeviceCommand, ActorSource, CommandOutcome]]] = ()
    has_more = False
    if not errors:
        rows = session.execute(
            query.order_by(DeviceCommand.sent_at.desc(), DeviceCommand.id.desc())
            .offset((page_number - 1) * ENTRIES_PER_PAGE)
            .limit(ENTRIES_PER_PAGE + 1)
        ).all()
        has_more = len(rows) > ENTRIES_PER_PAGE
        entries = rows[:ENTRIES_PER_PAGE]

    # Every zone name ever recorded, not just the zones that still exist -- the whole
    # point of the snapshot is that a deleted zone stays filterable too.
    zone_names = session.scalars(
        select(DeviceCommand.zone_name).distinct().order_by(DeviceCommand.zone_name)
    ).all()
    outcomes = session.execute(
        select(CommandOutcome.code, CommandOutcome.label).order_by(CommandOutcome.label)
    ).all()
    filter_values = {
        "from_date": from_date,
        "to_date": to_date,
        "zone": zone,
        "outcome": outcome,
    }
    settings = session.get(Setting, 1)
    return templates.TemplateResponse(
        request,
        "device_commands.html",
        {
            "entries": entries,
            "zone_names": zone_names,
            "outcomes": outcomes,
            "filter": filter_values,
            "errors": errors,
            "page": page_number,
            "has_more": has_more,
            "base_parameters": urlencode(filter_values),
            "is_htmx": is_partial_swap(request),
            "timezone": settings.timezone if settings is not None else None,
        },
    )
