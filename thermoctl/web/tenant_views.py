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

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.absence import (
    absence_zones,
    end_absence,
    running_absence,
    running_absences,
    start_absence,
)
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

#: Die beiden Zeiten, die ein noch leerer Tag vorschlägt. Nur ein Startpunkt zum
#: Überschreiben, keine fachliche Aussage -- deshalb hier und nicht in der Domäne.
SUGGESTED_TIMES = (6 * 60 + 30, 22 * 60)


def _modes_for_an_empty_day(
    session: Session, zone: Zone
) -> tuple[tuple[int, str], tuple[int, str]] | None:
    """Die zwei Modi, mit denen ein noch leerer Tag angelegt wird -- oder `None`.

    Der vereinfachte Editor hat zwei Felder, „warm ab" und „kühler ab". Welche zwei
    Modi das für **diesen** Raum sind, kann er nicht raten: Modi sind frei
    benannt und je Zone mit eigenen Sollwerten hinterlegt. Genommen werden deshalb
    die beiden wärmsten Sollwerte dieser Zone -- der wärmere für das erste Feld, der
    nächstkühlere für das zweite. Das ist dieselbe Ordnung, die der Mieter auf der
    Seite ohnehin sieht.

    Der Frostschutz bleibt außen vor: er ist die untere Schranke der Regelung und
    kein Abschnitt eines Tagesablaufs.

    `None`, wenn dafür nicht genug da ist -- dann bietet die Seite den Editor für
    einen leeren Tag gar nicht erst an und sagt, was fehlt. Die Modi werden hier
    **serverseitig** bestimmt und nicht aus dem Formular gelesen: eine Modus-Id aus
    dem Browser wäre eine weitere Angabe, die geprüft werden müsste, und sie brächte
    nichts, was der Server nicht ohnehin weiß.
    """
    settings = session.get(Setting, 1)
    frost_id = settings.frost_protection_mode_id if settings is not None else None
    rows = session.execute(
        select(SetpointMode.id, SetpointMode.name, ZoneSetpoint.temperature_c)
        .join(ZoneSetpoint, ZoneSetpoint.setpoint_mode_id == SetpointMode.id)
        .where(ZoneSetpoint.zone_id == zone.id)
    ).all()
    usable = sorted(
        ((mode_id, name, temperature) for mode_id, name, temperature in rows
         if mode_id != frost_id),
        key=lambda row: row[2],
        reverse=True,
    )
    if len(usable) < 2:
        return None
    return (usable[0][0], usable[0][1]), (usable[1][0], usable[1][1])


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
            "zones": zones,
            "notice": _home_notice(zones, states),
            "next_switches": next_switches,
            # Die laufende Abwesenheit dieses Benutzers samt ihrer Räume -- damit
            # sie sichtbar ist und sich in einem Schritt beenden lässt, statt in
            # jedem Raum einzeln.
            "absence": absence,
            # Durch `visible_zones` gefiltert, obwohl die Abwesenheit nur Räume
            # enthält, die der Anlegende damals bedienen durfte: Rechte können
            # zurückgenommen werden, während eine Abwesenheit läuft. Der Name eines
            # Raums, den jemand heute nicht mehr sehen darf, hat auch dann nichts in
            # einer Antwort zu suchen, wenn er ihn gestern selbst abgesenkt hat.
            "absence_zone_names": (
                [
                    zone.display_name
                    for zone in absence_zones(session, absence)
                    if zone.id in {visible.id for visible in zones}
                ]
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
    # Die Wärme je Modus -- dieselbe Skala wie die Tagesspur der Startseite und die
    # Wochenansicht der Anlagensicht. Ohne sie müsste die Vorlage die Farbe raten
    # ("der erste Abschnitt wird schon der kühle sein"), und der Wochenplan zeigte
    # dann *dass* umgeschaltet wird, aber nicht wohin -- gelegentlich sogar falsch
    # herum.
    warmth = {
        mode_id: warmth_fraction(temperature)
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
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
    # Die Ziele einer Übernahme sind die Räume, in die geschrieben werden **darf** --
    # nicht die, die man lesen darf. Das ist genau die Zone, an der der Endpunkt
    # `schedule.manage` verlangt (`adopt_tenant_schedule` weiter unten); die Quelle
    # braucht nur `zone.read`.
    #
    # Vorher stand hier "alle anderen sichtbaren Räume", und ob das Formular
    # überhaupt erschien, hing am Schreibrecht der **angezeigten** Zone. Bei
    # zonenbezogenen Rechten war das gleich doppelt falsch: ein angebotenes Ziel
    # ohne Schreibrecht endete mit 404, und eine erlaubte Übernahme blieb verborgen,
    # sobald man sie vom nur lesbaren Raum aus ansah.
    adopt_targets = [
        other
        for other in visible_zones(session, principal, "schedule.manage")
        if other.id != zone.id
    ]

    return templates.TemplateResponse(
        request,
        "tenant_schedule.html",
        {
            "zone": zone,
            "zones": zones,
            "adopt_targets": adopt_targets,
            "weekdays": WEEKDAYS,
            "segments": by_day,
            "day_points": day_points,
            "modes": modes,
            "warmth": warmth,
            "forecast": _forecast_bars(session, zone, modes),
            "may_edit": may_edit,
            # Für einen Tag ohne jede Schaltzeit: mit welchen zwei Modi er angelegt
            # würde, und welche Zeiten das Formular vorschlägt. `None` heißt, dass
            # für diesen Raum noch keine zwei Sollwerte hinterlegt sind -- dann gibt
            # es nichts anzulegen, und die Seite sagt das.
            "empty_day_modes": _modes_for_an_empty_day(session, zone),
            "suggested_times": [
                f"{minute // 60:02d}:{minute % 60:02d}" for minute in SUGGESTED_TIMES
            ],
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

    # Ein Tag ohne jede Schaltzeit wird **angelegt** statt verschoben. Ohne diesen
    # Zweig käme ein Mieter an einen frisch angelegten Raum gar nicht heran: dort
    # gibt es keine Punkte, also nichts zu verschieben, und jemand mit der
    # Anlagensicht hätte die ersten zwei erst setzen müssen.
    if not str(form.get("point_id_1", "")).strip():
        return _create_day(request, session, principal, zone, zones, weekday, form)

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


def _create_day(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    zones: list[Zone],
    weekday: int,
    form: Mapping[str, object],
) -> Response:
    """Legt die beiden Schaltzeiten eines noch leeren Tages an.

    Die **Modi bestimmt der Server** (`_modes_for_an_empty_day`), nicht das
    Formular -- eine Modus-Id aus dem Browser wäre eine weitere Angabe, der man
    nicht glauben darf, und sie brächte nichts, was der Server nicht ohnehin weiß.

    Hat der Tag entgegen der Annahme doch schon Schaltzeiten -- zwei Fenster
    nebeneinander, im zweiten steht das alte Formular --, wird nichts angelegt: sonst
    stünden danach vier Punkte an einem Tag, den der Mieter mit zwei Feldern nicht
    mehr bearbeiten könnte.
    """
    vorhanden = session.scalars(
        select(SchedulePoint).where(
            SchedulePoint.zone_id == zone.id, SchedulePoint.weekday == weekday
        )
    ).first()
    if vorhanden is not None:
        return _tenant_schedule_page(
            request, session, principal, zone, zones,
            day_error=(
                "Dieser Tag hat inzwischen Schaltzeiten. Bitte die Seite neu laden."
            ),
        )

    modi = _modes_for_an_empty_day(session, zone)
    if modi is None:
        return _tenant_schedule_page(
            request, session, principal, zone, zones,
            day_error=(
                "Für diesen Raum sind noch keine zwei Temperaturen hinterlegt -- "
                "ohne sie gibt es nichts, wozwischen ein Tag umschalten könnte."
            ),
        )

    try:
        zeiten = [
            time_of_day_in_minutes(str(form.get(f"time_{index}", ""))) for index in (1, 2)
        ]
    except ScheduleError as exc:
        return _tenant_schedule_page(
            request, session, principal, zone, zones, day_error=exc.notice
        )
    if zeiten[0] == zeiten[1]:
        return _tenant_schedule_page(
            request, session, principal, zone, zones,
            day_error="Die beiden Zeiten müssen sich unterscheiden.",
        )

    try:
        for (mode_id, _name), minute in zip(modi, zeiten, strict=True):
            create_schedule_point(
                session, zone, weekday=weekday, minute=minute, mode_id=mode_id,
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
    # **Alle** laufenden, nicht nur die angezeigte: zwei gleichzeitig abgeschickte
    # Formulare können zwei Klammern anlegen (siehe `running_absences`). Bliebe die
    # zweite stehen, wäre die Wohnung nach dem Beenden weiter abgesenkt und niemand
    # käme an sie heran.
    now = utcnow()
    laufende = running_absences(session, principal.user_id, now)
    if not laufende:
        return _home_with_error(
            request, session, principal, "Es läuft gerade keine Abwesenheit."
        )
    for absence in laufende:
        end_absence(session, absence, now=now)
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
