"""Auswertung für die Svelte-Oberfläche (`/ui-api`): Verlauf, Heizzeit, Verschleiß, Ereignisse.

Dünner Adapter (Grundsatz 6): die Zahlen kommen aus denselben Domänenfunktionen wie zuvor
(`domain/history_report`, `statistics`, `audit_query`, `change_log`); hier stehen
Rechteprüfung, Eingabeprüfung und Serialisierung. Der Verlauf liefert **Rohdaten statt SVG**
(Regeln für Lücken, Verdichtung und Obergrenzen: `domain/history_report`).

Rechte werden in jedem Endpunkt neu geprüft:

* Verlauf, Heizzeit: `zone.read` je Zone. Eine unbekannte und eine nicht sichtbare Zone
  antworten mit demselben 404.
* Relaisverschleiß, Ereignisse, Änderungsprotokoll: `audit.read`, anlagenweit **oder auf
  Räume beschränkt** (`ZoneScope`). Anlagenweit sieht alles; beschränkt nur die Einträge der
  eigenen Räume -- der Filter steht in der Abfrage (Seiten, `has_more`, Auswahllisten).
  Verschleiß zusätzlich nur Zonen mit `zone.read`.
* Rücknahme: `audit.read` mit dem Eintrag im Bereich (sonst 404 wie ein unbekannter Eintrag)
  **und** die Rechte der ursprünglichen Änderung (prüft die Domäne gegen die Zone des
  Eintrags). Ein Konflikt antwortet mit 409 und einem Bestätigungswert; nur damit lässt sich
  erzwingen.

Fehler: `{"detail", "code"[, "field"]}` wie bei den Räumen.
"""

import csv
import hmac
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain import history_report
from thermoctl.domain.audit_query import (
    ENTRIES_PER_PAGE,
    MAX_EXPORT_ROWS,
    MAX_TEXT_LENGTH,
    export_audit,
    list_audit,
    scoped_actions,
    scoped_source_codes,
)
from thermoctl.domain.authz import Forbidden, ZoneScope, require_scope, visible_zones
from thermoctl.domain.change_log import (
    CHANGE_TYPES,
    MAX_LIMIT,
    ChangeAlreadyEffective,
    ChangeAlreadyReverted,
    ChangeConflict,
    ChangeNotFound,
    ChangeNotPossible,
    ChangeNotRevertible,
    ChangeView,
    get_change,
    list_changes,
    revert_change,
)
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import ScheduleError
from thermoctl.domain.sensor_failure_policy import PolicyError
from thermoctl.domain.statistics import (
    DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS,
    PERIODS,
    RelayDeviceStatistics,
    heating_periods,
    relay_operations,
)
from thermoctl.domain.time import local_day_start_utc, local_time
from thermoctl.domain.zone_history_chart import GAP
from thermoctl.domain.zone_settings import ParameterOutOfRange
from thermoctl.web.shared.changes import confirmation_token
from thermoctl.web.ui_api_rooms import (
    RoomApiError,
    _actor,
    _iso,
    _RoomsRoute,
    _visible_zone,
)

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

GAP_THRESHOLD_SECONDS = int(GAP.total_seconds())
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


# -- Hilfen -----------------------------------------------------------------------------


def _audit(principal: Principal) -> ZoneScope:
    """Der Bereich von `audit.read` (anlagenweit oder Räume); ohne das Recht 403."""
    try:
        return require_scope(principal, "audit.read")
    except Forbidden:
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden") from None


def _num(value: float | None) -> str | None:
    """Zahl als Dezimalzeichenkette (wie überall in `/ui-api`), `None` bleibt `None`."""
    return format(Decimal(repr(value)), "f") if value is not None else None


def _invalid(detail: str, field: str) -> RoomApiError:
    return RoomApiError(422, detail, "validation", field)


