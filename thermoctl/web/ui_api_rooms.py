"""Räume für die Svelte-Oberfläche: Lesemodell und Alltagsaktionen (`/ui-api/rooms`).

Dünner Adapter (Grundsatz 6): Zustand, aufgelöster Sollwert, Übersteuerung, Tagesverlauf
und Entscheidung kommen aus `web/shared/zone_status.py`, derselben Rechnung wie
zuvor die HTML-Startseite; geschrieben wird mit denselben Domänenfunktionen wie dort.
Hier steht keine Fachregel und nichts, was der Browser nachrechnen müsste:
Alter, Segmentgrenzen und Rechte kommen fertig.

Rechte werden in jedem Endpunkt neu geprüft. Eine Zone, die es nicht gibt, und eine,
die der Principal nicht mit `zone.read` sehen darf, antworten mit demselben 404 --
kein Hinweis auf Existenz. Erst danach kommt die Prüfung der Aktion (403).

Fehler sind immer `{"detail": ..., "code": ...}` (optional `"field"`). Temperaturen
sind Decimal-Strings, Zeitpunkte ISO-8601 mit Zeitzone (UTC).
"""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import OperatingMode, SensorStatus
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import change_log
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.fault import OK
from thermoctl.domain.modes import DomainError, step_setpoint, update_setpoints
from thermoctl.domain.outdoor import outdoor_reading
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    OverrideAlreadyRunning,
    ScheduleError,
    current_or_upcoming_vacation,
    end_of_next_switch,
    jump_to_next_switch,
    next_switch,
    resolved_setpoint,
)
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.domain.zones import UnknownOperatingMode, set_operating_mode
from thermoctl.services import cluster
from thermoctl.web.shared.home_notice import home_notice
from thermoctl.web.shared.zone_status import MINUTES_PER_DAY, zone_status_context


class RoomApiError(Exception):
    """Ein erwarteter Fehlschlag, der als `{detail, code}` beim Aufrufer ankommt."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        field: str | None = None,
        extra: dict[str, object] | None = None,
    ):
        super().__init__(detail)
        self.extra = extra
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.field = field


class _RoomsRoute(APIRoute):
    """Übersetzt `RoomApiError` und Schemafehler in den einheitlichen Fehlerkörper.

    Als Routenklasse und nicht als App-Handler: Die Übersetzung gilt damit genau für
    diesen Router, ohne die Fehlerform der übrigen Anwendung anzufassen. Die
    Transaktion ist beim Auslösen schon zurückgerollt (Ausnahme läuft durch
    `get_session`).
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RoomApiError as exc:
                content: dict[str, object] = {"detail": exc.detail, "code": exc.code}
                if exc.field is not None:
                    content["field"] = exc.field
                if exc.extra is not None:
                    content.update(exc.extra)
                return JSONResponse(content, status_code=exc.status_code)
            except RequestValidationError:
                return JSONResponse(
                    {"detail": "Die Eingabe ist ungültig.", "code": "validation"},
                    status_code=422,
                )

        return handler


router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)


# -- Ausgabe ----------------------------------------------------------------------------


class NamedOut(BaseModel):
    code: str
    label: str


class SensorOut(BaseModel):
    status_code: str
    status_label: str
    stuck: bool
    window_open: bool | None
    window_open_by_temperature: bool
    window_alarm: bool


class OutdoorOut(BaseModel):
    """Die anlagenweite Außentemperatur, nur lesend (Domäne `outdoor_reading`).

    `status` ist `ok`, `veraltet` oder `keine_quelle`; `temperature_c` ist nur bei `ok`
    eine Zahl, die man anzeigen darf -- ein veralteter Wert wird nie als Temperatur gezeigt.
    """

    status: str
    temperature_c: str | None
    measured_at: str | None


class SetpointOut(BaseModel):
    temperature_c: str
    reason: str
    mode_id: int | None
    mode_name: str | None


class OverrideOut(BaseModel):
    id: int
    temperature_c: str | None
    starts_at: str
    ends_at: str | None


