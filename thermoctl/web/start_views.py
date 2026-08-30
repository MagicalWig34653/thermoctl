"""Die Startseite: eine Statustafel, keine Kennzahlensammlung.

Sie beantwortet genau eine Frage -- *tut das Haus gerade das, was ich ihm gesagt habe?*
-- und zwar fuer alle Zonen auf einmal. Alles, was diese Frage nicht beantwortet, gehoert
woandershin.

Frueher standen hier zwei Zaehlkacheln: die Zahl der Zonen und die Zahl der **Benutzer**.
Wie viele Konten es gibt, sagt ueber eine Heizung nichts; die Zahl stand da, weil sie
leicht zu ermitteln war. Beide sind weg.

Anders als die geschuetzten Verwaltungsseiten antwortet die Seite einem nicht angemeldeten
Besucher nicht mit 401, sondern leitet auf die Anmeldung weiter: Wer die Adresse des
Dienstes im Browser eingibt, soll ein Anmeldeformular sehen und keine Fehlermeldung.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from thermoctl.auth.dependencies import csrf_schutz, get_session
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint
from thermoctl.domain.authz import has_permission, principal_for_user, visible_zones
from thermoctl.domain.modes import MAXIMUM_TEMPERATURE_C, MINIMUM_TEMPERATURE_C
from thermoctl.domain.schedule import resolved_setpoint, week_segments
from thermoctl.setup import einrichtung_noetig
from thermoctl.web import templates, waermeanteil

# `include_in_schema=False`: Die OpenAPI-Beschreibung ist der Vertrag der
# REST-Schnittstelle. Diese Wege liefern HTML fuer Menschen, und in der Oberflaeche
# unter /docs stuende sonst neben jedem echten Endpunkt ein Formularweg, dessen
# 'Try it out' eine echte Aenderung ausloest.
router = APIRouter(dependencies=[Depends(csrf_schutz)], include_in_schema=False)


MINUTES_PER_DAY = 1440


def _day_track(
    session: Session, zone_ids: list[int], weekday: int
) -> dict[int, list[dict[str, object]]]:
    """Der heutige Zeitplan je Zone als Abschnitte mit Anteil, Zeit und Solltemperatur.

    Dieselbe Zerlegung wie die Wochenansicht (`wochenabschnitte`), nur auf einen Tag
    beschraenkt -- eine zweite Fassung derselben Logik im Browser waere genau das, was
    Grundsatz 6 verbietet.
    """
    if not zone_ids:
        return {}
    modes = {m.id: m for m in session.scalars(select(SetpointMode))}
    namen = {identifier: mode.name for identifier, mode in modes.items()}
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

    spuren: dict[int, list[dict[str, object]]] = {}
    for zone_id, points in points_per_zone.items():
        segments = [
            a for a in week_segments(points, namen) if a.weekday == weekday
        ]
        spuren[zone_id] = [
            {
                "start": segment.start_minute,
                "breite": (segment.endminute - segment.start_minute)
                * 100
                / MINUTES_PER_DAY,
                "links": segment.start_minute * 100 / MINUTES_PER_DAY,
                "modusname": segment.mode_name,
                "temperatur": temperatures.get((zone_id, segment.mode_id)),
                "waerme": waermeanteil(temperatures.get((zone_id, segment.mode_id))),
            }
            for segment in segments
        ]
    return spuren


@router.get("/")
def start(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    # Vor der Anmeldung: Solange es keinen einzigen Benutzer gibt, kann sich niemand
    # anmelden. Ein Anmeldeformular waere dann eine Sackgasse -- wer die Adresse des
    # Dienstes eingibt, gehoert zur Einrichtung. Dass die Weiterleitung den leeren
    # Zustand verraet, ist kein Zugewinn fuer einen Angreifer: /setup antwortet ohnehin
    # sichtbar anders als nach abgeschlossener Einrichtung, und die Einrichtung selbst
    # haengt am Einmal-Token aus dem Log, nicht an der Erreichbarkeit der Seite.
    if einrichtung_noetig(session):
        return RedirectResponse("/setup", status_code=303)

    cookie_value = request.cookies.get(COOKIE_NAME)
    http_session = resolve_session(session, cookie_value) if cookie_value else None
    user = session.get(User, http_session.user_id) if http_session else None
    if user is None or not user.is_active:
        return RedirectResponse("/login", status_code=303)

    request.state.user = user
    principal = principal_for_user(session, user)
    zones = visible_zones(session, principal, "zone.read")
    now = utcnow()
    settings = session.get(Setting, 1)
    zustaende = {
        zone_id: (state, sensorstatus)
        for zone_id, state, sensorstatus in session.execute(
            select(ZoneState.zone_id, ZoneState, SensorStatus)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    zone_ids = [zone.id for zone in zones]
    overrides: dict[int, ZoneOverride] = {}
    for entry in session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id.in_(zone_ids),
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
            or_(ZoneOverride.ends_at.is_(None), ZoneOverride.ends_at > now),
        )
        .order_by(ZoneOverride.created_at.desc())
    ):
        overrides.setdefault(entry.zone_id, entry)
    entscheidungen: dict[int, ShadowDecision] = {}
    for entscheidung in session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id.in_(zone_ids))
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
    ):
        entscheidungen.setdefault(entscheidung.zone_id, entscheidung)

    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "user": user,
            "zones": zones,
            "zustaende": zustaende,
            "setpoints": {
                zone.id: resolved_setpoint(session, zone, now) for zone in zones
            },
            "overrides": overrides,
            "entscheidungen": entscheidungen,
            "darf_uebersteuern": {
                zone.id
                for zone in zones
                if has_permission(principal, "override.create", zone.id)
            },
            "darf_aufheben": {
                zone.id
                for zone in zones
                if has_permission(principal, "override.cancel", zone.id)
            },
            "darf_sollwert": {
                zone.id
                for zone in zones
                if has_permission(principal, "setpoint.write", zone.id)
            },
            "thermostatfehler": request.query_params.get("thermostatfehler"),
            # Aus der Domaene: Ein `min="5"` im Markup waere eine zweite Fassung der
            # Grenze und wuerde beim naechsten Verschieben zurueckbleiben.
            "mindesttemperatur": MINIMUM_TEMPERATURE_C,
            "hoechsttemperatur": MAXIMUM_TEMPERATURE_C,
            # Der Anzeigename, nicht der Code: Am Thermostat stand "frostschutz" statt
            # "Frostschutz" -- ein Bezeichner aus der Datenbank, der dort nichts zu
            # suchen hat.
            "mode_names": {
                identifier: name
                for identifier, name in session.execute(
                    select(SetpointMode.id, SetpointMode.name)
                )
            },
            "darf_parameter": {
                zone.id for zone in zones if has_permission(principal, "zone.manage", zone.id)
            },
            "uebersteuerungsfehler": request.query_params.get("uebersteuerungsfehler"),
            "fehler_zone_id": request.query_params.get("zone_id"),
            "uebersteuerungswerte": request.query_params,
            # Die Anlage in einem Satz: Schaltet sie wirklich, laeuft die Bruecke, und
            # gibt es Sensoren, die schweigen? Genau die drei Dinge, die eine Anzeige
            # unglaubwuerdig machen, wenn man sie nicht kennt.
            "armed": bool(settings and settings.control_armed),
            "bridge": getattr(request.app.state, "bridge_reachable", None),
            "stumme_sensoren": [
                zone.display_name
                for zone in zones
                if zone.id in zustaende and zustaende[zone.id][1].code != "ok"
            ],
            "tagesspuren": _day_track(
                session, zone_ids, now.isoweekday()
            ),
            "jetzt_anteil": (now.hour * 60 + now.minute) * 100 / MINUTES_PER_DAY,
        },
    )
