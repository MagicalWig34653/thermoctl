"""Die Mieteroberfläche: Startseite, Zeitplan und Heizzeit.

Diese drei Seiten haben keine eigene Route für `/`, weil `/` für beide Oberflächen
eine einzige Adresse bleibt (`start_views.start` verzweigt nach
`principal.ui_profile`). `/schedule` und `/heating-time` haben dagegen eigene
Routen, mit `tenant_ui_only` als Router-Dependency abgesichert.

Zonenisolation ist die wichtigste Regel dieser Seiten: ein Mieter sieht
ausschließlich, was `visible_zones(session, principal, "zone.read")` liefert. Jede
Zonen-Id aus Formular oder Pfad wird serverseitig erneut gegen `visible_zones`
geprüft (`_zone_or_404`, dasselbe Muster wie in `daily_views.py` und
`schedule_views.py`) und führt sonst zu 404 -- nie 403, das den Unterschied zwischen
"gibt es nicht" und "gehört jemand anderem" verriete.

Keine zweite Zeitplanmechanik: jede Änderung läuft über dieselben
Domänenfunktionen wie der Admin-Editor (`domain.schedule`). Diese Seite ist nur
eine einfachere Oberfläche auf dieselben Daten.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ScheduleError,
    adopt_schedule,
    copy_schedule_day,
    create_schedule_point,
    delete_schedule_point,
    move_schedule_point,
    schedule_forecast,
    time_of_day_in_minutes,
    undo_schedule_gesture,
    week_segments,
)
from thermoctl.domain.statistics import ZEITRAEUME_TAGE, as_duration, heating_periods
from thermoctl.domain.time import local_day_start_utc, local_time
from thermoctl.web import templates, warmth_fraction
from thermoctl.web.guards import tenant_ui_only
from thermoctl.web.schedule_views import (
    WEEKDAYS,
    _forecast_bars,
    _undo_payload,
    _undo_token,
)
from thermoctl.web.start_views import zone_status_context
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: siehe die Begründung in jedem anderen HTML-Router
# dieses Projekts -- die OpenAPI-Beschreibung ist der Vertrag der REST-Schnittstelle.
router = APIRouter(
    dependencies=[Depends(csrf_protection), Depends(tenant_ui_only)],
    include_in_schema=False,
)


def _zone_or_404(session: Session, principal: Principal, zone_id: int, permission: str) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, permission) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _home_notice(zones: list[Zone], states: dict[int, object]) -> dict[str, object] | None:
    """Der für den Mieter relevante Effekt eines Problems -- nicht die Ursache.

    Erlaubt sind ausschließlich ein stiller/festhängender Sensor und ein offenes
    Fenster, je einer sichtbaren Zone. Ausdrücklich nicht: MQTT, Broker, Brücke,
    ``control_armed``, Schattenentscheidung, PI, Hysterese, Verbundrolle, Relais,
    Gerätenamen -- keines davon sagt einem Mieter etwas über seine Wohnung.
    """
    for zone in zones:
        entry = states.get(zone.id)
        if entry is None:
            continue
        zone_state, sensor_status = entry  # type: ignore[misc]
        if sensor_status.code != "ok":
            return {
                "kind": "sensor",
                "zone_name": zone.display_name,
                "measured_at": zone_state.measured_at,
            }
    for zone in zones:
        entry = states.get(zone.id)
        if entry is None:
            continue
        zone_state, _sensor_status = entry  # type: ignore[misc]
        if zone_state.window_open:
            return {"kind": "window", "zone_name": zone.display_name}
    return None


def render_home(request: Request, session: Session, principal: Principal) -> Response:
    """Die Wohnungs-Startseite. Von `start_views.start` gerufen -- keine eigene Route,
    damit `/` für beide Oberflächen eine Adresse bleibt.
    """
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()
    settings = session.get(Setting, 1)
    context = zone_status_context(session, principal, zones, now, settings)
    states = context["states"]

    return templates.TemplateResponse(
        request,
        "tenant_start.html",
        {
            **context,
            "user": getattr(request.state, "user", None),
            "zones": zones,
            "notice": _home_notice(zones, states),  # type: ignore[arg-type]
            "thermostat_errors": request.query_params.get("thermostat_errors"),
            "override_errors": request.query_params.get("override_errors"),
            "jump_next_errors": request.query_params.get("jump_next_errors"),
            "error_zone_id": request.query_params.get("zone_id"),
            "override_values": request.query_params,
        },
    )


@router.get("/schedule")
async def show_tenant_schedule(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zones = visible_zones(session, principal, "zone.read")
    if not zones:
        return templates.TemplateResponse(request, "tenant_schedule_empty.html", {})

    requested = request.query_params.get("zone")
    zone = zones[0]
    if requested is not None:
        try:
            requested_id = int(requested)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
        zone = _zone_or_404(session, principal, requested_id, "zone.read")

    return _tenant_schedule_page(request, session, principal, zone, zones)


def _tenant_schedule_page(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    zones: list[Zone],
    *,
    gesture_error: str = "",
    gesture_notice: str = "",
    undo_token: str = "",
    day_error: str = "",
) -> Response:
    points = list(
        session.query(SchedulePoint)
        .filter(SchedulePoint.zone_id == zone.id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    modes = {m.id: m.name for m in session.query(SetpointMode)}
    segments = week_segments(points, modes)
    by_day = {
        day: [segment for segment in segments if segment.weekday == day]
        for day, _name in WEEKDAYS
    }
    # Genau zwei Punkte je Tag lassen sich mit "Warm ab"/"Nacht ab" abbilden --
    # die vereinfachte Mieterbedienung aus dem UX-Zielbild. Ein Tag mit einer
    # anderen Punktzahl bleibt lesbar, aber ohne die einfache Bearbeitung: eine
    # dritte Schaltzeit ließe sich mit zwei Feldern nicht ehrlich abbilden, ohne
    # eine der vorhandenen Schaltzeiten stillschweigend zu verwerfen.
    day_points: dict[int, list[SchedulePoint]] = {day: [] for day, _name in WEEKDAYS}
    for point in points:
        day_points[point.weekday].append(point)
    for day_list in day_points.values():
        day_list.sort(key=lambda p: p.minute_of_day)

    may_edit = has_permission(principal, "schedule.manage", zone.id)
    sources = [other for other in zones if other.id != zone.id]

    return templates.TemplateResponse(
        request,
        "tenant_schedule.html",
        {
            "zone": zone,
            "zones": zones,
            "sources": sources,
            "weekdays": WEEKDAYS,
            "segments": by_day,
            "day_points": day_points,
            "modes": modes,
            "forecast": _forecast_bars(session, zone, modes),
            "may_edit": may_edit,
            "gesture_error": gesture_error,
            "gesture_notice": gesture_notice,
            "undo_token": undo_token,
            "day_error": day_error,
        },
    )


@router.post("/schedule/day")
async def edit_tenant_schedule_day(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Bearbeitet genau einen Tag über zwei Zeitpunkte ("Warm ab"/"Nacht ab").

    Setzt auf den vorhandenen Domänenfunktionen `move_schedule_point` (ein
    geänderter Zeitpunkt) bzw. `delete_schedule_point`/`create_schedule_point`
    (beide Zeitpunkte ändern sich zugleich -- z. B. ein Tausch -- wo ein
    zwischenzeitliches `move_schedule_point` an der noch belegten Zielzeit des
    jeweils anderen Punktes scheitern könnte) auf.
    """
    form = await request.form()
    zones = visible_zones(session, principal, "zone.read")
    try:
        zone_id = int(str(form.get("zone_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    try:
        weekday = int(str(form.get("weekday", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc

    edits: list[tuple[SchedulePoint, int]] = []
    try:
        for index in (1, 2):
            point_id = int(str(form.get(f"point_id_{index}", "")))
            point = session.get(SchedulePoint, point_id)
            if point is None or point.zone_id != zone.id or point.weekday != weekday:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            minute = time_of_day_in_minutes(str(form.get(f"time_{index}", "")))
            edits.append((point, minute))
    except ScheduleError as exc:
        return _tenant_schedule_page(
            request, session, principal, zone, zones, day_error=exc.notice
        )

    changed = [(point, minute) for point, minute in edits if point.minute_of_day != minute]
    try:
        if len(changed) >= 2:
            # Möglicher Tausch: erst beide löschen, dann beide neu anlegen, damit
            # keine Zwischenzeit die jeweils andere Zielzeit noch belegt.
            saved = [
                (point.setpoint_mode_id, minute) for point, minute in changed
            ]
            for point, _minute in changed:
                delete_schedule_point(
                    session, zone, point,
                    user_id=principal.user_id, token_id=principal.token_id,
                )
            for mode_id, minute in saved:
                create_schedule_point(
                    session, zone, weekday=weekday, minute=minute, mode_id=mode_id,
                    user_id=principal.user_id, token_id=principal.token_id,
                )
        elif changed:
            point, minute = changed[0]
            move_schedule_point(
                session, zone, point, weekday=weekday, minute=minute,
                user_id=principal.user_id, token_id=principal.token_id,
            )
    except ScheduleError as exc:
        return _tenant_schedule_page(
            request, session, principal, zone, zones, day_error=exc.notice
        )
    return RedirectResponse(
        prefixed(request, f"/schedule?zone={zone.id}"), status.HTTP_303_SEE_OTHER
    )


@router.post("/schedule/copy-day")
async def copy_tenant_schedule_day(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    zones = visible_zones(session, principal, "zone.read")
    try:
        zone_id = int(str(form.get("zone_id", "")))
        source_weekday = int(str(form.get("weekday", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    scope = str(form.get("scope", ""))
    if scope not in {"workdays", "all"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    targets = list(range(1, 6)) if scope == "workdays" else list(range(1, 8))
    try:
        snapshot = copy_schedule_day(
            session, zone, source_weekday=source_weekday, target_weekdays=targets,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _tenant_schedule_page(request, session, principal, zone, zones, gesture_error=exc.notice)
    return _tenant_schedule_page(
        request, session, principal, zone, zones,
        undo_token=_undo_token(zone.id, snapshot) if snapshot else "",
        gesture_notice=(
            "Es hat sich nichts geändert -- die Zieltage sahen bereits genauso aus."
            if snapshot is None
            else "Der Tag wurde auf Mo–Fr übertragen."
            if scope == "workdays"
            else "Der Tag wurde auf alle Tage übertragen."
        ),
    )


@router.post("/schedule/adopt")
async def adopt_tenant_schedule(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Übernimmt den Zeitplan einer eigenen sichtbaren Zone auf eine andere.

    Ziel- **und** Quell-Id werden erneut gegen `visible_zones` geprüft -- ein
    Formularfeld ist kein Nachweis, dass die Zone tatsächlich sichtbar ist.
    """
    form = await request.form()
    zones = visible_zones(session, principal, "zone.read")
    try:
        zone_id = int(str(form.get("zone_id", "")))
        source_id = int(str(form.get("source_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
    target = _zone_or_404(session, principal, zone_id, "schedule.manage")
    source = _zone_or_404(session, principal, source_id, "zone.read")
    if source.id == target.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    adopt_schedule(
        session, target, source,
        user_id=principal.user_id, token_id=principal.token_id,
    )
    return RedirectResponse(
        prefixed(request, f"/schedule?zone={target.id}"), status.HTTP_303_SEE_OTHER
    )


@router.post("/schedule/undo")
async def undo_tenant_schedule(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    zones = visible_zones(session, principal, "zone.read")
    try:
        zone_id = int(str(form.get("zone_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    try:
        payload = _undo_payload(str(form.get("undo_token", "")))
        if payload["zone_id"] != zone.id:
            raise ValueError("wrong zone")
        before = tuple(tuple(point) for point in payload["before"])  # type: ignore[union-attr]
        after = tuple(tuple(point) for point in payload["after"])  # type: ignore[union-attr]
        revision = int(payload["revision"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from None
    try:
        undo_schedule_gesture(
            session, zone, before=before, expected_after=after,  # type: ignore[arg-type]
            expected_revision=revision,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _tenant_schedule_page(request, session, principal, zone, zones, gesture_error=exc.notice)
    return _tenant_schedule_page(request, session, principal, zone, zones)


@router.get("/heating-time")
async def show_heating_time(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Die reduzierte Heizzeit-Ansicht -- dieselbe Domänenfunktion wie
    `/statistics`, aber nur sichtbare Zonen, drei Zeiträume, kein Freitext-Datum,
    keine Relaisverschleiß- oder Interfaces-Verweise.
    """
    zones = visible_zones(session, principal, "zone.read")
    settings = session.get(Setting, 1)
    timezone_name = settings.timezone if settings is not None else "UTC"
    cycle_seconds = settings.shadow_interval_seconds if settings is not None else 60
    armed = bool(settings and settings.control_armed)

    key = request.query_params.get("period", "7")
    if key not in ZEITRAEUME_TAGE:
        key = "7"
    days = ZEITRAEUME_TAGE[key]

    bis = utcnow()
    first_local_day = local_time(bis, timezone_name).date() - timedelta(days=days - 1)
    start_at = local_day_start_utc(first_local_day, timezone_name)
    values = heating_periods(
        session,
        [zone.id for zone in zones],
        start_at,
        bis,
        cycle_seconds=cycle_seconds,
        timezone_name=timezone_name,
    )
    maximum = max((day.seconds for stat in values.values() for day in stat.days), default=0)

    return templates.TemplateResponse(
        request,
        "tenant_heating_time.html",
        {
            "zones": zones,
            "values": values,
            "maximum": maximum,
            "periods": list(ZEITRAEUME_TAGE.items()),
            "period": key,
            "armed": armed,
            "as_duration": as_duration,
        },
    )