class DecisionOut(BaseModel):
    """Die letzte Regelentscheidung -- eine Entscheidung, kein Schaltzustand."""

    would_heat: bool
    outcome_code: str
    reason: str
    decided_at: str
    planned_setpoint_c: str | None


class EmergencyOut(BaseModel):
    stage: str
    headline: str
    detail: str


class DaySegmentOut(BaseModel):
    start_minute: int
    end_minute: int
    mode_name: str
    temperature_c: str | None
    warmth: float


class ActionsOut(BaseModel):
    """Was dieser Principal an dieser Zone tatsächlich darf (Darstellung, kein Riegel)."""

    override: bool
    cancel_override: bool
    edit_setpoint_step: bool
    edit_mode_setpoints: bool
    jump_next: bool
    set_operating_mode: bool


class RoomOut(BaseModel):
    id: int
    name: str
    operating_mode: NamedOut
    temperature_c: str | None
    measured_at: str | None
    measured_age_seconds: int | None
    #: Der Messwert ist nicht mehr frisch (Zustand aus `domain.fault.sensor_state`, dieselbe
    #: Schwelle wie die Regelung). Für die Wohnungssicht: ein Effekt, keine Ursache.
    measurement_stale: bool
    sensor: SensorOut
    setpoint: SetpointOut
    override: OverrideOut | None
    decision: DecisionOut | None
    emergency: EmergencyOut | None
    plan_applies: bool
    day_track: list[DaySegmentOut]
    actions: ActionsOut


class ActuationOut(BaseModel):
    """Gespeichert, versendet und Rolle getrennt (wie die drei Riegel der Startseite).

    `control_armed` ist die gespeicherte Freigabe, `sending_allowed` der beim Start
    gebaute MQTT-Riegel. `state` fasst beides in die drei Texte der Startseite.
    """

    control_armed: bool
    sending_allowed: bool
    cluster_standby: bool
    cluster_state: Literal["leading", "standby", "determining"] = "leading"
    determining_until: str | None = None
    state: Literal["dry_run", "armed_restart_missing", "live"]


class RoomRefOut(BaseModel):
    id: int
    name: str


class VacationStatusOut(BaseModel):
    """Urlaub laufend oder geplant (Domäne `current_or_upcoming_vacation`)."""

    starts_at: str
    ends_at: str
    setback_temperature_c: str
    running: bool


class PlantOut(BaseModel):
    """Dauerhafte Anlagenhinweise der Startseite -- nur die Anlagensicht (Alt: `start.html`).

    Nur Lesewerte: `bridge_reachable` ist der zuletzt vom Dienst geprüfte Stand
    (`null`: noch nie geprüft, also weder erreichbar noch ausgefallen), die Räume sind
    ausschließlich die, die der Principal mit `zone.read` sieht.
    """

    bridge_reachable: bool | None
    silent_sensor_rooms: list[RoomRefOut]
    stuck_sensor_rooms: list[RoomRefOut]
    window_alarm_rooms: list[RoomRefOut]
    vacation: VacationStatusOut | None
    #: Leerzustand „Erste Zone anlegen“ nur mit anlagenweitem `zone.manage` (wie die Alt-Seite).
    may_create_zone: bool


class HomeNoticeOut(BaseModel):
    """Der Effekt eines Problems für die Wohnung, nicht die Ursache (`home_notice`)."""

    kind: Literal["sensor", "notbetrieb", "window"]
    zone_name: str
    stage_label: str | None
    detail: str | None
    measured_age_seconds: int | None


class HomeOut(BaseModel):
    """Wohnungssicht: `calm` genau dann, wenn kein Hinweis anliegt (Alt: „Heizung läuft normal“)."""

    calm: bool
    notice: HomeNoticeOut | None


class RoomsOut(BaseModel):
    timezone: str
    poll_interval_seconds: int
    now_fraction: float
    actuation: ActuationOut
    #: `null` in der Wohnungssicht: Anlagentechnik gehört nicht dorthin.
    plant: PlantOut | None
    #: `null` in der Anlagensicht.
    home: HomeOut | None
    #: `null` in der Wohnungssicht (zeigt keine Anlagentechnik) und vor der Einrichtung.
    outdoor: OutdoorOut | None
    rooms: list[RoomOut]


