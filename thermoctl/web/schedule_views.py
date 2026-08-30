from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import aktueller_principal, csrf_schutz, get_session
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ScheduleError,
    adopt_schedule,
    create_schedule_point,
    delete_schedule_point,
    move_schedule_point,
    time_of_day_in_minutes,
    week_segments,
)
from thermoctl.web import templates, waermeanteil

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)

WEEKDAYS = (
    (1, "Montag"),
    (2, "Dienstag"),
    (3, "Mittwoch"),
    (4, "Donnerstag"),
    (5, "Freitag"),
    (6, "Samstag"),
    (7, "Sonntag"),
)


def _zone_or_404(
    session: Session, principal: Principal, zone_id: int, permission: str
) -> Zone:
    zone = next(
        (zone for zone in visible_zones(session, principal, permission) if zone.id == zone_id),
        None,
    )
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return zone


def _points(session: Session, zone_id: int) -> list[SchedulePoint]:
    return list(
        session.scalars(
            select(SchedulePoint)
            .where(SchedulePoint.zone_id == zone_id)
            .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
        )
    )


def _modes(session: Session) -> list[SetpointMode]:
    return list(
        session.scalars(
            select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name)
        )
    )


def _schedulepage(
    request: Request,
    session: Session,
    zone: Zone,
    principal: Principal,
    *,
    values: dict[str, str] | None = None,
    errors: ScheduleError | None = None,
    move_error: str = "",
) -> Response:
    points = _points(session, zone.id)
    modes = _modes(session)
    segments = week_segments(points, {mode.id: mode.name for mode in modes})
    # Die Waerme je Modus, damit die Wochenansicht dieselbe Sprache spricht wie die
    # Tagesspur der Startseite: waermer heisst waermer. Ohne sie waeren Tag und Nacht
    # zwei gleich aussehende Balken -- und der Zeitplan zeigte nur, *dass* umgeschaltet
    # wird, nicht wohin.
    temperatures: dict[int, Decimal] = {
        mode_id: temperature
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
    waerme = {mode.id: waermeanteil(temperatures.get(mode.id)) for mode in modes}
    by_day = {
        day: [segment for segment in segments if segment.weekday == day]
        for day, _name in WEEKDAYS
    }
    return templates.TemplateResponse(
        request,
        "zeitplan.html",
        {
            "zone": zone,
            "points": points,
            "modes": modes,
            "wochentage": WEEKDAYS,
            "segments": by_day,
            "waerme": waerme,
            "temperatures": temperatures,
            "values": values or {"weekday": "1", "time_of_day": "06:00", "modus": ""},
            "errors": {errors.feld: errors.notice} if errors else {},
            # Eigener Kanal, nicht `fehler`: Eine abgelehnte Verschiebung meldete sich
            # sonst am Uhrzeitfeld des *Anlege*-Formulars -- beide melden "Zu diesem
            # Zeitpunkt gibt es bereits einen Punkt", und beide schreiben in dasselbe
            # Feld. Der Benutzer sah eine rote Meldung an einem Formular, das er gar
            # nicht angefasst hatte, waehrend der zurueckgesprungene Balken unkommentiert
            # blieb. Im Browser aufgefallen, von keinem Test.
            "move_error": move_error,
            "darf_aendern": has_permission(principal, "schedule.manage", zone.id),
        },
    )


@router.get("/zones/{zone_id}/schedule")
async def show_schedule(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.read")
    return _schedulepage(request, session, zone, principal)


@router.post("/zones/{zone_id}/schedule/points")
async def create_schedule_point_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in ("weekday", "time_of_day", "modus")
    }
    try:
        try:
            weekday = int(values["weekday"])
        except ValueError as exc:
            raise ScheduleError(
                "weekday", "Bitte einen Wochentag auswählen."
            ) from exc
        minute = time_of_day_in_minutes(values["time_of_day"])
        try:
            mode_id = int(values["modus"])
        except ValueError as exc:
            raise ScheduleError("mode_id", "Bitte einen Modus auswählen.") from exc
        create_schedule_point(
            session,
            zone,
            weekday=weekday,
            minute=minute,
            mode_id=mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _schedulepage(
            request, session, zone, principal, values=values, errors=exc
        )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/schedule/points/move")
async def reposition_schedule_point(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Ziel des Ziehens in der Wochenansicht.

    Bewusst ein gewoehnliches Formular und keine JSON-Schnittstelle: Dann gilt derselbe
    CSRF-Schutz, dieselbe Rechtepruefung und dieselbe Fehlerdarstellung wie fuer den Weg
    ueber die Formulare -- und der Zeitplan bleibt ohne JavaScript vollstaendig
    bedienbar, weil das Ziehen nur eine zweite Bedienart derselben Aenderung ist.

    Die Punktkennung steht im Rumpf und nicht im Pfad, anders als beim Loeschen daneben.
    Der Grund ist htmx: `hx-boost` liest die `action` eines Formulars **einmal** beim
    Verarbeiten der Seite. Ein Skript, das sie vor dem Absenden umschreibt, aendert damit
    nichts -- die Anfrage ginge an den Pfad von vorhin. Mit einem festen Pfad und einem
    Feld gibt es diese Falle nicht.
    """
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    values = {
        name: str(form.get(name, "")).strip()
        for name in ("point_id", "weekday", "time_of_day")
    }
    try:
        point_id = int(values["point_id"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    point = _point_or_404(session, zone, point_id)
    try:
        try:
            weekday = int(values["weekday"])
        except ValueError as exc:
            raise ScheduleError("weekday", "Bitte einen Wochentag auswählen.") from exc
        move_schedule_point(
            session,
            zone,
            point,
            weekday=weekday,
            minute=time_of_day_in_minutes(values["time_of_day"]),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        return _schedulepage(
            request, session, zone, principal, move_error=exc.notice
        )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


def _point_or_404(session: Session, zone: Zone, point_id: int) -> SchedulePoint:
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return point


@router.get("/zones/{zone_id}/schedule/points/{point_id}/delete")
async def schedule_point_delete_form(
    zone_id: int,
    point_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    point = _point_or_404(session, zone, point_id)
    return templates.TemplateResponse(
        request,
        "zeitplanpunkt_loeschen.html",
        {"zone": zone, "point": point, "wochentage": dict(WEEKDAYS)},
    )


@router.post("/zones/{zone_id}/schedule/points/{point_id}/delete")
async def remove_schedule_point(
    zone_id: int,
    point_id: int,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    point = _point_or_404(session, zone, point_id)
    delete_schedule_point(
        session,
        zone,
        point,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zones/{zone.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )


def _schedule_adopt_page(
    request: Request,
    session: Session,
    principal: Principal,
    zone: Zone,
    *,
    source_id: int | None = None,
    bestaetigung: bool = False,
    errors: str = "",
) -> Response:
    sources = [
        andere
        for andere in visible_zones(session, principal, "zone.read")
        if andere.id != zone.id
    ]
    return templates.TemplateResponse(
        request,
        "zeitplan_uebernehmen.html",
        {
            "zone": zone,
            "sources": sources,
            "source_id": source_id,
            "bestaetigung": bestaetigung,
            "errors": errors,
        },
    )


@router.get("/zones/{zone_id}/schedule/adopt")
async def schedule_adopt_form(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "schedule.manage")
    return _schedule_adopt_page(request, session, principal, zone)


@router.post("/zones/{zone_id}/schedule/adopt")
async def execute_schedule_adoption(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(aktueller_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    ziel = _zone_or_404(session, principal, zone_id, "schedule.manage")
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id", "")))
    except ValueError:
        return _schedule_adopt_page(
            request, session, principal, ziel, errors="Bitte eine Quellzone auswählen."
        )
    source = next(
        (
            zone
            for zone in visible_zones(session, principal, "zone.read")
            if zone.id == source_id and zone.id != ziel.id
        ),
        None,
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    hat_plan = bool(_points(session, ziel.id))
    if hat_plan and str(form.get("confirmed", "")) != "ja":
        return _schedule_adopt_page(
            request,
            session,
            principal,
            ziel,
            source_id=source.id,
            bestaetigung=True,
        )
    adopt_schedule(
        session,
        ziel,
        source,
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return RedirectResponse(
        f"/zones/{ziel.id}/schedule", status_code=status.HTTP_303_SEE_OTHER
    )