def _moment(value: str, field: str) -> datetime:
    """ISO-8601 als naive UTC (die Darstellung der Datenbank); ohne Zone gilt UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _invalid("Bitte einen Zeitpunkt im ISO-8601-Format angeben.", field) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _day(value: str, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _invalid("Bitte ein gültiges Datum eingeben.", field) from None


def _page(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise _invalid("Die Seitennummer muss eine ganze Zahl sein.", "page") from None
    return max(1, number)


def _timezone(session: Session) -> tuple[str, Setting | None]:
    settings = session.get(Setting, 1)
    return (settings.timezone if settings is not None else "UTC"), settings


def _period_start(now: datetime, period: str, timezone_name: str) -> datetime:
    """Erster lokaler Tag des Zeitraums, als UTC (gleiche Rechnung wie die HTML-Seiten)."""
    if period not in PERIODS:
        raise _invalid("Ungültiger Zeitraum.", "period")
    days = PERIODS[period][1]
    first = local_time(now, timezone_name).date() - timedelta(days=days - 1)
    return local_day_start_utc(first, timezone_name)


# -- Verlauf ----------------------------------------------------------------------------


class BucketOut(BaseModel):
    """Ein Zeitfach. `rows == 0`: keine Zeile, alle Werte `null` -- eine Lücke, kein Wert."""

    start: str
    rows: int
    measurements: int
    temperature_c: str | None
    temperature_min_c: str | None
    temperature_max_c: str | None
    scheduled_min_c: str | None
    scheduled_max_c: str | None
    effective_min_c: str | None
    effective_max_c: str | None
    heat_percent: int | None
    #: Kleinste und größte Absenkung im Fach (`solar_setback_k` ist die größte).
    solar_setback_min_k: str | None
    solar_setback_k: str | None


class GapOut(BaseModel):
    start: str
    end: str


class UnitsOut(BaseModel):
    temperature: str
    setback: str
    heat: str


class LimitsOut(BaseModel):
    max_span_days: int
    max_buckets: int
    min_resolution_seconds: int
    max_rows: int
    gap_threshold_seconds: int


class HistoryOut(BaseModel):
    zone_id: int
    zone_name: str
    timezone: str
    start: str
    end: str
    resolution_seconds: int
    rows: int
    #: Ist die Anlage scharf? Sonst sind Sollwert und Heizanforderung Entscheidungen der
    #: Regelung („hätte“), keine Aufzeichnung dessen, was in den Räumen geschah.
    control_armed: bool
    units: UnitsOut
    limits: LimitsOut
    buckets: list[BucketOut]
    gaps: list[GapOut]


def _history(
    session: Session,
    principal: Principal,
    zone_id: int,
    start: str,
    end: str,
    resolution: int | None,
) -> tuple[HistoryOut, history_report.HistoryReport]:
    zone = _visible_zone(session, principal, zone_id)
    timezone_name, settings = _timezone(session)
    try:
        report = history_report.history_report(
            session,
            zone.id,
            _moment(start, "from"),
            _moment(end, "to"),
            requested_resolution=resolution,
            now=utcnow(),
        )
    except history_report.ReportRangeError as exc:
        raise _invalid(str(exc), exc.field or "from") from None
    out = HistoryOut(
        zone_id=zone.id,
        zone_name=zone.display_name,
        timezone=timezone_name,
        start=report.start.isoformat(),
        end=report.end.isoformat(),
        resolution_seconds=report.resolution_seconds,
        rows=report.rows,
        control_armed=bool(settings and settings.control_armed),
        units=UnitsOut(temperature="°C", setback="K", heat="%"),
        limits=LimitsOut(
            max_span_days=history_report.MAX_SPAN.days,
            max_buckets=history_report.MAX_BUCKETS,
            min_resolution_seconds=history_report.MIN_RESOLUTION_SECONDS,
            max_rows=history_report.MAX_ROWS,
            gap_threshold_seconds=GAP_THRESHOLD_SECONDS,
        ),
        buckets=[
            BucketOut(
                start=b.start.isoformat(),
                rows=b.rows,
                measurements=b.measurements,
                temperature_c=_num(b.temperature_mean_c),
                temperature_min_c=_num(b.temperature_min_c),
                temperature_max_c=_num(b.temperature_max_c),
                scheduled_min_c=_num(b.scheduled_min_c),
                scheduled_max_c=_num(b.scheduled_max_c),
                effective_min_c=_num(b.effective_min_c),
                effective_max_c=_num(b.effective_max_c),
                heat_percent=b.heat_percent,
                solar_setback_min_k=_num(b.solar_setback_min_k),
                solar_setback_k=_num(b.solar_setback_k),
            )
            for b in report.buckets
        ],
        gaps=[GapOut(start=g.start.isoformat(), end=g.end.isoformat()) for g in report.gaps],
    )
    return out, report


# `from` ist in Python ein Schlüsselwort: der Parameter heißt `start`, die Adresse `from`.
_From = Annotated[str, Query(alias="from")]


@router.get("/history")
def show_history(
    zone_id: int,
    start: _From,
    to: str,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    resolution: int | None = None,
) -> HistoryOut:
    """Rohe Messreihe einer Zone (Regeln: `domain/history_report`)."""
    return _history(session, principal, zone_id, start, to, resolution)[0]


def csv_cell(text: str) -> str:
    """Schutz vor Formelinjektion: Text, den eine Tabellenkalkulation als Formel läse
    (Beginn mit `=`, `+`, `-`, `@`, Tab, Zeilenumbruch), bekommt ein Apostroph vorangestellt.
    Nur für **Text**; Zahlen stehen unverändert da."""
    return "'" + text if text.startswith(_CSV_FORMULA_START) else text


@router.get("/history.csv")
def export_history(
    zone_id: int,
    start: _From,
    to: str,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    resolution: int | None = None,
) -> Response:
    """Dieselben Zeitfächer wie `/history` als CSV (leere Zellen: keine Zeile im Fach)."""
    data, _report = _history(session, principal, zone_id, start, to, resolution)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "zone",
            "bucket_start_utc",
            "rows",
            "measurements",
            "temperature_c",
            "temperature_min_c",
            "temperature_max_c",
            "scheduled_min_c",
            "scheduled_max_c",
            "effective_min_c",
            "effective_max_c",
            "heat_percent",
            "solar_setback_min_k",
            "solar_setback_k",
        ]
    )
    zone_text = csv_cell(data.zone_name)
    for b in data.buckets:
        writer.writerow(
            [
                zone_text,
                b.start,
                b.rows,
                b.measurements,
                *(
                    "" if v is None else v
                    for v in (
                        b.temperature_c,
                        b.temperature_min_c,
                        b.temperature_max_c,
                        b.scheduled_min_c,
                        b.scheduled_max_c,
                        b.effective_min_c,
                        b.effective_max_c,
                        b.heat_percent,
                        b.solar_setback_min_k,
                        b.solar_setback_k,
                    )
                ),
            ]
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="verlauf-{data.zone_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


# -- Heizzeit und Verschleiß ------------------------------------------------------------


class DayOut(BaseModel):
    day: str
    seconds: int


class ZoneHeatingOut(BaseModel):
    zone_id: int
    zone_name: str
    seconds_total: int
    days: list[DayOut]


class HeatingTimeOut(BaseModel):
    timezone: str
    period: str
    control_armed: bool
    zones: list[ZoneHeatingOut]


def _selected_zones(session: Session, principal: Principal, zone_id: int | None) -> list[Zone]:
    if zone_id is not None:
        return [_visible_zone(session, principal, zone_id)]
    return visible_zones(session, principal, "zone.read")


@router.get("/heating-time")
def show_heating_time(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    period: str = "7",
    zone_id: int | None = None,
) -> HeatingTimeOut:
    """Heizanforderung je Zone und lokalem Tag in Sekunden (Entscheidung der Regelung,
    im Probebetrieb also keine Aussage über geschaltete Geräte -- `control_armed` sagt es).
    Tage ohne Messzeilen erscheinen als 0; die Lücke ist hier nicht von „nicht geheizt"
    unterscheidbar (siehe `domain/statistics.heating_periods`)."""
    if not any(code == "zone.read" for code, _zone in principal.grants):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")
    timezone_name, settings = _timezone(session)
    zones = _selected_zones(session, principal, zone_id)
    now = utcnow()
    values = heating_periods(
        session,
        [zone.id for zone in zones],
        _period_start(now, period, timezone_name),
        now,
        cycle_seconds=settings.shadow_interval_seconds if settings is not None else 60,
        timezone_name=timezone_name,
    )
    return HeatingTimeOut(
        timezone=timezone_name,
        period=period,
        control_armed=bool(settings and settings.control_armed),
        zones=[
            ZoneHeatingOut(
                zone_id=zone.id,
                zone_name=zone.display_name,
                seconds_total=values[zone.id].seconds_total,
                days=[
                    DayOut(day=day.day.isoformat(), seconds=day.seconds)
                    for day in values[zone.id].days
                ],
            )
            for zone in zones
        ],
    )


class RelayDayOut(BaseModel):
    day: str
    operations: int
    annual_projection: int


class RelayDeviceOut(BaseModel):
    zone_id: int
    zone_name: str
    device_name: str
    operations_total: int
    annual_projection: int
    lifetime_percent_per_year: float
    lifetime_years: float | None
    wear_level: str
    days: list[RelayDayOut]


class RelayWearOut(BaseModel):
    timezone: str
    period: str
    assumed_lifetime_operations: int
    devices: list[RelayDeviceOut]


@router.get("/relay-wear")
def show_relay_wear(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    period: str = "7",
    zone_id: int | None = None,
) -> RelayWearOut:
    """Bestätigte Schaltwechsel je Gerät und Tag samt Hochrechnung (`audit.read`; beschränkt
    auf Räume nur deren Geräte)."""
    scope = _audit(principal)
    timezone_name, settings = _timezone(session)
    zones = _selected_zones(session, principal, zone_id)
    if scope.zone_ids is not None:
        # Auch eine ausdrücklich gewählte Zone außerhalb des Bereichs ist "nicht gefunden".
        if zone_id is not None and zone_id not in scope.zone_ids:
            raise RoomApiError(404, "Raum nicht gefunden.", "not_found")
        zones = [zone for zone in zones if zone.id in scope.zone_ids]
    names = {zone.id: zone.display_name for zone in zones}
    now = utcnow()
    lifetime = (
        settings.assumed_relay_lifetime_operations
        if settings is not None
        else DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS
    )
    values: list[RelayDeviceStatistics] = relay_operations(
        session,
        list(names),
        _period_start(now, period, timezone_name),
        now,
        timezone_name=timezone_name,
        assumed_lifetime_operations=lifetime,
    )
    values.sort(key=lambda v: (-v.annual_projection, names[v.zone_id], v.device_name))
    return RelayWearOut(
        timezone=timezone_name,
        period=period,
        assumed_lifetime_operations=lifetime,
        devices=[
            RelayDeviceOut(
                zone_id=v.zone_id,
                zone_name=names[v.zone_id],
                device_name=v.device_name,
                operations_total=v.operations_total,
                annual_projection=v.annual_projection,
                lifetime_percent_per_year=v.assumed_lifetime_percent_per_year,
                lifetime_years=v.assumed_lifetime_years,
                wear_level=v.wear_level,
                days=[RelayDayOut(
                        day=d.day.isoformat(),
                        operations=d.operations,
                        annual_projection=d.annual_projection,
                    ) for d in v.days],
            )
            for v in values
        ],
    )


# -- Ereignisse (Audit) -----------------------------------------------------------------


class NamedOut(BaseModel):
    code: str
    label: str


class AuditEntryOut(BaseModel):
    id: int
    occurred_at: str
    source: NamedOut
    actor: str | None
    action: str
    object_type: str
    object_id: str | None
    summary: str
    detail: str | None


class ZoneRefOut(BaseModel):
    id: int
    name: str


class AuditOut(BaseModel):
    timezone: str
    page: int
    page_size: int
    has_more: bool
    entries: list[AuditEntryOut]
    sources: list[NamedOut]
    actions: list[str]
    zones: list[ZoneRefOut]


def _audit_filters(
    from_date: str, to_date: str, text: str
) -> tuple[date | None, date | None, str]:
    from_day, to_day = _day(from_date, "from_date"), _day(to_date, "to_date")
    if from_day is not None and to_day is not None and to_day < from_day:
        raise _invalid("Das Bis-Datum darf nicht vor dem Von-Datum liegen.", "to_date")
    needle = text.strip()
    if len(needle) > MAX_TEXT_LENGTH:
        raise _invalid(f"Der Suchtext darf höchstens {MAX_TEXT_LENGTH} Zeichen lang sein.", "q")
    return from_day, to_day, needle


@router.get("/audit")
def show_audit(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    user: str = "",
    action_code: str = "",
    object: str = "",  # noqa: A002 - Filtername wie in der HTML-Ansicht
    source: str = "",
    q: str = "",
    page: str = "1",
) -> AuditOut:
    """Das Protokoll mit Filtern und Seiten (50 je Seite), neueste zuerst."""
    scope = _audit(principal)
    from_day, to_day, needle = _audit_filters(from_date, to_date, q)
    number = _page(page)
    timezone_name, _settings = _timezone(session)
    rows, has_more = list_audit(
        session,
        from_day=from_day,
        to_day=to_day,
        user=user,
        action_code=action_code,
        source=source,
        object=object,
        text=needle,
        page=number,
        timezone_name=timezone_name,
        zone_ids=scope.zone_ids,
    )
    return AuditOut(
        timezone=timezone_name,
        page=number,
        page_size=ENTRIES_PER_PAGE,
        has_more=has_more,
        entries=[
            AuditEntryOut(
                id=event.id,
                occurred_at=_iso(event.occurred_at) or "",
                source=NamedOut(code=origin.code, label=origin.label),
                actor=(
                    account.username
                    if account is not None
                    else token.name
                    if token is not None
                    else None
                ),
                action=event.action,
                object_type=event.object_type,
                object_id=event.object_id,
                summary=event.summary,
                detail=event.detail,
            )
            for event, origin, account, token in rows
        ],
        sources=_audit_sources(session, scope),
        actions=scoped_actions(session, scope.zone_ids),
        zones=[
            ZoneRefOut(id=z.id, name=z.name)
            for z in visible_zones(session, principal, "zone.read")
            if scope.covers(z.id)
        ],
    )


@router.get("/audit.csv")
def export_audit_csv(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    user: str = "",
    action_code: str = "",
    object: str = "",  # noqa: A002 - Filtername wie in der HTML-Ansicht
    source: str = "",
    q: str = "",
) -> Response:
    """Die gefilterten Ereignisse als CSV (höchstens `MAX_EXPORT_ROWS`, sonst Hinweiszeile)."""
    scope = _audit(principal)
    from_day, to_day, needle = _audit_filters(from_date, to_date, q)
    timezone_name, _settings = _timezone(session)
    rows, truncated = export_audit(
        session,
        from_day=from_day,
        to_day=to_day,
        user=user,
        action_code=action_code,
        source=source,
        object=object,
        text=needle,
        timezone_name=timezone_name,
        zone_ids=scope.zone_ids,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["time_utc", "source", "actor", "action", "object_type", "object_id", "summary", "detail"]
    )
    for event, origin, account, token in rows:
        actor = account.username if account is not None else token.name if token is not None else ""
        writer.writerow(
            [
                _iso(event.occurred_at) or "",
                csv_cell(origin.code),
                csv_cell(actor),
                csv_cell(event.action),
                csv_cell(event.object_type),
                csv_cell(event.object_id or ""),
                csv_cell(event.summary),
                csv_cell(event.detail or ""),
            ]
        )
    if truncated:
        note = f"Hinweis: auf {MAX_EXPORT_ROWS} Zeilen gekürzt, bitte Filter enger setzen"
        writer.writerow([note])
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ereignisse.csv"',
            "Cache-Control": "no-store",
        },
    )


def _sources(session: Session) -> list[NamedOut]:
    return [
        NamedOut(code=code, label=label)
        for code, label in session.execute(
            select(ActorSource.code, ActorSource.label).order_by(ActorSource.label)
        )
    ]


def _audit_sources(session: Session, scope: ZoneScope) -> list[NamedOut]:
    """Die Quellen für den Filter; auf Räume beschränkt nur die in den sichtbaren Ereignissen
    vorkommenden (die Liste verrät sonst, welche Quellen unsichtbare Ereignisse hatten)."""
    present = scoped_source_codes(session, scope.zone_ids)
    return [entry for entry in _sources(session) if present is None or entry.code in present]


# -- Änderungsprotokoll und Rücknahme ---------------------------------------------------


class FieldOut(BaseModel):
    field: str
    before: str | None
    after: str | None


class ChangeOut(BaseModel):
    id: int
    occurred_at: str
    source: str
    user_name: str | None
    token_name: str | None
    change_type: str
    zone_id: int | None
    zone_name: str | None
    object_ref: str | None
    summary: str
    revertible: bool
    not_revertible_reason: str | None
    reverted_at: str | None
    reverted_by_id: int | None
    reverts_id: int | None
    forced: bool
    fields: list[FieldOut]


class ChangesOut(BaseModel):
    timezone: str
    page: int
    page_size: int
    has_more: bool
    entries: list[ChangeOut]
    change_types: list[str]
    sources: list[NamedOut]


def _change(view: ChangeView) -> ChangeOut:
    return ChangeOut(
        id=view.id,
        occurred_at=_iso(view.occurred_at) or "",
        source=view.source,
        user_name=view.user_name,
        token_name=view.token_name,
        change_type=view.change_type,
        zone_id=view.zone_id,
        zone_name=view.zone_name,
        object_ref=view.object_ref,
        summary=view.summary,
        revertible=view.revertible,
        not_revertible_reason=view.not_revertible_reason,
        reverted_at=_iso(view.reverted_at),
        reverted_by_id=view.reverted_by_id,
        reverts_id=view.reverts_id,
        forced=view.forced,
        fields=[FieldOut(field=f.field, before=f.before, after=f.after) for f in view.fields],
    )


@router.get("/changes")
def show_changes(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    from_date: str = "",
    to_date: str = "",
    zone_id: int | None = None,
    change_type: str = "",
    source: str = "",
    hide_reverted: bool = False,
    page: str = "1",
) -> ChangesOut:
    """Das Änderungsprotokoll (50 je Seite), neueste zuerst."""
    scope = _audit(principal)
    from_day, to_day = _day(from_date, "from_date"), _day(to_date, "to_date")
    if from_day is not None and to_day is not None and to_day < from_day:
        raise _invalid("Das Bis-Datum darf nicht vor dem Von-Datum liegen.", "to_date")
    number = _page(page)
    if change_type and change_type not in CHANGE_TYPES:
        raise _invalid("Bitte eine gültige Änderungsart wählen.", "change_type")
    sources = _sources(session)
    if source and source not in {entry.code for entry in sources}:
        raise _invalid("Bitte eine gültige Quelle wählen.", "source")
    timezone_name, _settings = _timezone(session)
    fetched = list_changes(
        session,
        zone_id=zone_id,
        change_type=change_type or None,
        source=source or None,
        from_at=local_day_start_utc(from_day, timezone_name) if from_day else None,
        to_at=(local_day_start_utc(to_day + timedelta(days=1), timezone_name) if to_day else None),
        include_reverted=not hide_reverted,
        limit=min(ENTRIES_PER_PAGE + 1, MAX_LIMIT),
        offset=(number - 1) * ENTRIES_PER_PAGE,
        zone_ids=scope.zone_ids,
    )
    return ChangesOut(
        timezone=timezone_name,
        page=number,
        page_size=ENTRIES_PER_PAGE,
        has_more=len(fetched) > ENTRIES_PER_PAGE,
        entries=[_change(view) for view in fetched[:ENTRIES_PER_PAGE]],
        change_types=list(CHANGE_TYPES),
        sources=sources,
    )


@router.get("/changes/{change_id}")
def show_change(
    change_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> ChangeOut:
    scope = _audit(principal)
    return _change(_existing_change(session, change_id, scope))


def _existing_change(session: Session, change_id: int, scope: ZoneScope) -> ChangeView:
    """Eine Änderung im Bereich; eine außerhalb ist ununterscheidbar von einer unbekannten."""
    try:
        view = get_change(session, change_id)
    except ChangeNotFound:
        raise RoomApiError(404, "Änderung nicht gefunden.", "not_found") from None
    if not scope.covers(view.zone_id):
        raise RoomApiError(404, "Änderung nicht gefunden.", "not_found")
    return view


class RevertIn(BaseModel):
    force: bool = False
    force_token: str | None = None


class RevertOut(BaseModel):
    """`outcome`: `reverted` (neuer Eintrag in `change`) oder `already_effective` (die Wirkung
    war schon beendet; nur der Eintrag wurde als erledigt markiert, `change` ist dann der
    unveränderte Eintrag)."""

    outcome: str
    change: ChangeOut


@router.post("/changes/{change_id}/revert")
def revert(
    change_id: int,
    body: RevertIn,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> RevertOut:
    """Nimmt eine Änderung zurück. Ohne `force` bei Konflikt: 409 mit `conflicts` und
    `force_token`; mit `force` und genau diesem Token wird trotzdem zurückgesetzt."""
    scope = _audit(principal)
    if scope.zone_ids is not None:
        # Auf Räume beschränkt: der Eintrag muss im Bereich liegen, bevor irgendetwas über
        # ihn verraten wird (Konflikt, Zustand) -- sonst 404. Anlagenweit gibt es nichts zu
        # prüfen. Die schreibenden Rechte der Änderung prüft in beiden Fällen die Domäne.
        _existing_change(session, change_id, scope)
    if body.force and not hmac.compare_digest(
        body.force_token or "", confirmation_token(request, change_id)
    ):
        raise RoomApiError(
            403, "Der Bestätigungswert für das Erzwingen ist ungültig.", "force_token_invalid"
        )
    try:
        created = revert_change(
            session, principal, change_id, force=body.force, actor=_actor(principal)
        )
    except ChangeNotFound:
        raise RoomApiError(404, "Änderung nicht gefunden.", "not_found") from None
    except Forbidden:
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden") from None
    except ChangeConflict as exc:
        raise RoomApiError(
            409,
            str(exc),
            "conflict",
            extra={
                "conflicts": [
                    {"field": c.field, "current": c.current, "expected": c.expected}
                    for c in exc.conflicts
                ],
                "force_token": confirmation_token(request, change_id),
            },
        ) from None
    except ChangeAlreadyEffective:
        # Kein Fehler: die Markierung am Eintrag soll bestehen bleiben, also normal
        # zurückkehren (die Transaktion wird dann abgeschlossen).
        return RevertOut(
            outcome="already_effective", change=_change(get_change(session, change_id))
        )
    except ChangeAlreadyReverted as exc:
        raise RoomApiError(409, str(exc), "already_reverted") from None
    except ChangeNotRevertible as exc:
        raise RoomApiError(409, str(exc), "not_revertible") from None
    except ChangeNotPossible as exc:
        raise RoomApiError(409, str(exc), "not_possible") from None
    except ParameterOutOfRange as exc:
        raise RoomApiError(422, str(exc), "revert_invalid") from None
    except (ScheduleError, PolicyError) as exc:
        # `ScheduleError` ist ein Dataclass-`Exception` ohne `args`: `str()` wäre leer.
        raise RoomApiError(422, exc.notice, "revert_invalid", exc.field) from None
    return RevertOut(outcome="reverted", change=_change(created))
