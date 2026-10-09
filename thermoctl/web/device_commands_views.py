from datetime import date, datetime, time, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.models.lookup import ActorSource, CommandOutcome
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import DeviceCommand
from thermoctl.domain.authz import require
from thermoctl.domain.device_commands import (
    MAX_LIMIT,
    SETBACK_OUTCOME_PREFIX,
    SETBACK_SOURCE,
    CommandLogEntry,
    list_commands,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.solar_setback_log import KIND_BEGIN, KIND_CHANGE, KIND_END
from thermoctl.web import is_partial_swap, templates
from thermoctl.web.guards import admin_ui_only

# `include_in_schema=False`: see the identical remark in `audit_views.py` -- these
# routes deliver HTML for humans, not the REST contract.
# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)

ENTRIES_PER_PAGE = 50

SETBACK_OUTCOME_LABELS = {
    SETBACK_OUTCOME_PREFIX + KIND_BEGIN: "Absenkung beginnt",
    SETBACK_OUTCOME_PREFIX + KIND_CHANGE: "Absenkung geändert",
    SETBACK_OUTCOME_PREFIX + KIND_END: "Absenkung beendet",
}


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

    from_at = datetime.combine(from_day, time.min) if from_day is not None else None
    # An exclusive bound on the following day includes the whole "to" day and
    # avoids database-specific date functions -- as in `audit_views.py`.
    to_at = (
        datetime.combine(to_day, time.min) + timedelta(days=1) - timedelta(microseconds=1)
        if to_day is not None
        else None
    )

    entries: list[CommandLogEntry] = []
    has_more = False
    if not errors:
        fetched = list_commands(
            session,
            zone_name=zone or None,
            from_at=from_at,
            to_at=to_at,
            outcome=outcome or None,
            # `+1` over the page size to detect whether another page follows --
            # same trick the previous raw query used, now through the shared
            # domain function (Grundsatz 6; see its module docstring on why it
            # also carries `ActuatorDecision` rows since Auftrag 8b).
            limit=min(ENTRIES_PER_PAGE + 1, MAX_LIMIT),
            offset=(page_number - 1) * ENTRIES_PER_PAGE,
        )
        has_more = len(fetched) > ENTRIES_PER_PAGE
        entries = fetched[:ENTRIES_PER_PAGE]

    # Every zone name ever recorded, not just the zones that still exist -- the whole
    # point of the snapshot is that a deleted zone stays filterable too.
    zone_names = session.scalars(
        select(DeviceCommand.zone_name).distinct().order_by(DeviceCommand.zone_name)
    ).all()
    outcomes = session.execute(
        select(CommandOutcome.code, CommandOutcome.label).order_by(CommandOutcome.label)
    ).all()
    outcome_labels = {code: label for code, label in outcomes}
    # Die Ergebnisse der Absenkungseinträge sind keine `command_outcome`-Zeilen (wie die
    # `sensorausfall_*`-Gründe der Entscheidungen); ihre Anzeigetexte stehen hier.
    outcome_labels.update(SETBACK_OUTCOME_LABELS)
    # `list_commands` returns the plain code for both `entry.source` (a real
    # `actor_source` code) and -- for an `"entscheidung"` row -- `entry.outcome`
    # (a `sensorausfall_*` reason code with no `command_outcome` row at all).
    # The template shows a human label where one exists and falls back to the
    # raw code otherwise, via these two small maps rather than re-querying per
    # row.
    source_labels = {
        code: label for code, label in session.execute(select(ActorSource.code, ActorSource.label))
    }
    source_labels.setdefault("regelung", "Regelung (Notbetrieb)")
    source_labels.setdefault(SETBACK_SOURCE, "Regelung (Sonnenabsenkung)")
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
            "outcome_labels": outcome_labels,
            "source_labels": source_labels,
            "filter": filter_values,
            "errors": errors,
            "page": page_number,
            "has_more": has_more,
            "base_parameters": urlencode(filter_values),
            "is_htmx": is_partial_swap(request),
            "timezone": settings.timezone if settings is not None else None,
        },
    )
