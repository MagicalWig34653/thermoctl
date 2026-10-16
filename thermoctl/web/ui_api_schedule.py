"""Zeitplan, Vorschau, Urlaub, Abwesenheit und Modi für die Svelte-Oberfläche (`/ui-api`).

Dünner Adapter (Grundsatz 6): Der Wochenplan-Editor im Browser schickt fachliche
**Absichten** (Bereich setzen, Punkt anlegen/verschieben/löschen, Tag oder Woche
kopieren, Rücknahme). Jede Regel -- Überschneidung, Wertebereiche, Normalform des
Plans, Sommerzeit -- sitzt in `domain.schedule`; hier wird nichts nachgerechnet und der
Browser braucht keine Zeitplan-Validierung. Zeiten sind Minuten der **lokalen** Woche
(Wochentag 1 = Montag, `minute_of_day` 0..1439), wie in der Datenbank; ISO-Zeitpunkte
(Vorschau, Urlaub) sind UTC mit Zeitzone, dazu der lokale Zeitpunkt der Anlage.

Konflikterkennung: Jeder Schreibaufruf auf einen Zeitplan trägt die `version`, die das
Lesen geliefert hat (die Id des jüngsten Audit-Ereignisses dieses Plans -- dieselbe
Größe, an der die Rücknahme der HTML-Ansicht ihre Veraltung erkennt). Passt sie nicht
mehr, antwortet der Server `409 version_conflict` und ändert nichts. Das ist eine
Prüfung vor dem Schreiben, keine Sperre: zwei exakt gleichzeitige Anfragen derselben
Version kann sie nicht trennen (wie die HTML-Rücknahme auch nicht).

Rechte und Sichtbarkeit wie `ui_api_rooms`: unbekannte und nicht lesbare Zonen
antworten gleich (404), erst danach kommt die Aktion (403). Profilregeln wie die
HTML-Views: der vollständige Editor (Bereich/Punkte), Urlaub und Modi nur in der
Anlagensicht, die Abwesenheit nur in der Wohnungssicht; Tag kopieren, Plan kopieren und
Rücknahme stehen beiden Profilen offen (die Mieteransicht hat sie auch).

Block-Operationen (`/block/move|duplicate|resize|delete`): ein Block ist der Lauf von
einem Schaltpunkt bis zum nächsten (er darf über Mitternacht und die Wochennaht reichen,
`end_minute` zählt dann über 1440 hinaus, höchstens eine Woche). Der Plan ist ein Ring ohne
Lücken -- „frei" heißt deshalb: im Zielbereich beginnt kein anderer Block (er liegt im
Inneren eines einzelnen Blocks, den er teilt), sonst `409 overlap`. Was ein Block
hinterlässt, erbt den Modus davor (der vorherige Block läuft weiter; ohne anderen Block
gilt der Frostschutzmodus). Alles in einer Transaktion: jeder Fehler lässt den Plan
unverändert.

Mehrstufige Rücknahme: Die Rücknahme-Marke trägt optional die Vorgänger-Stände
(`history`). Schreibaufrufe nehmen die zuletzt erhaltene Marke als `previous_undo_token`
entgegen und verlängern damit die Kette (passt sie nicht zum aktuellen Stand, beginnt eine
neue); `undo` antwortet mit der Marke für den nächsten Schritt zurück.

Fehler sind immer `{"detail": ..., "code": ...}` (optional `"field"`).
"""

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import change_log
from thermoctl.domain.absence import (
    absence_zones,
    end_absence,
    running_absence,
    running_absences,
    start_absence,
)
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.modes import (
    DomainError,
    create_mode,
    delete_guard,
    delete_mode,
    update_mode,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    ScheduleError,
    ScheduleGesture,
    ScheduleSnapshot,
    _current_schedule_revision,
    adopt_schedule,
    change_schedule_point_mode,
    copy_schedule_day,
    create_schedule_point,
    current_or_upcoming_vacation,
    delete_schedule_block,
    delete_schedule_point,
    duplicate_schedule_block,
    move_schedule_block,
    paint_schedule_interval,
    resize_schedule_block,
    schedule_forecast,
    schedule_snapshot,
    undo_schedule_gesture,
    week_segments,
)
from thermoctl.domain.time import local_day_start_utc, local_time
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web import warmth_fraction
from thermoctl.web.schedule_views import _undo_payload, _undo_token
from thermoctl.web.ui_api_rooms import (
    RoomApiError,
    _actor,
    _dec,
    _domain_error,
    _iso,
    _RoomsRoute,
    _temperature,
)

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

Sess = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]


# -- Ausgabe ----------------------------------------------------------------------------


class PointOut(BaseModel):
    id: int
    weekday: int
    minute_of_day: int
    mode_id: int


class SegmentOut(BaseModel):
    """Ein Balken der Woche, an Tagesgrenzen geschnitten (`week_segments`).

    `point_id` ist der Schaltpunkt, der den Balken beginnt -- `null` für den Rest, der
    vom Vortag herüberreicht (der gehört einem Punkt des Vortags und ist nicht
    ziehbar)."""

    weekday: int
    start_minute: int
    end_minute: int
    mode_id: int
    mode_name: str
    point_id: int | None
    temperature_c: str | None
    warmth: float


