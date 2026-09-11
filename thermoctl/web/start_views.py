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

from datetime import datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_protection, get_session
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import has_permission, principal_for_user, visible_zones
from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C
from thermoctl.domain.outdoor import outdoor_reading
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    BulkSetpointContext,
    current_or_upcoming_vacation,
    resolved_setpoint,
    running_vacation,
    week_segments,
)
from thermoctl.domain.time import local_time
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.domain.zones import latest_decisions_by_zone
from thermoctl.services import cluster
from thermoctl.setup import setup_needed
from thermoctl.web import templates, warmth_fraction
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


MINUTES_PER_DAY = 1440


def _day_track(
    modes: dict[int, SetpointMode],
    temperatures: dict[tuple[int, int], Decimal],
    points_per_zone: dict[int, list[SchedulePoint]],
    weekday: int,
) -> dict[int, list[dict[str, object]]]:
    """Today's schedule per zone as segments with share, time, and setpoint.

    The same decomposition as the week view (`wochenabschnitte`), just restricted to
    one day -- a second version of the same logic in the browser would be exactly what
    principle 6 forbids.

    Takes its data pre-fetched rather than querying: `zone_status_context` (the only
    caller) already loads all three -- modes, per-zone-and-mode temperatures, and
    schedule points -- once for the whole page, the same data `resolved_setpoint`
    needs per zone. Querying them again here would be the exact N+1 duplication this
    module exists to avoid.
    """
    if not points_per_zone:
        return {}
    names = {identifier: mode.name for identifier, mode in modes.items()}

    tracks: dict[int, list[dict[str, object]]] = {}
    for zone_id, points in points_per_zone.items():
        segments = [
            a for a in week_segments(points, names) if a.weekday == weekday
        ]
        tracks[zone_id] = [
            {
                "start": segment.start_minute,
                "width": (segment.end_minute - segment.start_minute)
                * 100
                / MINUTES_PER_DAY,
                "left": segment.start_minute * 100 / MINUTES_PER_DAY,
                "mode_name": segment.mode_name,
                "temperature": temperatures.get((zone_id, segment.mode_id)),
                "warmth": warmth_fraction(temperatures.get((zone_id, segment.mode_id))),
            }
            for segment in segments
        ]
    return tracks


