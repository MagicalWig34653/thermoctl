"""The plant-wide vacation: an absence away from home, not a per-zone concern.

Reachable via its own navigation item, not tucked under `/settings` or `/control`:
whoever asks "what does the plant do while I'm away" should not have to already
know it lives among the control defaults. Read access is `zone.read` -- the same
bar as `/control` -- because the running-or-planned state has to be visible to
anyone who can see the plant at all (the project owner's own wording: "muss das
sichtbar sein, ohne dass man danach sucht"); only setting or cancelling one requires
`vacation.manage`, its own permission and not `setting.manage` -- see the reasoning
in `db/models/lookup.py`.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.domain.authz import has_permission, require
from thermoctl.domain.control import settings as control_settings
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ScheduleError,
    cancel_vacation,
    create_vacation,
    current_or_upcoming_vacation,
)
from thermoctl.web import templates
from thermoctl.web.forms import FormError
from thermoctl.web.guards import admin_ui_only
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: see the same note in every other HTML router -- these
# routes serve humans, not the OpenAPI description.
# `admin_ui_only`: diese Seiten gehören zur Anlagensicht. Ein Mieterprofil wird
# hier schon vor der Rechteprüfung abgewiesen -- eine ausgeblendete Verknüpfung
# in der Navigation ist kein Riegel (siehe `web/guards.py`). Die bestehenden
# Rechteprüfungen in den Endpunkten bleiben davon unberührt bestehen.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(admin_ui_only)],
    include_in_schema=False,
)


def _page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    errors: FormError | None = None,
) -> Response:
    row = control_settings(session)
    now = utcnow()
    vacation = current_or_upcoming_vacation(session, now)
    return templates.TemplateResponse(
        request,
        "vacation.html",
        {
            "vacation": vacation,
            "running": vacation is not None and vacation.starts_at <= now,
            "timezone": row.timezone,
            "values": values or {},
            "errors": {errors.field: errors.notice} if errors else {},
            "may_edit": has_permission(principal, "vacation.manage"),
        },
    )


@router.get("/vacation")
async def show_vacation(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.read")
    return _page(request, session, principal)


@router.post("/vacation")
async def save_vacation(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "vacation.manage")
    row = control_settings(session)
    form = await request.form()
    values = {
        "start_date": str(form.get("start_date", "")).strip(),
        "end_date": str(form.get("end_date", "")).strip(),
        "setback_temperature_c": str(form.get("setback_temperature_c", "")).strip(),
    }
    try:
        try:
            start_date = date.fromisoformat(values["start_date"])
        except ValueError as exc:
            raise FormError("start_date", "Bitte ein gültiges Datum eingeben.") from exc
        try:
            end_date = date.fromisoformat(values["end_date"])
        except ValueError as exc:
            raise FormError("end_date", "Bitte ein gültiges Datum eingeben.") from exc
        try:
            setback_temperature_c = Decimal(values["setback_temperature_c"].replace(",", "."))
        except Exception as exc:  # noqa: BLE001 -- turned into the same field error below
            raise FormError(
                "setback_temperature_c", "Bitte eine gültige Zahl eingeben."
            ) from exc
        create_vacation(
            session,
            start_date=start_date,
            end_date=end_date,
            setback_temperature_c=setback_temperature_c,
            timezone_name=row.timezone,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="web",
        )
    except (FormError, ScheduleError, DomainError) as exc:
        return _page(
            request,
            session,
            principal,
            values=values,
            errors=FormError(exc.field, exc.notice),
        )
    return RedirectResponse(prefixed(request, "/vacation"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/vacation/cancel")
async def cancel_vacation_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "vacation.manage")
    cancel_vacation(
        session, user_id=principal.user_id, token_id=principal.token_id, source="web"
    )
    return RedirectResponse(prefixed(request, "/vacation"), status_code=status.HTTP_303_SEE_OTHER)