class PlanModeOut(BaseModel):
    id: int
    code: str
    name: str
    temperature_c: str | None
    warmth: float


class CopyTargetOut(BaseModel):
    """Ein Raum, in den der Principal schreiben darf (`schedule.manage`)."""

    id: int
    name: str
    version: str
    has_plan: bool


class ScheduleOut(BaseModel):
    zone_id: int
    zone_name: str
    timezone: str
    version: str
    points: list[PointOut]
    segments: list[SegmentOut]
    modes: list[PlanModeOut]
    can_edit: bool
    full_editor: bool
    copy_targets: list[CopyTargetOut]


class ScheduleChangeOut(ScheduleOut):
    """Antwort auf eine Schreibabsicht: der neue Stand samt Rücknahme-Marke.

    `changed` ist `false`, wenn die Absicht nichts verändert hätte (der Plan sah schon so
    aus); dann gibt es keine Rücknahme. `undo_token` ist dieselbe signierte Marke wie in
    der HTML-Ansicht und gilt nur, bis jemand den Plan erneut ändert."""

    changed: bool
    undo_token: str | None


class ForecastSegmentOut(BaseModel):
    starts_at: str
    ends_at: str
    local_starts_at: str
    duration_minutes: int
    temperature_c: str
    reason: str
    mode_id: int | None
    mode_name: str | None
    warmth: float


class PreviewOut(BaseModel):
    zone_id: int
    timezone: str
    generated_at: str
    horizon_hours: int
    segments: list[ForecastSegmentOut]


class VacationOut(BaseModel):
    id: int
    starts_at: str
    ends_at: str
    start_date: str
    end_date: str
    setback_temperature_c: str
    running: bool


class VacationStateOut(BaseModel):
    timezone: str
    can_manage: bool
    vacation: VacationOut | None


class AbsenceInfoOut(BaseModel):
    id: int
    starts_at: str
    ends_at: str
    setback_temperature_c: str
    zones: list[CopyTargetOut]


class AbsenceStateOut(BaseModel):
    timezone: str
    can_start: bool
    eligible_zone_ids: list[int]
    absence: AbsenceInfoOut | None


class ModeAdminOut(BaseModel):
    id: int
    code: str
    name: str
    sort_order: int
    is_builtin: bool
    delete_lock: str | None


class ModesOut(BaseModel):
    modes: list[ModeAdminOut]


# -- Eingabe ----------------------------------------------------------------------------


class PaintIn(BaseModel):
    version: str
    weekday: int
    start_minute: int
    end_minute: int
    mode_id: int
    previous_undo_token: str | None = None


class PointIn(BaseModel):
    version: str
    weekday: int
    minute_of_day: int
    mode_id: int
    previous_undo_token: str | None = None


class PointMoveIn(BaseModel):
    version: str
    weekday: int
    minute_of_day: int
    previous_undo_token: str | None = None


class PointModeIn(BaseModel):
    version: str
    mode_id: int
    previous_undo_token: str | None = None


class CopyDayIn(BaseModel):
    version: str
    weekday: int
    scope: Literal["workdays", "all"]
    previous_undo_token: str | None = None


class CopyTargetIn(BaseModel):
    zone_id: int
    version: str


class CopyWeekIn(BaseModel):
    version: str
    targets: list[CopyTargetIn]


class UndoIn(BaseModel):
    undo_token: str


class BlockIn(BaseModel):
    """Der Block, wie ihn der Plan zeigt: Wochentag, Beginn und Ende in Minuten ab dessen
    Mitternacht (`end_minute` > 1440 über Mitternacht hinaus)."""

    version: str
    weekday: int
    start_minute: int
    end_minute: int
    previous_undo_token: str | None = None


class BlockTargetIn(BlockIn):
    to_weekday: int
    to_start_minute: int


class BlockResizeIn(BlockIn):
    new_start_minute: int
    new_end_minute: int
    adjacent: bool = False


class VacationIn(BaseModel):
    start_date: str
    end_date: str
    setback_temperature_c: str


class AbsenceIn(BaseModel):
    temperature_c: str
    return_on: str


class ModeIn(BaseModel):
    code: str
    name: str
    sort_order: int = 0


# -- Hilfen: Rechte und Zonen -----------------------------------------------------------


def _visible(session: Session, principal: Principal, zone_id: int) -> Zone:
    """Unbekannt und nicht lesbar sind ununterscheidbar."""
    zone = next(
        (z for z in visible_zones(session, principal, "zone.read") if z.id == zone_id), None
    )
    if zone is None:
        raise RoomApiError(404, "Raum nicht gefunden.", "not_found")
    return zone


def _need(principal: Principal, permission: str, zone: Zone | None = None) -> None:
    if not has_permission(principal, permission, zone.id if zone is not None else None):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _need_profile(principal: Principal, profile: WebUiProfile) -> None:
    if principal.ui_profile is not profile:
        raise RoomApiError(403, "Diese Aktion gehört nicht zu Ihrer Oberfläche.", "wrong_profile")


