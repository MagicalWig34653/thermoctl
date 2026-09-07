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

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.absence import absence_zones, end_absence, running_absence, start_absence
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.problem_report import REPORT_KINDS
from thermoctl.domain.schedule import (
    ScheduleError,
    ScheduleSnapshot,
    adopt_schedule,
    copy_schedule_day,
    create_schedule_point,
    delete_schedule_point,
    move_schedule_point,
    next_switch,
    time_of_day_in_minutes,
    undo_schedule_gesture,
    week_segments,
)
from thermoctl.domain.statistics import (
    PERIODS,
    as_duration,
    heating_periods,
    period_days,
)
from thermoctl.domain.time import local_day_start_utc, local_time
from thermoctl.web import templates
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


def _home_notice(
    zones: list[Zone], states: dict[int, tuple[ZoneState, SensorStatus]]
) -> dict[str, object] | None:
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
        zone_state, sensor_status = entry
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
        zone_state, _sensor_status = entry
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
    states = cast("dict[int, tuple[ZoneState, SensorStatus]]", context["states"])
    # "Als Nächstes: 18,0 °C um 23:00" -- aus der Domäne, nicht aus einer zweiten
    # Rechnung in der Vorlage oder im Browser. Dieselbe Funktion, die auch der
    # Sprung zur nächsten Schaltzeit benutzt: was die Karte ankündigt und was der
    # Knopf daneben tut, kann so nicht auseinanderlaufen.
    next_switches = {zone.id: next_switch(session, zone, now) for zone in zones}
    absence = running_absence(session, principal.user_id, now)

    return templates.TemplateResponse(
        request,
        "tenant_start.html",
        {
            **context,
            "user": getattr(request.state, "user", None),
            "zones": zones,
            "notice": _home_notice(zones, states),
            "next_switches": next_switches,
            # Die laufende Abwesenheit dieses Benutzers samt ihrer Räume -- damit
            # sie sichtbar ist und sich in einem Schritt beenden lässt, statt in
            # jedem Raum einzeln.
            "absence": absence,
            "absence_zone_names": (
                [zone.display_name for zone in absence_zones(session, absence)]
                if absence is not None
                else []
            ),
            "absence_errors": request.query_params.get("absence_errors"),
            "report_errors": request.query_params.get("report_errors"),
            "report_notice": request.query_params.get("report_notice"),
            # Die Problemarten kommen aus der Domäne, nicht aus der Vorlage: der
            # Server prüft die gewählte gegen dieselbe Liste.
            "report_kinds": REPORT_KINDS,
            "may_report": {
                zone.id
                for zone in visible_zones(session, principal, "report.create")
            },
            "absence_zone_count": len(
                visible_zones(session, principal, "override.create")
            ),
            "timezone": context["timezone"],
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
        return _tenant_schedule_page(
            request, session, principal, zone, zones, gesture_error=exc.notice
        )
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
        raw_before = cast(list[list[int]], payload["before"])
        raw_after = cast(list[list[int]], payload["after"])
        revision = int(cast(int, payload["revision"]))
        before = cast(ScheduleSnapshot, tuple(tuple(point) for point in raw_before))
        after = cast(ScheduleSnapshot, tuple(tuple(point) for point in raw_after))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from None
    try:
        undo_schedule_gesture(
            session, zone, before=before, expected_after=after,
            expected_revision=revision,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _tenant_schedule_page(
            request, session, principal, zone, zones, gesture_error=exc.notice
        )
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
    if key not in PERIODS:
        key = "7"
    _label, days = period_days(key)

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
            "periods": [(code, label) for code, (label, _days) in PERIODS.items()],
            "period": key,
            "armed": armed,
            "as_duration": as_duration,
        },
    )


# -- Abwesenheit ----------------------------------------------------------------


@router.post("/absence")
async def start_absence_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """„Ich bin bis Samstag weg -- regelt meine Räume bis dahin sparsamer."

    Welche Räume das sind, entscheidet **ausschließlich der Server**:
    `visible_zones(..., "override.create")` liefert genau die Räume, für die dieser
    Principal übersteuern darf. Es gibt hier bewusst kein Formularfeld für die
    Zonenauswahl -- ein Feld wäre eine Angabe, der man nicht glauben darf, und ein
    zweiter Weg, an dem eine Prüfung fehlen könnte.

    Die Domäne legt Klammer und Übersteuerungen in einem Zug an oder gar nicht
    (`domain.absence.start_absence`). Eine halb abgesenkte Wohnung, die niemand mehr
    auflösen kann, ist ausdrücklich ausgeschlossen.
    """
    form = await request.form()
    zones = visible_zones(session, principal, "override.create")
    now = utcnow()
    settings = session.get(Setting, 1)
    timezone_name = settings.timezone if settings is not None else "UTC"

    try:
        temperature = Decimal(str(form.get("temperature_c", "")).replace(",", "."))
        # Der Mieter gibt sein Rückkehrdatum in Ortszeit an -- gespeichert wird wie
        # überall im Projekt naives UTC.
        ends_at = local_day_start_utc(
            date.fromisoformat(str(form.get("return_on", ""))), timezone_name
        )
    except (InvalidOperation, ValueError):
        return _home_with_error(
            request, session, principal,
            "Bitte Rückkehrdatum und Temperatur prüfen.",
        )
    try:
        start_absence(
            session, zones, temperature, ends_at,
            now=now, user_id=principal.user_id, token_id=principal.token_id,
        )
    except DomainError as exc:
        return _home_with_error(request, session, principal, exc.notice)
    return RedirectResponse(prefixed(request, "/"), status.HTTP_303_SEE_OTHER)


@router.post("/absence/end")
async def end_absence_view(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Beendet die eigene laufende Abwesenheit vorzeitig.

    Ohne Id im Formular: es gibt je Benutzer höchstens eine laufende, und der Server
    sucht sie selbst. Eine fremde Abwesenheit ist damit gar nicht adressierbar.
    """
    running = running_absence(session, principal.user_id, utcnow())
    if running is None:
        return _home_with_error(
            request, session, principal, "Es läuft gerade keine Abwesenheit."
        )
    end_absence(session, running)
    return RedirectResponse(prefixed(request, "/"), status.HTTP_303_SEE_OTHER)


def _home_with_error(
    request: Request, session: Session, principal: Principal, notice: str
) -> Response:
    """Der Fehlerfall der Abwesenheit landet als Meldung auf der Startseite.

    Über einen Abfrageparameter und eine Weiterleitung -- dasselbe Muster, das die
    Übersteuerung schon benutzt, damit ein Neuladen nicht dieselbe Aktion erneut
    auslöst.
    """
    return RedirectResponse(
        prefixed(request, "/?" + urlencode({"absence_errors": notice})),
        status.HTTP_303_SEE_OTHER,
    )
