"""Anlagenweites Änderungsprotokoll und bestätigte Rücknahme."""

import hmac
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.change_log import ChangeLog
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import require
from thermoctl.domain.change_log import (
    CHANGE_TYPES,
    MAX_LIMIT,
    Actor,
    ChangeAlreadyEffective,
    ChangeAlreadyReverted,
    ChangeConflict,
    ChangeNotFound,
    ChangeNotPossible,
    ChangeNotRevertible,
    ChangeView,
    get_change,
    list_changes,
    revert_change,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import ScheduleError
from thermoctl.domain.sensor_failure_policy import PolicyError
from thermoctl.domain.zone_settings import ParameterOutOfRange
from thermoctl.web import templates
from thermoctl.web.guards import admin_ui_only

router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)], include_in_schema=False
)
ENTRIES_PER_PAGE = 50


def _date(value: str, field: str, errors: dict[str, str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[field] = "Bitte ein gültiges Datum eingeben."
        return None


def _confirmation_token(request: Request, change_id: int) -> str:
    secret = get_settings().secret_key.get_secret_value().encode()
    cookie = request.cookies.get(COOKIE_NAME, "")
    return hmac.new(secret, f"force:{cookie}:{change_id}".encode(), sha256).hexdigest()


def _detail(
    request: Request,
    session: Session,
    entry: ChangeView,
    *,
    message: str | None = None,
    conflict: ChangeConflict | None = None,
) -> Response:
    settings = session.get(Setting, 1)
    return templates.TemplateResponse(
        request,
        "change_revert.html",
        {
            "entry": entry,
            "message": message,
            "conflict": conflict,
            "force_token": _confirmation_token(request, entry.id) if conflict else None,
            "timezone": settings.timezone if settings is not None else None,
        },
    )


@router.get("/changes")
async def change_list(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    zone: str = "",
    change_type: str = "",
    source: str = "",
    hide_reverted: str = "",
    page: str = "1",
) -> Response:
    require(principal, "audit.read")
    errors: dict[str, str] = {}
    from_day = _date(from_date, "from_date", errors)
    to_day = _date(to_date, "to_date", errors)
    if from_day and to_day and to_day < from_day:
        errors["to_date"] = "Das Bis-Datum darf nicht vor dem Von-Datum liegen."
    try:
        page_number = max(1, int(page))
    except ValueError:
        page_number = 1
        errors["page"] = "Die Seitennummer muss eine ganze Zahl sein."
    zone_id = None
    if zone:
        try:
            zone_id = int(zone)
            if zone_id < 1:
                raise ValueError
        except ValueError:
            errors["zone"] = "Bitte eine gültige Zone wählen."
    if change_type and change_type not in CHANGE_TYPES:
        errors["change_type"] = "Bitte eine gültige Änderungsart wählen."
    sources = list(
        session.execute(select(ActorSource.code, ActorSource.label).order_by(ActorSource.label))
    )
    if source and source not in {code for code, _ in sources}:
        errors["source"] = "Bitte eine gültige Quelle wählen."
    fetched: list[ChangeView] = []
    if not errors:
        fetched = list_changes(
            session,
            zone_id=zone_id,
            change_type=change_type or None,
            source=source or None,
            from_at=datetime.combine(from_day, time.min) if from_day else None,
            to_at=datetime.combine(to_day + timedelta(days=1), time.min) if to_day else None,
            include_reverted=not bool(hide_reverted),
            limit=min(ENTRIES_PER_PAGE + 1, MAX_LIMIT),
            offset=(page_number - 1) * ENTRIES_PER_PAGE,
        )
    zones = session.execute(select(ChangeLog.zone_id, ChangeLog.zone_name).distinct()).all()
    current_zones = {
        zone_id: name for zone_id, name in session.execute(select(Zone.id, Zone.display_name))
    }
    zone_choices = sorted(
        ((id_, current_zones.get(id_, name)) for id_, name in zones if id_ is not None),
        key=lambda row: (row[1] or "").casefold(),
    )
    filters = dict(
        from_date=from_date,
        to_date=to_date,
        zone=zone,
        change_type=change_type,
        source=source,
        hide_reverted=hide_reverted,
    )
    settings = session.get(Setting, 1)
    return templates.TemplateResponse(
        request,
        "changes.html",
        {
            "entries": fetched[:ENTRIES_PER_PAGE],
            "has_more": len(fetched) > ENTRIES_PER_PAGE,
            "page": page_number,
            "filter": filters,
            "errors": errors,
            "zone_choices": zone_choices,
            "change_types": CHANGE_TYPES,
            "sources": sources,
            "source_labels": {code: label for code, label in sources},
            "base_parameters": urlencode(filters),
            "timezone": settings.timezone if settings is not None else None,
        },
    )


@router.get("/changes/{change_id}/revert")
async def confirm_revert(
    request: Request,
    change_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "audit.read")
    try:
        entry = get_change(session, change_id)
    except ChangeNotFound:
        return templates.TemplateResponse(request, "change_not_found.html", {}, status_code=404)
    return _detail(request, session, entry)


@router.post("/changes/{change_id}/revert")
async def submit_revert(
    request: Request,
    change_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    force = form.get("force") == "1"
    if force and not hmac.compare_digest(
        str(form.get("force_token", "")), _confirmation_token(request, change_id)
    ):
        return templates.TemplateResponse(request, "change_force_invalid.html", {}, status_code=403)
    try:
        revert_change(
            session, principal, change_id, force=force, actor=Actor(principal.user_id, None, "web")
        )
    except ChangeNotFound:
        return templates.TemplateResponse(request, "change_not_found.html", {}, status_code=404)
    except ChangeConflict as exc:
        return _detail(request, session, get_change(session, change_id), conflict=exc)
    except (
        ChangeAlreadyEffective,
        ChangeAlreadyReverted,
        ChangeNotRevertible,
        ChangeNotPossible,
        ParameterOutOfRange,
        ScheduleError,
        PolicyError,
    ) as exc:
        # ChangeAlreadyEffective marks the row in this session; returning normally
        # lets get_session commit that marker.
        return _detail(request, session, get_change(session, change_id), message=str(exc))
    return RedirectResponse(f"{request.scope.get('root_path', '')}/changes", status_code=303)