class ModeSetpointOut(BaseModel):
    mode_id: int
    code: str
    name: str
    temperature_c: str | None
    is_current: bool


class NextSwitchOut(BaseModel):
    at: str
    temperature_c: str
    reason: str
    mode_id: int | None
    mode_name: str | None


class VacationOut(BaseModel):
    starts_at: str
    ends_at: str
    setback_temperature_c: str
    running: bool


class RoomDetailOut(RoomOut):
    minimum_temperature_c: str
    maximum_temperature_c: str
    modes: list[ModeSetpointOut]
    operating_modes: list[NamedOut]
    next_switch: NextSwitchOut | None
    vacation: VacationOut | None


# -- Eingabe ----------------------------------------------------------------------------


class OverrideIn(BaseModel):
    temperature_c: str
    end: Literal["next_switch", "duration", "permanent"] = "permanent"
    duration_minutes: int | None = None


class ModeSetpointIn(BaseModel):
    temperature_c: str | None


class StepIn(BaseModel):
    mode_id: int
    direction: Literal["up", "down"]


class OperatingModeIn(BaseModel):
    code: str


class JumpNextIn(BaseModel):
    replace: bool = False


# -- Hilfen -----------------------------------------------------------------------------


def _iso(moment: datetime | None) -> str | None:
    """Naive UTC der Datenbank als ISO-8601 mit Zeitzone."""
    return moment.replace(tzinfo=UTC).isoformat() if moment is not None else None


