"""Two pages that operate on the same settings row -- and still belong apart.

`/steuerung` is **operations**: whether the plant is really switching right now, what
it's currently deciding, and the button that flips both. This is what you look at when
something's wrong.

`/einstellungen` are the **control defaults**: hysteresis, minimum switch durations,
cycle time, retention, timezone. You set these once and then not again for years.

At first both lived on one page. That was convenient to build and wrong to use:
whoever wanted to check whether the plant is armed scrolled past nine number fields
that didn't interest them at that moment -- and whoever wanted to change a default
landed on the arm button first.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.domain.authz import has_permission, require, visible_zones
from thermoctl.domain.control import (
    GANZZAHLIG,
    LABELS,
    LIMITS,
    ControlError,
    arm,
    save_settings,
    settings,
)
from thermoctl.domain.interfaces import uebersicht
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.statistics import as_duration, heizzeiten
from thermoctl.web import templates

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


def _page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    errors: ControlError | None = None,
) -> Response:
    zeile = settings(session)
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()

    zustaende = {
        zone_id: (state, status)
        for zone_id, state, status in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_([zone.id for zone in zones]))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "steuerung.html",
        {
            "settings": zeile,
            "zones": zones,
            "zustaende": zustaende,
            "entscheidungen": entscheidungen,
            "setpoints": {
                zone.id: resolved_setpoint(session, zone, now) for zone in zones
            },
            "errors": {errors.feld: errors.notice} if errors else {},
            "darf_scharf": has_permission(principal, "control.arm"),
            # The first bolt sits in the MQTT client's constructor and is read from
            # the database at startup. Whoever arms the plant while it is running
            # ends up with a state where the plant decides while armed and still
            # sends nothing. That is intentional -- but it has to show up here,
            # otherwise someone spends hours hunting for the bug.
            "sending_allowed": getattr(request.app.state, "sending_allowed", False),
        },
    )


def _defaults_page(
    request: Request,
    session: Session,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    errors: ControlError | None = None,
) -> Response:
    zeile = settings(session)
    if values is None:
        values = {feld: str(getattr(zeile, feld)) for feld in LIMITS}
        values["timezone"] = zeile.timezone
    return templates.TemplateResponse(
        request,
        "einstellungen.html",
        {
            "felder": [(feld, LABELS[feld], feld in GANZZAHLIG) for feld in LIMITS],
            "values": values,
            "errors": {errors.feld: errors.notice} if errors else {},
            "darf_aendern": has_permission(principal, "setting.manage"),
        },
    )


@router.get("/control")
async def show_control(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Whoever may see the plant may read this. The operating state is the answer to
    # "is this thing actually switching right now?" -- nobody should have to guess it.
    require(principal, "zone.read")
    return _page(request, session, principal)


@router.get("/settings")
async def show_settings(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "zone.read")
    return _defaults_page(request, session, principal)


@router.post("/settings")
async def save_defaults(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require(principal, "setting.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in (*LIMITS, "timezone")
    }
    try:
        save_settings(
            session,
            values,
            values["timezone"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _defaults_page(request, session, principal, values=values, errors=exc)
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/control/arm")
async def arm_view(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Its own permission, not `setting.manage`: this here moves a valve.
    require(principal, "control.arm")
    form = await request.form()
    armed = str(form.get("armed", "")) == "ja"
    try:
        arm(
            session,
            armed,
            reason=str(form.get("begruendung", "")),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        return _page(request, session, principal, errors=exc)
    return RedirectResponse("/control", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/interfaces")
async def show_interfaces(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """What is connected from outside -- and whether it's really running.

    `setting.manage`, not `zone.read`: the page names broker addresses, webhook
    targets, and account names. None of that is a secret in the strict sense, but it's
    also nothing every operator of the heating needs to see.
    """
    require(principal, "setting.manage")
    return templates.TemplateResponse(
        request,
        "schnittstellen.html",
        {
            "interfaces": uebersicht(
                session,
                get_settings(),
                getattr(request.app.state, "bridge_reachable", None),
            ),
        },
    )


# Time ranges people actually want to know about. No free-form date field: the
# question is "this week" or "this month", not "from the 14th to the 23rd".
ZEITRAEUME: dict[str, tuple[str, int]] = {
    "7": ("7 Tage", 7),
    "30": ("30 Tage", 30),
    "90": ("90 Tage", 90),
}


@router.get("/statistics")
async def show_statistics(
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """When and for how long there was heating, per zone and day.

    In the dry run this is a statement about what thermoctl *would have* heated -- the
    page says so too, instead of just putting up a number that could be mistaken for
    the plant's actual history.
    """
    require(principal, "zone.read")
    zones = visible_zones(session, principal, "zone.read")
    zeile = settings(session)

    schluessel = request.query_params.get("zeitraum", "7")
    if schluessel not in ZEITRAEUME:
        schluessel = "7"
    _label, days = ZEITRAEUME[schluessel]

    bis = utcnow()
    von = (bis - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    values = heizzeiten(
        session,
        [zone.id for zone in zones],
        von,
        bis,
        cycle_seconds=zeile.shadow_interval_seconds,
    )
    # The single longest day value determines the height of the bars. Scaling per
    # zone would be more comfortable to read and would falsify the comparison
    # between zones -- and that comparison is exactly why the zones are stacked.
    maximum = max(
        (t.seconds for stat in values.values() for t in stat.days), default=0
    )
    return templates.TemplateResponse(
        request,
        "statistik.html",
        {
            "zones": zones,
            "values": values,
            "maximum": maximum,
            "zeitraeume": [(s, b) for s, (b, _t) in ZEITRAEUME.items()],
            "zeitraum": schluessel,
            "armed": zeile.control_armed,
            "as_duration": as_duration,
        },
    )
