"""The start page: a status board, not a collection of metrics.

It answers exactly one question -- *is the house currently doing what I told it to?*
-- for all zones at once. Anything that doesn't answer this question belongs
elsewhere.

There used to be two count tiles here: the number of zones and the number of
**users**. How many accounts exist says nothing about a heating system; the number was
there because it was easy to compute. Both are gone.

Unlike the protected administration pages, this page does not respond to a
non-logged-in visitor with 401, but redirects to the login: whoever types the
service's address into a browser should see a login form, not an error message.
"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_protection, get_session
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.domain.authz import has_permission, principal_for_user, visible_zones
from thermoctl.domain.outdoor import outdoor_reading
from thermoctl.domain.schedule import (
    current_or_upcoming_vacation,
)
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.services import cluster
from thermoctl.setup import setup_needed
from thermoctl.web import templates
from thermoctl.web.shared.zone_status import zone_status_context
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


@router.get("/")
def start(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Before login: as long as there isn't a single user, nobody can log in. A login
    # form would then be a dead end -- whoever types the service's address is here for
    # setup. That the redirect reveals the empty state is no gain for an attacker:
    # /setup responds visibly differently anyway after setup is complete, and setup
    # itself depends on the one-time token from the log, not on the page's
    # reachability.
    if setup_needed(session):
        return RedirectResponse(prefixed(request, "/setup"), status_code=303)

    cookie_value = request.cookies.get(COOKIE_NAME)
    http_session = resolve_session(session, cookie_value) if cookie_value else None
    user = session.get(User, http_session.user_id) if http_session else None
    if user is None or not user.is_active:
        return RedirectResponse(prefixed(request, "/login"), status_code=303)

    request.state.user = user
    principal = principal_for_user(session, user)
    request.state.principal = principal

    # Verzweigung nach UI-Profil (ARCHITEKTUR-v0.9.0.md §3): `/` bleibt für beide
    # Oberflächen eine einzige Adresse. Die Mieteransicht hat keine eigene Route --
    # sie lebt in `tenant_views.render_home` und wird hier nur aufgerufen. Ein
    # lokaler Import, nicht einer auf Modulebene: `tenant_views` importiert seinerseits
    # `zone_status_context` aus diesem Modul, ein Import auf Modulebene wäre ein
    # Ringschluss.
    if principal.ui_profile is WebUiProfile.TENANT:
        from thermoctl.web import tenant_views

        return tenant_views.render_home(request, session, principal)

    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()
    settings = session.get(Setting, 1)
    # Anlagenweit, kein Wert je Zone -- deshalb ein einzelnes Ergebnis, nicht ein
    # Wert je Zone wie `states` unten. `None` nur vor abgeschlossener Einrichtung
    # (fehlende `setting`-Zeile), sonst antwortet `outdoor_reading` selbst mit
    # "keine_quelle".
    outdoor = outdoor_reading(session, settings, now) if settings is not None else None

    # Running or merely planned, both count: whoever opens the start page in January
    # and does not see that tomorrow's setback is coming has exactly the problem this
    # banner exists to rule out (the project owner's own wording).
    vacation = current_or_upcoming_vacation(session, now)

    context = zone_status_context(session, principal, zones, now, settings)
    states = cast("dict[int, tuple[ZoneState, SensorStatus]]", context["states"])

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            **context,
            "zones": zones,
            "thermostat_errors": request.query_params.get("thermostat_errors"),
            "may_edit_parameters": {
                zone.id for zone in zones if has_permission(principal, "zone.manage", zone.id)
            },
            "override_errors": request.query_params.get("override_errors"),
            "error_zone_id": request.query_params.get("zone_id"),
            "override_values": request.query_params,
            # The plant in one sentence: are both latches open, is the bridge running,
            # and are there sensors that stay silent? Exactly the three things that make
            # a display untrustworthy if you don't know them.
            "armed": bool(settings and settings.control_armed),
            "sending_allowed": getattr(request.app.state, "sending_allowed", False),
            # Aktiv-Bereitschafts-Verbund: whether *this* instance is currently the
            # standby half. Deliberately must be visible everywhere the operating
            # state is -- a standby that looks identical to the active instance is
            # exactly the failure mode this banner exists to rule out. `False` for
            # every single, unclustered installation (see `services/cluster.py`'s
            # module docstring on the fail-open default).
            "cluster_standby": not cluster.is_leader(session, holder=cluster.instance_id()),

            "bridge": getattr(request.app.state, "bridge_reachable", None),
            "silent_sensors": [
                zone.display_name
                for zone in zones
                if zone.id in states and states[zone.id][1].code != "ok"
            ],
            # Deliberately its own banner, not folded into `silent_sensors` above:
            # a stuck reading is present and current, the zone keeps regulating on
            # it normally -- lumping it in with a silent sensor would suggest the
            # same fallback to frost protection that only actually applies there.
            "stuck_sensors": [
                zone.display_name
                for zone in zones
                if zone.id in states and states[zone.id][0].sensor_stuck
            ],
            # Die Außentemperatur -- anlagenweit, deshalb kein Eintrag je Zone,
            # sondern ein einzelner Wert neben Brücke und Scharfschaltung oben.
            "outdoor": outdoor,
            # Ein Fenster ist seit Längerem offen und es ist kalt genug draußen --
            # eine eigene Marke je Zone, nicht in `silent_sensors` oder
            # `stuck_sensors` verwoben: der Sensor ist hier völlig in Ordnung, nur
            # das Fenster steht offen.
            "window_alarm_zones": [
                zone.display_name
                for zone in zones
                if zone.id in states and states[zone.id][0].window_alarm
            ],
            "vacation": vacation,
            "vacation_running": vacation is not None and vacation.starts_at <= now,
        },
    )