def _editable_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = _visible(session, principal, zone_id)
    _need(principal, "schedule.manage", zone)
    return zone


def _settings(session: Session) -> Setting:
    settings = session.get(Setting, 1)
    if settings is None:
        raise RoomApiError(503, "Die Einrichtung ist unvollständig.", "setup_incomplete")
    return settings


def _schedule_error(exc: ScheduleError) -> RoomApiError:
    return RoomApiError(422, exc.notice, "validation", exc.field)


# -- Hilfen: Zeitplan -------------------------------------------------------------------


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
        session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order, SetpointMode.name))
    )


def _version(session: Session, zone_id: int) -> str:
    """Die Id des jüngsten Audit-Ereignisses dieses Plans; `0` für einen nie geänderten."""
    return str(_current_schedule_revision(session, zone_id) or 0)


_RECORD_CHANGED = 1020  # MariaDB/MySQL: "Record has changed since last read in table ..."


def _lock_zone(session: Session, zone_id: int) -> bool:
    """Sperrt die Zonenzeile und prüft, dass der Plan seit dem Anfang der Transaktion
    unverändert blieb; `False`, wenn eine gleichzeitige Geste dazwischen festgeschrieben hat.

    Unter MariaDB (REPEATABLE READ) liest eine gewöhnliche Abfrage den Stand vom Beginn der
    Transaktion; Versionsprüfung und Schreiben allein wären nicht atomar, zwei Gesten mit
    derselben Version liefen beide durch. Deshalb: erst den Plan gewöhnlich lesen (hält den
    Transaktionsstand fest), dann die Zonenzeile mit `FOR UPDATE` sperren (die zweite Geste
    wartet, bis die erste festgeschrieben hat) und die Punkte der Zone gesperrt -- also aktuell
    -- erneut lesen. Weicht das vom ersten Stand ab, hat die andere Geste geschrieben, und die
    Version des Aufrufers ist überholt. SQLite kennt `FOR UPDATE` nicht und lässt es weg; dort
    serialisiert die Datenbank Schreiber ohnehin."""
    columns = (
        SchedulePoint.id,
        SchedulePoint.weekday,
        SchedulePoint.minute_of_day,
        SchedulePoint.setpoint_mode_id,
    )
    ordering = (SchedulePoint.weekday, SchedulePoint.minute_of_day)
    seen = [
        tuple(row)
        for row in session.execute(
            select(*columns).where(SchedulePoint.zone_id == zone_id).order_by(*ordering)
        )
    ]
    session.execute(select(Zone.id).where(Zone.id == zone_id).with_for_update()).all()
    try:
        current = [
            tuple(row)
            for row in session.execute(
                select(*columns)
                .where(SchedulePoint.zone_id == zone_id)
                .order_by(*ordering)
                .with_for_update()
            )
        ]
    except OperationalError as exc:
        # MariaDB mit `innodb_snapshot_isolation` (Standard seit 11.6) meldet die überholte
        # Zeile selbst: 1020 "Record has changed since last read". Dasselbe wie ein
        # abweichender Stand -- eine Geste hat dazwischen festgeschrieben.
        if exc.orig is not None and exc.orig.args[:1] == (_RECORD_CHANGED,):
            return False
        raise
    return seen == current


def _check_version(session: Session, zone: Zone, version: str) -> None:
    if not _lock_zone(session, zone.id) or _version(session, zone.id) != version:
        raise RoomApiError(
            409,
            "Der Zeitplan wurde inzwischen geändert. Bitte neu laden.",
            "version_conflict",
            "version",
        )


def _point_of(session: Session, zone: Zone, point_id: int) -> SchedulePoint:
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone.id:
        raise RoomApiError(404, "Schaltpunkt nicht gefunden.", "point_not_found")
    return point


def _slot_taken(session: Session, zone: Zone, weekday: int, minute: int) -> bool:
    """Nur zur Einordnung der Fehlerart (Überschneidung); entschieden hat die Domäne."""
    return (
        session.scalar(
            select(SchedulePoint.id).where(
                SchedulePoint.zone_id == zone.id,
                SchedulePoint.weekday == weekday,
                SchedulePoint.minute_of_day == minute,
            )
        )
        is not None
    )


def _point_error(
    session: Session, zone: Zone, exc: ScheduleError, weekday: int, minute: int
) -> RoomApiError:
    if exc.field == "time_of_day" and _slot_taken(session, zone, weekday, minute):
        return RoomApiError(409, exc.notice, "overlap", exc.field)
    return _schedule_error(exc)


def _copy_targets(session: Session, principal: Principal, zone: Zone) -> list[CopyTargetOut]:
    return [
        CopyTargetOut(
            id=other.id,
            name=other.display_name,
            version=_version(session, other.id),
            has_plan=session.scalar(
                select(SchedulePoint.id).where(SchedulePoint.zone_id == other.id).limit(1)
            )
            is not None,
        )
        for other in visible_zones(session, principal, "schedule.manage")
        if other.id != zone.id
    ]