def _dec(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Unbekannt und nicht sichtbar sind ununterscheidbar."""
    zone = next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id), None
    )
    if zone is None:
        raise RoomApiError(404, "Raum nicht gefunden.", "not_found")
    return zone


def _require(principal: Principal, permission: str, zone: Zone) -> None:
    if not has_permission(principal, permission, zone.id):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _actor(principal: Principal) -> change_log.Actor:
    return change_log.Actor(principal.user_id, principal.token_id, "web")


def _settings_or_error(session: Session) -> Setting:
    settings = session.get(Setting, 1)
    if settings is None:
        raise RoomApiError(503, "Die Einrichtung ist unvollständig.", "setup_incomplete")
    return settings


def _actions(principal: Principal, zone: Zone) -> ActionsOut:
    return ActionsOut(
        override=has_permission(principal, "override.create", zone.id),
        cancel_override=has_permission(principal, "override.cancel", zone.id),
        edit_setpoint_step=has_permission(principal, "setpoint.write", zone.id),
        edit_mode_setpoints=has_permission(principal, "setpoint.write", zone.id),
        jump_next=has_permission(principal, "override.create", zone.id),
        set_operating_mode=has_permission(principal, "zone.manage", zone.id),
    )


def _rooms(
    session: Session, principal: Principal, zones: list[Zone], now: datetime, settings: Setting
) -> tuple[list[RoomOut], dict[str, object]]:
    context = zone_status_context(session, principal, zones, now, settings)
    states = cast("dict[int, tuple[ZoneState, SensorStatus]]", context["states"])
    setpoints = cast("dict[int, Any]", context["setpoints"])
    overrides = cast("dict[int, ZoneOverride]", context["overrides"])
    decisions = cast("dict[int, ShadowDecision]", context["decisions"])
    banners = cast("dict[int, Any]", context["emergency_banners"])
    tracks = cast("dict[int, list[dict[str, Any]]]", context["day_tracks"])
    mode_names = cast("dict[int, str]", context["mode_names"])

    rooms: list[RoomOut] = []
    for zone in zones:
        entry = states.get(zone.id)
        state, status = entry if entry is not None else (None, None)
        setpoint = setpoints[zone.id]
        override = overrides.get(zone.id)
        decision = decisions.get(zone.id)
        banner = banners.get(zone.id)
        measured = state.measured_at if state is not None else None
        rooms.append(
            RoomOut(
                id=zone.id,
                name=zone.display_name,
                operating_mode=NamedOut(
                    code=zone.operating_mode.code, label=zone.operating_mode.label
                ),
                temperature_c=_dec(state.temperature_c) if state is not None else None,
                measured_at=_iso(measured),
                measured_age_seconds=(
                    max(0, int((now - measured).total_seconds())) if measured is not None else None
                ),
                measurement_stale=status is not None and status.code != "ok",
                sensor=SensorOut(
                    status_code=status.code if status is not None else "unknown",
                    status_label=status.label if status is not None else "unbekannt",
                    stuck=bool(state and state.sensor_stuck),
                    window_open=state.window_open if state is not None else None,
                    window_open_by_temperature=bool(state and state.window_open_by_temperature),
                    window_alarm=bool(state and state.window_alarm),
                ),
                setpoint=SetpointOut(
                    temperature_c=format(setpoint.temperature_c, "f"),
                    reason=setpoint.reason,
                    mode_id=setpoint.mode_id,
                    mode_name=(
                        mode_names.get(setpoint.mode_id) if setpoint.mode_id is not None else None
                    ),
                ),
                override=(
                    OverrideOut(
                        id=override.id,
                        temperature_c=_dec(override.temperature_c),
                        starts_at=cast("str", _iso(override.starts_at)),
                        ends_at=_iso(override.ends_at),
                    )
                    if override is not None
                    else None
                ),
                decision=(
                    DecisionOut(
                        would_heat=decision.would_heat,
                        outcome_code=decision.outcome_code,
                        reason=decision.reason,
                        decided_at=cast("str", _iso(decision.decided_at)),
                        planned_setpoint_c=_dec(decision.scheduled_setpoint_c),
                    )
                    if decision is not None
                    else None
                ),
                emergency=(
                    EmergencyOut(stage=banner.stage, headline=banner.headline, detail=banner.detail)
                    if banner is not None
                    else None
                ),
                plan_applies=zone.operating_mode.code == "auto" and override is None,
                day_track=[
                    DaySegmentOut(
                        start_minute=int(segment["start"]),
                        end_minute=round(
                            (float(segment["left"]) + float(segment["width"]))
                            * MINUTES_PER_DAY
                            / 100
                        ),
                        mode_name=str(segment["mode_name"]),
                        temperature_c=_dec(segment["temperature"]),
                        warmth=float(segment["warmth"]),
                    )
                    for segment in tracks.get(zone.id, [])
                ],
                actions=_actions(principal, zone),
            )
        )
    return rooms, context


def _outdoor(
    session: Session, principal: Principal, settings: Setting, now: datetime
) -> OutdoorOut | None:
    """Außentemperatur für die Anlagensicht; die Wohnungssicht bekommt sie nicht (wie bisher)."""
    if principal.ui_profile is not WebUiProfile.ADMIN:
        return None
    reading = outdoor_reading(session, settings, now)
    fresh = reading.status == OK and reading.temperature_c is not None
    return OutdoorOut(
        status=reading.status,
        temperature_c=_dec(reading.temperature_c) if fresh else None,
        measured_at=_iso(reading.measured_at),
    )


def _actuation(request: Request, session: Session, settings: Setting) -> ActuationOut:
    armed = bool(settings.control_armed)
    sending = bool(getattr(request.app.state, "sending_allowed", False))
    state: Literal["dry_run", "armed_restart_missing", "live"]
    if armed and sending:
        state = "live"
    elif armed:
        state = "armed_restart_missing"
    else:
        state = "dry_run"
    leading = cluster.is_leader(session, holder=cluster.instance_id())
    cluster_state: Literal["leading", "standby", "determining"] = "leading"
    determining_until: datetime | None = None
    if not leading:
        cluster_state = "standby"
        started = getattr(request.app.state, "process_started_at", None)
        if started is not None:
            until = started + timedelta(seconds=cluster.takeover_timeout_seconds(settings))
            if utcnow() < until:
                cluster_state = "determining"
                determining_until = until
    return ActuationOut(
        control_armed=armed,
        sending_allowed=sending,
        cluster_standby=not leading,
        cluster_state=cluster_state,
        determining_until=_iso(determining_until),
        state=state,
    )


def _detail(
    session: Session, principal: Principal, zone: Zone, now: datetime
) -> RoomDetailOut:
    settings = _settings_or_error(session)
    rooms, context = _rooms(session, principal, [zone], now, settings)
    room = rooms[0]
    stored = {
        row.setpoint_mode_id: row.temperature_c
        for row in session.scalars(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id))
    }
    modes = list(
        session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name))
    )
    upcoming = next_switch(session, zone, now)
    mode_names = {mode.id: mode.name for mode in modes}
    vacation = current_or_upcoming_vacation(session, now)
    return RoomDetailOut(
        **room.model_dump(),
        minimum_temperature_c=format(cast("Decimal", context["minimum_temperature"]), "f"),
        maximum_temperature_c=format(cast("Decimal", context["maximum_temperature"]), "f"),
        modes=[
            ModeSetpointOut(
                mode_id=mode.id,
                code=mode.code,
                name=mode.name,
                temperature_c=_dec(stored.get(mode.id)),
                is_current=mode.id == room.setpoint.mode_id,
            )
            for mode in modes
        ],
        operating_modes=[
            NamedOut(code=kind.code, label=kind.label)
            for kind in session.scalars(select(OperatingMode).order_by(OperatingMode.id))
        ],
        next_switch=(
            NextSwitchOut(
                at=cast("str", _iso(upcoming.at)),
                temperature_c=format(upcoming.setpoint.temperature_c, "f"),
                reason=upcoming.setpoint.reason,
                mode_id=upcoming.setpoint.mode_id,
                mode_name=(
                    mode_names.get(upcoming.setpoint.mode_id)
                    if upcoming.setpoint.mode_id is not None
                    else None
                ),
            )
            if upcoming is not None
            else None
        ),
        vacation=(
            VacationOut(
                starts_at=cast("str", _iso(vacation.starts_at)),
                ends_at=cast("str", _iso(vacation.ends_at)),
                setback_temperature_c=format(vacation.setback_temperature_c, "f"),
                running=vacation.starts_at <= now,
            )
            if vacation is not None
            else None
        ),
    )


def _temperature(text: str) -> Decimal:
    try:
        return Decimal(text.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise RoomApiError(
            422, "Bitte eine gültige Temperatur angeben.", "validation", "temperature_c"
        ) from exc


def _domain_error(exc: DomainError) -> RoomApiError:
    return RoomApiError(422, exc.notice, "validation", exc.field)


def _refs(zones: list[Zone], include: Callable[[Zone], bool]) -> list[RoomRefOut]:
    return [RoomRefOut(id=zone.id, name=zone.display_name) for zone in zones if include(zone)]


def _plant(
    request: Request,
    session: Session,
    principal: Principal,
    zones: list[Zone],
    states: dict[int, tuple[ZoneState, SensorStatus]],
    now: datetime,
) -> PlantOut:
    """Die Quellen der Übersicht, nur lesend: `app.state`, Zustände, Urlaub."""
    vacation = current_or_upcoming_vacation(session, now)
    return PlantOut(
        bridge_reachable=getattr(request.app.state, "bridge_reachable", None),
        silent_sensor_rooms=_refs(
            zones, lambda z: z.id in states and states[z.id][1].code != "ok"
        ),
        stuck_sensor_rooms=_refs(
            zones, lambda z: z.id in states and bool(states[z.id][0].sensor_stuck)
        ),
        window_alarm_rooms=_refs(
            zones, lambda z: z.id in states and bool(states[z.id][0].window_alarm)
        ),
        vacation=(
            VacationStatusOut(
                starts_at=cast("str", _iso(vacation.starts_at)),
                ends_at=cast("str", _iso(vacation.ends_at)),
                setback_temperature_c=format(vacation.setback_temperature_c, "f"),
                running=vacation.starts_at <= now,
            )
            if vacation is not None
            else None
        ),
        may_create_zone=has_permission(principal, "zone.manage"),
    )


def _home(
    zones: list[Zone], context: dict[str, object], now: datetime
) -> HomeOut:
    """Dieselbe Entscheidung wie die Wohnungs-Startseite (`shared.home_notice.home_notice`)."""
    notice = home_notice(
        zones,
        cast("dict[int, tuple[ZoneState, SensorStatus]]", context["states"]),
        cast("dict[int, Any]", context["emergency_banners"]),
    )
    if notice is None:
        return HomeOut(calm=True, notice=None)
    measured = cast("datetime | None", notice.get("measured_at"))
    return HomeOut(
        calm=False,
        notice=HomeNoticeOut(
            kind=cast("Literal['sensor', 'notbetrieb', 'window']", notice["kind"]),
            zone_name=cast("str", notice["zone_name"]),
            stage_label=cast("str | None", notice.get("stage_label")),
            detail=cast("str | None", notice.get("detail")),
            measured_age_seconds=(
                max(0, int((now - measured).total_seconds())) if measured is not None else None
            ),
        ),
    )


# -- Lesen ------------------------------------------------------------------------------


@router.get("/rooms")
def list_rooms(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomsOut:
    zones = visible_zones(session, principal, "zone.read")
    settings = session.get(Setting, 1)
    if settings is None:
        if zones:
            raise RoomApiError(503, "Die Einrichtung ist unvollständig.", "setup_incomplete")
        return RoomsOut(
            timezone="UTC",
            poll_interval_seconds=60,
            now_fraction=0.0,
            actuation=ActuationOut(
                control_armed=False,
                sending_allowed=False,
                cluster_standby=False,
                state="dry_run",
            ),
            plant=(
                _plant(request, session, principal, [], {}, utcnow())
                if principal.ui_profile is WebUiProfile.ADMIN
                else None
            ),
            home=(
                HomeOut(calm=True, notice=None)
                if principal.ui_profile is WebUiProfile.TENANT
                else None
            ),
            outdoor=None,
            rooms=[],
        )
    now = utcnow()
    rooms, context = _rooms(session, principal, zones, now, settings)
    admin_ui = principal.ui_profile is WebUiProfile.ADMIN
    return RoomsOut(
        timezone=cast("str", context["timezone"]),
        poll_interval_seconds=cast("int", context["poll_interval_seconds"]),
        now_fraction=cast("float", context["now_fraction"]),
        actuation=_actuation(request, session, settings),
        plant=(
            _plant(
                request,
                session,
                principal,
                zones,
                cast("dict[int, tuple[ZoneState, SensorStatus]]", context["states"]),
                now,
            )
            if admin_ui
            else None
        ),
        home=_home(zones, context, now) if not admin_ui else None,
        outdoor=_outdoor(session, principal, settings, now),
        rooms=rooms,
    )


@router.get("/rooms/{zone_id}")
def show_room(
    zone_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    zone = _visible_zone(session, principal, zone_id)
    return _detail(session, principal, zone, utcnow())


# -- Schreiben --------------------------------------------------------------------------


@router.post("/rooms/{zone_id}/override")
def create_room_override(
    zone_id: int,
    body: OverrideIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    """Übersteuerung anlegen, mit Protokolleintrag wie bei MCP."""
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "override.create", zone)
    temperature = _temperature(body.temperature_c)
    now = utcnow()
    end_at: datetime | None
    if body.end == "next_switch":
        end_at = end_of_next_switch(session, zone, now)
        if end_at is None:
            raise RoomApiError(
                409, "Für diesen Raum ist kein Zeitplan hinterlegt.", "no_schedule", "end"
            )
    elif body.end == "duration":
        if body.duration_minutes is None or body.duration_minutes <= 0:
            raise RoomApiError(
                422, "Bitte eine Dauer in Minuten angeben.", "validation", "duration_minutes"
            )
        end_at = now + timedelta(minutes=body.duration_minutes)
    else:
        end_at = None
    try:
        change_log.create_override(session, _actor(principal), zone, temperature, end_at, now=now)
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return _detail(session, principal, zone, now)


@router.post("/rooms/{zone_id}/override/cancel")
def cancel_room_override(
    zone_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "override.cancel", zone)
    recorded = change_log.cancel_override(session, _actor(principal), zone)
    if recorded.result is None:
        raise RoomApiError(409, "Für diesen Raum läuft keine Übersteuerung.", "no_override")
    return _detail(session, principal, zone, utcnow())


@router.put("/rooms/{zone_id}/modes/{mode_id}/setpoint")
def set_mode_setpoint(
    zone_id: int,
    mode_id: int,
    body: ModeSetpointIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    """Ein Modus-Sollwert wie im Formular `/zones/{id}/setpoints` (nur Anlagensicht);
    `null` löscht den Sollwert dieses Modus."""
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "setpoint.write", zone)
    if session.get(SetpointMode, mode_id) is None:
        raise RoomApiError(404, "Modus nicht gefunden.", "mode_not_found")
    value = _temperature(body.temperature_c) if body.temperature_c is not None else None
    try:
        update_setpoints(session, zone, {mode_id: value}, user_id=principal.user_id)
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return _detail(session, principal, zone, utcnow())


@router.post("/rooms/{zone_id}/setpoint-step")
def step_room_setpoint(
    zone_id: int,
    body: StepIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    """Der Thermostat-Schritt (ein halbes Grad) auf den Sollwert des Modus, wie
    dem früheren `adjust_thermostat`; kein Übersteuern."""
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "setpoint.write", zone)
    now = utcnow()
    _settings_or_error(session)
    shown = resolved_setpoint(session, zone, now)
    fallback = shown.temperature_c if shown.mode_id == body.mode_id else None
    try:
        step_setpoint(
            session,
            zone,
            body.mode_id,
            1 if body.direction == "up" else -1,
            fallback=fallback,
            user_id=principal.user_id,
        )
    except DomainError as exc:
        if exc.field == "mode_id":
            raise RoomApiError(
                404, "Für diesen Modus gibt es keinen Sollwert.", "mode_not_found"
            ) from exc
        raise RoomApiError(409, exc.notice, "limit_reached", exc.field) from exc
    return _detail(session, principal, zone, now)


@router.put("/rooms/{zone_id}/operating-mode")
def change_operating_mode(
    zone_id: int,
    body: OperatingModeIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    """Betriebsart wechseln (`domain.zones.set_operating_mode`). In der früheren HTML-Oberfläche
    geht das nur im Raumformular (`zone.manage`, Anlagensicht); hier dieselben Bedingungen."""
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "zone.manage", zone)
    try:
        set_operating_mode(session, zone, body.code, actor_id=principal.user_id)
    except UnknownOperatingMode as exc:
        raise RoomApiError(422, str(exc), "validation", "code") from exc
    return _detail(session, principal, zone, utcnow())


@router.post("/rooms/{zone_id}/jump-next")
def jump_room_to_next_switch(
    zone_id: int,
    body: JumpNextIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RoomDetailOut:
    """Nächste Phase vorziehen (`domain.schedule.jump_to_next_switch`)."""
    zone = _visible_zone(session, principal, zone_id)
    _require(principal, "override.create", zone)
    now = utcnow()
    _settings_or_error(session)
    try:
        override = jump_to_next_switch(
            session,
            zone,
            now=now,
            replace=body.replace,
            user_id=principal.user_id,
            token_id=principal.token_id,
            source="web",
        )
    except OverrideAlreadyRunning as exc:
        raise RoomApiError(409, exc.notice, "override_running", exc.field) from exc
    except ScheduleError as exc:
        raise RoomApiError(409, exc.notice, "no_schedule", exc.field) from exc
    audit.record(
        session, source="web", action="override.jump_next", object_type="zone",
        object_id=str(zone.id),
        summary=f"Nächste Phase in '{zone.display_name}' vorgezogen",
        user_id=principal.user_id, token_id=principal.token_id,
        detail=(
            f"{override.temperature_c} °C bis "
            f"{_iso(override.ends_at) or 'unbefristet'}"
            + ("; laufende Übersteuerung ersetzt" if body.replace else "")
        ),
    )
    return _detail(session, principal, zone, now)