def zone_status_context(
    session: Session,
    principal: Principal,
    zones: list[Zone],
    now: datetime,
    settings: Setting | None,
) -> dict[str, object]:
    """Was beide Startseiten -- Anlage und Wohnung -- gleichermaßen brauchen:
    Zustand, aufgelöster Sollwert, laufende Übersteuerung, letzte Entscheidung,
    Bearbeitungsrechte und der Tagesverlauf je sichtbarer Zone.

    Eine Rechnung statt zweier: Grundsatz 6 verbietet eine zweite Fassung derselben
    Logik im Browser -- dieselbe Regel gilt hier zwischen den beiden Serveransichten.
    Die Anlagensicht (`start()` unten) ergänzt danach ihre eigenen, zusätzlichen
    Daten (Riegel, Brücke, Verbund, Urlaub, Außentemperatur); die Mieteransicht
    bekommt diese Zusätze gar nicht erst in ihren Kontext.
    """
    zone_ids = [zone.id for zone in zones]
    now_utc = now
    settings_row = settings
    local_now = local_time(now_utc, settings_row.timezone if settings_row is not None else None)
    states = {
        zone_id: (state, sensor_status_of)
        for zone_id, state, sensor_status_of in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_(zone_ids))
        )
    }
    # Bündelung statt N+1 (siehe fix/uebersicht-tempo): eine Anfrage je Tabelle über
    # alle sichtbaren Zonen, statt `resolved_setpoint` seine gut ein Dutzend eigenen
    # Abfragen je Zone selbst stellen zu lassen -- auf MariaDB hinter einem
    # Reverse Proxy kostet jede davon eine echte Netzrunde. `BulkSetpointContext`
    # trägt genau dieselbe Vorrangkette wie zuvor, nur mit vorab geladenen Daten
    # statt eigener Abfragen je Zone (Grundsatz 6: eine einzige Fassung der Logik).
    #
    # `overrides` (Anzeige des Übersteuerungs-Banners) und `overrides_by_zone` (was
    # `resolved_setpoint` als laufende Übersteuerung ansieht) sind bewusst dieselbe
    # Abfrage: vorher hatte die Anzeige-Abfrage keine `id`-Tiebreak-Sortierung, die
    # zonenweise Auflösung über `_running_override` aber schon -- bei zwei
    # Übersteuerungen innerhalb derselben MariaDB-Sekunde (Sekundenpräzision) konnten
    # Banner und tatsächliche Entscheidung damit unbemerkt auseinanderlaufen. Jetzt
    # sind es garantiert dieselbe Zeile.
    overrides: dict[int, ZoneOverride] = {}
    for entry in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now_utc,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > now_utc),
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ):
        overrides.setdefault(entry.zone_id, entry)
    decisions = latest_decisions_by_zone(session, zone_ids)

    modes_by_id = {m.id: m for m in session.scalars(select(SetpointMode))}
    temperatures: dict[tuple[int, int], Decimal] = {
        (zone_id, mode_id): temperature
        for zone_id, mode_id, temperature in session.execute(
            select(
                ZoneSetpoint.zone_id,
                ZoneSetpoint.setpoint_mode_id,
                ZoneSetpoint.temperature_c,
            ).where(ZoneSetpoint.zone_id.in_(zone_ids))
        )
    }
    points_per_zone: dict[int, list[SchedulePoint]] = {zone_id: [] for zone_id in zone_ids}
    for point in session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id.in_(zone_ids))
    ):
        points_per_zone[point.zone_id].append(point)

    # Only built when there is at least one zone to resolve -- `zone_ids` empty
    # means the `setpoints` dict comprehension below never calls `resolved_setpoint`
    # at all, so `ctx` is never read. Guarding it here (rather than asserting
    # `settings_row` unconditionally) matters for exactly the case the original,
    # per-zone `resolved_setpoint` call never hit either: a principal with zero
    # visible zones, on an instance whose `setting` row does not exist yet.
    ctx: BulkSetpointContext | None = None
    if zone_ids:
        assert settings_row is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
        frost_code = session.scalar(
            select(SetpointMode.code).where(
                SetpointMode.id == settings_row.frost_protection_mode_id
            )
        )
        ctx = BulkSetpointContext(
            settings=settings_row,
            frost_code=frost_code,
            temperatures=temperatures,
            modes_by_id=modes_by_id,
            overrides_by_zone=overrides,
            vacation=running_vacation(session, now_utc),
            points_by_zone=points_per_zone,
        )

    return {
        "states": states,
        "setpoints": {
            zone.id: resolved_setpoint(session, zone, now_utc, ctx) for zone in zones
        },
        "overrides": overrides,
        "decisions": decisions,
        "may_override": {
            zone.id
            for zone in zones
            if has_permission(principal, "override.create", zone.id)
        },
        "may_cancel": {
            zone.id
            for zone in zones
            if has_permission(principal, "override.cancel", zone.id)
        },
        "may_edit_setpoint": {
            zone.id
            for zone in zones
            if has_permission(principal, "setpoint.write", zone.id)
        },
        "minimum_temperature": MINIMUM_TEMPERATURE_C,
        "maximum_temperature": MAXIMUM_TEMPERATURE_C,
        "mode_names": {identifier: mode.name for identifier, mode in modes_by_id.items()},
        "day_tracks": _day_track(
            modes_by_id, temperatures, points_per_zone, local_now.isoweekday()
        ),
        "now_fraction": (local_now.hour * 60 + local_now.minute) * 100 / MINUTES_PER_DAY,
        "timezone": settings_row.timezone if settings_row is not None else "UTC",
        "poll_interval_seconds": (
            settings_row.shadow_interval_seconds if settings_row is not None else 60
        ),
    }


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