def _schedule(session: Session, principal: Principal, zone: Zone) -> ScheduleOut:
    settings = _settings(session)
    session.flush()
    points = _points(session, zone.id)
    modes = _modes(session)
    stored: dict[int, Decimal] = {
        mode_id: temperature
        for mode_id, temperature in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
    names = {mode.id: mode.name for mode in modes}
    can_edit = has_permission(principal, "schedule.manage", zone.id)
    return ScheduleOut(
        zone_id=zone.id,
        zone_name=zone.display_name,
        timezone=settings.timezone,
        version=_version(session, zone.id),
        points=[
            PointOut(
                id=p.id,
                weekday=p.weekday,
                minute_of_day=p.minute_of_day,
                mode_id=p.setpoint_mode_id,
            )
            for p in points
        ],
        segments=[
            SegmentOut(
                weekday=s.weekday,
                start_minute=s.start_minute,
                end_minute=s.end_minute,
                mode_id=s.mode_id,
                mode_name=s.mode_name,
                point_id=s.point_id,
                temperature_c=_dec(stored.get(s.mode_id)),
                warmth=warmth_fraction(stored.get(s.mode_id)),
            )
            for s in week_segments(points, names)
        ],
        modes=[
            PlanModeOut(
                id=m.id,
                code=m.code,
                name=m.name,
                temperature_c=_dec(stored.get(m.id)),
                warmth=warmth_fraction(stored.get(m.id)),
            )
            for m in modes
        ],
        can_edit=can_edit,
        full_editor=can_edit and principal.ui_profile is WebUiProfile.ADMIN,
        copy_targets=_copy_targets(session, principal, zone) if can_edit else [],
    )


def _change(
    session: Session,
    principal: Principal,
    zone: Zone,
    *,
    changed: bool,
    gesture: ScheduleGesture | None = None,
    history: Sequence[ScheduleSnapshot] = (),
) -> ScheduleChangeOut:
    return ScheduleChangeOut(
        **_schedule(session, principal, zone).model_dump(),
        changed=changed,
        undo_token=_undo_token(zone.id, gesture, history) if gesture else None,
    )


MAX_UNDO_STEPS = 20


def _history(
    previous: str | None, zone: Zone, version: str, gesture: ScheduleGesture | None
) -> list[ScheduleSnapshot]:
    """Die Vorgänger-Stände für die neue Marke: nur wenn die frühere Marke genau zu dem
    Stand gehört, aus dem diese Geste hervorging (gleiche Zone, gleiche Version, ihr
    `after` ist unser `before`); sonst beginnt eine neue Kette."""
    if previous is None or gesture is None:
        return []
    try:
        payload = _undo_payload(previous)
        if payload["zone_id"] != zone.id or str(payload["revision"]) != version:
            return []
        if _snapshot(payload["after"]) != gesture[0]:
            return []
        raw: Any = payload.get("history", [])
        earlier = [_snapshot(snapshot) for snapshot in raw]
        return [_snapshot(payload["before"]), *earlier][:MAX_UNDO_STEPS]
    except (KeyError, TypeError, ValueError):
        return []


def _point_gesture(session: Session, zone: Zone, before: ScheduleSnapshot) -> ScheduleGesture:
    """Rücknahme-Geste einer Punkt-Operation (sie liefert selbst keine); nur aufrufen, wenn
    sie den Plan verändert hat."""
    session.flush()
    after = schedule_snapshot(_points(session, zone.id))
    return before, after, int(_version(session, zone.id))


def _before(session: Session, zone: Zone) -> ScheduleSnapshot:
    return schedule_snapshot(_points(session, zone.id))


def _point_change(
    session: Session,
    principal: Principal,
    zone: Zone,
    before: ScheduleSnapshot,
    version: str,
    previous: str | None,
    *,
    changed: bool,
) -> ScheduleChangeOut:
    gesture = _point_gesture(session, zone, before) if changed else None
    return _change(
        session,
        principal,
        zone,
        changed=changed,
        gesture=gesture,
        history=_history(previous, zone, version, gesture),
    )


# -- Zeitplan lesen ---------------------------------------------------------------------


@router.get("/schedule/{zone_id}")
def show_schedule(zone_id: int, principal: Who, session: Sess) -> ScheduleOut:
    return _schedule(session, principal, _visible(session, principal, zone_id))


@router.get("/schedule/{zone_id}/preview")
def show_preview(zone_id: int, principal: Who, session: Sess) -> PreviewOut:
    """Die nächsten 24 Stunden (`domain.schedule.schedule_forecast`): Betriebsart,
    Übersteuerung, Urlaub und Plan in der Rangfolge der Regelung."""
    zone = _visible(session, principal, zone_id)
    settings = _settings(session)
    now = utcnow()
    names = {mode.id: mode.name for mode in _modes(session)}
    segments = schedule_forecast(session, zone, now)
    return PreviewOut(
        zone_id=zone.id,
        timezone=settings.timezone,
        generated_at=str(_iso(now)),
        horizon_hours=24,
        segments=[
            ForecastSegmentOut(
                starts_at=str(_iso(s.starts_at)),
                ends_at=str(_iso(s.ends_at)),
                local_starts_at=local_time(s.starts_at, settings.timezone).isoformat(),
                duration_minutes=round((s.ends_at - s.starts_at).total_seconds() / 60),
                temperature_c=format(s.setpoint.temperature_c, "f"),
                reason=s.setpoint.reason,
                mode_id=s.setpoint.mode_id,
                mode_name=names.get(s.setpoint.mode_id) if s.setpoint.mode_id is not None else None,
                warmth=warmth_fraction(s.setpoint.temperature_c),
            )
            for s in segments
        ],
    )


# -- Zeitplan schreiben (Anlagensicht: der vollständige Editor) -------------------------


@router.post("/schedule/{zone_id}/paint")
def paint(zone_id: int, body: PaintIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    """Einen Bereich eines Tages setzen (`paint_schedule_interval`); auch Übermalen."""
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    _check_version(session, zone, body.version)
    try:
        gesture = paint_schedule_interval(
            session,
            zone,
            weekday=body.weekday,
            start_minute=body.start_minute,
            end_minute=body.end_minute,
            mode_id=body.mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        raise _schedule_error(exc) from exc
    return _change(
        session,
        principal,
        zone,
        changed=gesture is not None,
        gesture=gesture,
        history=_history(body.previous_undo_token, zone, body.version, gesture),
    )


@router.post("/schedule/{zone_id}/points")
def add_point(zone_id: int, body: PointIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    _check_version(session, zone, body.version)
    before = _before(session, zone)
    try:
        create_schedule_point(
            session,
            zone,
            weekday=body.weekday,
            minute=body.minute_of_day,
            mode_id=body.mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        raise _point_error(session, zone, exc, body.weekday, body.minute_of_day) from exc
    return _point_change(
        session, principal, zone, before, body.version, body.previous_undo_token, changed=True
    )


@router.put("/schedule/{zone_id}/points/{point_id}")
def move_point(
    zone_id: int, point_id: int, body: PointMoveIn, principal: Who, session: Sess
) -> ScheduleChangeOut:
    """Punkt verschieben (mit Eintrag im Änderungsprotokoll wie bei MCP und REST)."""
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    point = _point_of(session, zone, point_id)
    _check_version(session, zone, body.version)
    before = _before(session, zone)
    try:
        recorded = change_log.move_point(
            session,
            _actor(principal),
            zone,
            point,
            weekday=body.weekday,
            minute=body.minute_of_day,
        )
    except ScheduleError as exc:
        raise _point_error(session, zone, exc, body.weekday, body.minute_of_day) from exc
    return _point_change(
        session,
        principal,
        zone,
        before,
        body.version,
        body.previous_undo_token,
        changed=recorded.change_id is not None,
    )


@router.put("/schedule/{zone_id}/points/{point_id}/mode")
def set_point_mode(
    zone_id: int, point_id: int, body: PointModeIn, principal: Who, session: Sess
) -> ScheduleChangeOut:
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    point = _point_of(session, zone, point_id)
    _check_version(session, zone, body.version)
    before_mode = point.setpoint_mode_id
    before = _before(session, zone)
    try:
        change_schedule_point_mode(
            session,
            zone,
            point,
            mode_id=body.mode_id,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        raise _schedule_error(exc) from exc
    return _point_change(
        session,
        principal,
        zone,
        before,
        body.version,
        body.previous_undo_token,
        changed=before_mode != body.mode_id,
    )


@router.delete("/schedule/{zone_id}/points/{point_id}")
def remove_point(
    zone_id: int,
    point_id: int,
    version: str,
    principal: Who,
    session: Sess,
    previous_undo_token: str | None = None,
) -> ScheduleChangeOut:
    """Die Version kommt als Abfrageparameter (`?version=`), ein DELETE hat keinen Körper
    (ebenso `previous_undo_token`)."""
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    point = _point_of(session, zone, point_id)
    _check_version(session, zone, version)
    before = _before(session, zone)
    delete_schedule_point(
        session, zone, point, user_id=principal.user_id, token_id=principal.token_id
    )
    return _point_change(
        session, principal, zone, before, version, previous_undo_token, changed=True
    )


# -- Blöcke (Anlagensicht, atomar) ------------------------------------------------------


def _block_error(exc: ScheduleError) -> RoomApiError:
    if exc.field == "block":
        return RoomApiError(404, exc.notice, "block_not_found", exc.field)
    if exc.field == "overlap":
        return RoomApiError(409, exc.notice, "overlap", "time_range")
    return _schedule_error(exc)


def _block_operation(
    zone_id: int,
    body: BlockIn,
    principal: Principal,
    session: Session,
    operation: Callable[[Zone], ScheduleGesture | None],
) -> ScheduleChangeOut:
    zone = _editable_zone(session, principal, zone_id)
    _need_profile(principal, WebUiProfile.ADMIN)
    _check_version(session, zone, body.version)
    try:
        gesture = operation(zone)
    except ScheduleError as exc:
        raise _block_error(exc) from exc
    return _change(
        session,
        principal,
        zone,
        changed=gesture is not None,
        gesture=gesture,
        history=_history(body.previous_undo_token, zone, body.version, gesture),
    )


@router.post("/schedule/{zone_id}/block/move")
def move_block(
    zone_id: int, body: BlockTargetIn, principal: Who, session: Sess
) -> ScheduleChangeOut:
    """Block an eine freie Stelle verschieben; sein alter Bereich erbt den Modus davor."""
    return _block_operation(
        zone_id,
        body,
        principal,
        session,
        lambda zone: move_schedule_block(
            session,
            zone,
            weekday=body.weekday,
            start_minute=body.start_minute,
            end_minute=body.end_minute,
            to_weekday=body.to_weekday,
            to_start_minute=body.to_start_minute,
            user_id=principal.user_id,
            token_id=principal.token_id,
        ),
    )


@router.post("/schedule/{zone_id}/block/duplicate")
def duplicate_block(
    zone_id: int, body: BlockTargetIn, principal: Who, session: Sess
) -> ScheduleChangeOut:
    """Block an eine freie Stelle kopieren; die Quelle bleibt."""
    return _block_operation(
        zone_id,
        body,
        principal,
        session,
        lambda zone: duplicate_schedule_block(
            session,
            zone,
            weekday=body.weekday,
            start_minute=body.start_minute,
            end_minute=body.end_minute,
            to_weekday=body.to_weekday,
            to_start_minute=body.to_start_minute,
            user_id=principal.user_id,
            token_id=principal.token_id,
        ),
    )


@router.post("/schedule/{zone_id}/block/resize")
def resize_block(
    zone_id: int, body: BlockResizeIn, principal: Who, session: Sess
) -> ScheduleChangeOut:
    """Genau eine Kante eines Blocks verschieben; mit `adjacent` wandert die gemeinsame
    Kante des angrenzenden Blocks mit (Voraussetzung fürs Verlängern)."""
    return _block_operation(
        zone_id,
        body,
        principal,
        session,
        lambda zone: resize_schedule_block(
            session,
            zone,
            weekday=body.weekday,
            start_minute=body.start_minute,
            end_minute=body.end_minute,
            new_start_minute=body.new_start_minute,
            new_end_minute=body.new_end_minute,
            adjacent=body.adjacent,
            user_id=principal.user_id,
            token_id=principal.token_id,
        ),
    )


@router.post("/schedule/{zone_id}/block/delete")
def delete_block(zone_id: int, body: BlockIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    """Block entfernen; der vorherige läuft über seinen Bereich weiter."""
    return _block_operation(
        zone_id,
        body,
        principal,
        session,
        lambda zone: delete_schedule_block(
            session,
            zone,
            weekday=body.weekday,
            start_minute=body.start_minute,
            end_minute=body.end_minute,
            user_id=principal.user_id,
            token_id=principal.token_id,
        ),
    )


# -- Zeitplan schreiben (beide Profile) -------------------------------------------------


@router.post("/schedule/{zone_id}/copy-day")
def copy_day(zone_id: int, body: CopyDayIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    zone = _editable_zone(session, principal, zone_id)
    _check_version(session, zone, body.version)
    targets = list(range(1, 6)) if body.scope == "workdays" else list(range(1, 8))
    try:
        gesture = copy_schedule_day(
            session,
            zone,
            source_weekday=body.weekday,
            target_weekdays=targets,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        raise _schedule_error(exc) from exc
    return _change(
        session,
        principal,
        zone,
        changed=gesture is not None,
        gesture=gesture,
        history=_history(body.previous_undo_token, zone, body.version, gesture),
    )


@router.post("/schedule/{zone_id}/copy-week")
def copy_week(zone_id: int, body: CopyWeekIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    """Den Plan dieses Raums auf andere Räume übertragen (`adopt_schedule`, je Ziel).

    Alles-oder-nichts: Quelle (lesbar) und **jedes** Ziel (`schedule.manage`, Version
    aktuell) werden geprüft, bevor das erste Ziel geschrieben wird. Ein Ziel, das es nicht
    gibt oder das der Principal nicht beschreiben darf, antwortet gleich (404) -- ohne
    Hinweis, welches von beidem zutrifft. Die Quelle braucht nur `zone.read`, wie bei der
    HTML-Übernahme (dort in umgekehrter Richtung: das Ziel holt sich den Plan)."""
    source = _visible(session, principal, zone_id)
    writable = {z.id: z for z in visible_zones(session, principal, "schedule.manage")}
    ids = [t.zone_id for t in body.targets]
    # Quelle und alle schreibbaren Ziele in fester Reihenfolge sperren (kein Deadlock zweier
    # gegenläufiger Übernahmen); erst danach die Prüfungen in ihrer gewohnten Reihenfolge.
    for locked_id in sorted({source.id, *(i for i in ids if i in writable)}):
        _lock_zone(session, locked_id)
    _check_version(session, source, body.version)
    if not ids or len(set(ids)) != len(ids) or source.id in ids:
        raise RoomApiError(
            422, "Bitte mindestens einen anderen Raum, jeden nur einmal.", "validation", "targets"
        )
    if any(target_id not in writable for target_id in ids):
        raise RoomApiError(404, "Raum nicht gefunden.", "not_found")
    for target in body.targets:
        _check_version(session, writable[target.zone_id], target.version)
    for target in body.targets:
        adopt_schedule(
            session,
            writable[target.zone_id],
            source,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    session.flush()
    return _change(session, principal, source, changed=True)


@router.post("/schedule/{zone_id}/undo")
def undo(zone_id: int, body: UndoIn, principal: Who, session: Sess) -> ScheduleChangeOut:
    """Die letzte Geste dieses Plans zurücknehmen (`undo_schedule_gesture`), solange
    niemand danach etwas geändert hat."""
    zone = _editable_zone(session, principal, zone_id)
    try:
        payload = _undo_payload(body.undo_token)
        if payload["zone_id"] != zone.id:
            raise ValueError("wrong zone")
        before = _snapshot(payload["before"])
        after = _snapshot(payload["after"])
        revision = int(str(payload["revision"]))
        raw_history: Any = payload.get("history", [])
        history = [_snapshot(snapshot) for snapshot in raw_history]
    except (KeyError, TypeError, ValueError) as exc:
        raise RoomApiError(
            422, "Die Rücknahme-Marke ist ungültig.", "validation", "undo_token"
        ) from exc
    if not _lock_zone(session, zone.id):
        raise RoomApiError(
            409, "Der Zeitplan wurde inzwischen geändert.", "undo_stale", "undo"
        )
    try:
        undo_schedule_gesture(
            session,
            zone,
            before=before,
            expected_after=after,
            expected_revision=revision,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ScheduleError as exc:
        raise RoomApiError(409, exc.notice, "undo_stale", exc.field) from exc
    # Der Stand ist jetzt `before`; die Marke für einen weiteren Schritt führt zu dessen Vorgänger.
    session.flush()
    revision_now = _current_schedule_revision(session, zone.id)
    gesture: ScheduleGesture | None = None
    if history and revision_now is not None:
        gesture = (history[0], before, revision_now)
    return _change(session, principal, zone, changed=True, gesture=gesture, history=history[1:])


def _snapshot(raw: Any) -> ScheduleSnapshot:
    """Aus der signierten Marke; Fehlform (`TypeError`/`ValueError`) fängt der Aufrufer."""
    return tuple((int(p[0]), int(p[1]), int(p[2])) for p in raw)


# -- Urlaub (Anlagensicht) --------------------------------------------------------------


def _vacation_state(session: Session, principal: Principal) -> VacationStateOut:
    settings = _settings(session)
    now = utcnow()
    entry = current_or_upcoming_vacation(session, now)
    return VacationStateOut(
        timezone=settings.timezone,
        can_manage=has_permission(principal, "vacation.manage"),
        vacation=(
            VacationOut(
                id=entry.id,
                starts_at=str(_iso(entry.starts_at)),
                ends_at=str(_iso(entry.ends_at)),
                start_date=local_time(entry.starts_at, settings.timezone).date().isoformat(),
                # `ends_at` ist der Beginn des Tages nach dem letzten Urlaubstag.
                end_date=(
                    local_time(entry.ends_at, settings.timezone).date() - timedelta(days=1)
                ).isoformat(),
                setback_temperature_c=format(entry.setback_temperature_c, "f"),
                running=entry.starts_at <= now,
            )
            if entry is not None
            else None
        ),
    )


def _date(text: str, field: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise RoomApiError(422, "Bitte ein gültiges Datum eingeben.", "validation", field) from exc


@router.get("/vacation")
def show_vacation(principal: Who, session: Sess) -> VacationStateOut:
    _need_profile(principal, WebUiProfile.ADMIN)
    _need(principal, "zone.read")
    return _vacation_state(session, principal)


@router.post("/vacation")
def set_vacation(body: VacationIn, principal: Who, session: Sess) -> VacationStateOut:
    _need_profile(principal, WebUiProfile.ADMIN)
    _need(principal, "vacation.manage")
    settings = _settings(session)
    start = _date(body.start_date, "start_date")
    end = _date(body.end_date, "end_date")
    temperature = _temperature(body.setback_temperature_c)
    now = utcnow()
    if current_or_upcoming_vacation(session, now) is not None:
        raise RoomApiError(
            409,
            "Es gibt bereits einen laufenden oder geplanten Urlaub -- erst den bestehenden "
            "beenden.",
            "vacation_exists",
            "start_date",
        )
    try:
        change_log.create_vacation(
            session,
            _actor(principal),
            start_date=start,
            end_date=end,
            setback_temperature_c=temperature,
            timezone_name=settings.timezone,
            now=now,
        )
    except (ScheduleError, DomainError) as exc:
        raise RoomApiError(422, exc.notice, "validation", exc.field) from exc
    return _vacation_state(session, principal)


@router.delete("/vacation")
def end_vacation(principal: Who, session: Sess) -> VacationStateOut:
    _need_profile(principal, WebUiProfile.ADMIN)
    _need(principal, "vacation.manage")
    recorded = change_log.cancel_vacation(session, _actor(principal), now=utcnow())
    if recorded.result is None:
        raise RoomApiError(409, "Es läuft oder plant gerade kein Urlaub.", "no_vacation")
    return _vacation_state(session, principal)


# -- Abwesenheit (Wohnungssicht) --------------------------------------------------------


def _absence_state(session: Session, principal: Principal) -> AbsenceStateOut:
    settings = _settings(session)
    eligible = visible_zones(session, principal, "override.create")
    entry = running_absence(session, principal.user_id, utcnow())
    return AbsenceStateOut(
        timezone=settings.timezone,
        can_start=bool(eligible) and entry is None,
        eligible_zone_ids=[z.id for z in eligible],
        absence=(
            AbsenceInfoOut(
                id=entry.id,
                starts_at=str(_iso(entry.starts_at)),
                ends_at=str(_iso(entry.ends_at)),
                setback_temperature_c=format(entry.setback_temperature_c, "f"),
                zones=[
                    CopyTargetOut(id=z.id, name=z.display_name, version="", has_plan=False)
                    for z in absence_zones(session, entry)
                ],
            )
            if entry is not None
            else None
        ),
    )


@router.get("/absence")
def show_absence(principal: Who, session: Sess) -> AbsenceStateOut:
    _need_profile(principal, WebUiProfile.TENANT)
    return _absence_state(session, principal)


@router.post("/absence")
def begin_absence(body: AbsenceIn, principal: Who, session: Sess) -> AbsenceStateOut:
    """„Ich bin bis <Datum> weg": die eigenen Räume (`override.create`) sparsamer
    regeln. Die Räume bestimmt allein der Server, nicht der Aufrufer."""
    _need_profile(principal, WebUiProfile.TENANT)
    settings = _settings(session)
    zones = visible_zones(session, principal, "override.create")
    if not zones:
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")
    temperature = _temperature(body.temperature_c)
    ends_at = local_day_start_utc(_date(body.return_on, "return_on"), settings.timezone)
    try:
        start_absence(
            session,
            zones,
            temperature,
            ends_at,
            now=utcnow(),
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except DomainError as exc:
        if exc.field == "absence":
            raise RoomApiError(409, exc.notice, "absence_running", exc.field) from exc
        raise _domain_error(exc) from exc
    return _absence_state(session, principal)


@router.post("/absence/end")
def finish_absence(principal: Who, session: Sess) -> AbsenceStateOut:
    """Beendet die eigene laufende Abwesenheit -- **jede** laufende, wie die HTML-Ansicht
    (zwei gleichzeitig abgeschickte Formulare können zwei Klammern anlegen)."""
    _need_profile(principal, WebUiProfile.TENANT)
    now = utcnow()
    running = running_absences(session, principal.user_id, now)
    if not running:
        raise RoomApiError(409, "Es läuft gerade keine Abwesenheit.", "no_absence")
    for entry in running:
        end_absence(session, entry, now=now)
    return _absence_state(session, principal)


# -- Modi (Anlagensicht, mode.manage) ---------------------------------------------------


def _modes_out(session: Session) -> ModesOut:
    return ModesOut(
        modes=[
            ModeAdminOut(
                id=m.id,
                code=m.code,
                name=m.name,
                sort_order=m.sort_order,
                is_builtin=m.is_builtin,
                delete_lock=delete_guard(session, m),
            )
            for m in _modes(session)
        ]
    )


def _mode_admin(principal: Principal) -> None:
    _need_profile(principal, WebUiProfile.ADMIN)
    _need(principal, "mode.manage")


def _mode_of(session: Session, mode_id: int) -> SetpointMode:
    mode = session.get(SetpointMode, mode_id)
    if mode is None:
        raise RoomApiError(404, "Modus nicht gefunden.", "mode_not_found")
    return mode


@router.get("/modes")
def list_modes(principal: Who, session: Sess) -> ModesOut:
    _mode_admin(principal)
    return _modes_out(session)


@router.post("/modes")
def add_mode(body: ModeIn, principal: Who, session: Sess) -> ModesOut:
    _mode_admin(principal)
    try:
        create_mode(
            session,
            code=body.code,
            name=body.name,
            sort_order=body.sort_order,
            user_id=principal.user_id,
        )
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return _modes_out(session)


@router.put("/modes/{mode_id}")
def rename_mode(mode_id: int, body: ModeIn, principal: Who, session: Sess) -> ModesOut:
    _mode_admin(principal)
    mode = _mode_of(session, mode_id)
    try:
        update_mode(
            session,
            mode,
            code=body.code,
            name=body.name,
            sort_order=body.sort_order,
            user_id=principal.user_id,
        )
    except DomainError as exc:
        raise _domain_error(exc) from exc
    return _modes_out(session)


@router.delete("/modes/{mode_id}")
def remove_mode(mode_id: int, principal: Who, session: Sess) -> ModesOut:
    _mode_admin(principal)
    mode = _mode_of(session, mode_id)
    try:
        delete_mode(session, mode, user_id=principal.user_id)
    except DomainError as exc:
        raise RoomApiError(409, exc.notice, "mode_locked", exc.field) from exc
    return _modes_out(session)
