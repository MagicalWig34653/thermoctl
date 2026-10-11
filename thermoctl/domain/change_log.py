"""Das Änderungsprotokoll: jede schreibende Änderung mit Vorher/Nachher, einzeln zurücknehmbar.

Eine Domänenfunktion je Operation, drei dünne Adapter darüber (Grundsatz 6). Die
Schreibfunktionen hier **umhüllen** die vorhandenen Domänenfunktionen -- Vorher lesen,
ausführen, Nachher lesen, Eintrag in derselben Transaktion schreiben --, sie ersetzen
sie nicht: Grenzen, Validierung und Audit-Log bleiben die der Domäne. Schlägt die
Domänenfunktion fehl, entsteht kein Eintrag; eine Änderung, die nichts ändert (derselbe
Wert, derselbe Platz), schreibt ebenfalls keinen.

Die Rücknahme läuft über dieselben Domänenfunktionen wie die ursprüngliche Änderung und
umgeht nichts. Reihenfolge in `revert_change`, und sie ist Absicht: erst die Rechte (wer
nichts darf, erfährt nichts über den Zustand), dann „nicht zurücknehmbar“, dann „schon
zurückgenommen“, dann der Konflikt, dann die Ausführung. Die Anlage steuert eine echte
Heizung (Grundsatz 7): Eine Rücknahme ist eine Änderung wie jede andere.

Werte stehen als kanonische Zeichenketten im Protokoll (`canonical`): Decimal ohne
überflüssige Nullen, bool als `true`/`false`, `None` als `None`, Zeitpunkte als ISO-UTC auf
die Sekunde. Es werden nur die Felder gespeichert, die sich tatsächlich geändert haben.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.base import utcnow
from thermoctl.db.models.change_log import ChangeLog, ChangeLogField
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.vacation import Vacation
from thermoctl.db.models.zone import Zone
from thermoctl.domain import control as control_domain
from thermoctl.domain import remote_control as remote_control_domain
from thermoctl.domain import schedule as schedule_domain
from thermoctl.domain import zone_settings as zone_settings_domain
from thermoctl.domain.authz import require
from thermoctl.domain.device_commands import naive_utc
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import Boost
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    Profile,
    ProfileValues,
    list_backup_offset_candidates,
    read_defaults,
    read_profile,
)

CONTROL_PARAMETER = "control_parameter"
SCHEDULE_POINT = "schedule_point"
OVERRIDE = "override"
BOOST = "boost"
VACATION = "vacation"
SENSOR_FAILURE_DEFAULTS = "sensor_failure_defaults"
SENSOR_FAILURE_POLICY = "sensor_failure_policy"
OVERRIDE_CANCEL = "override_cancel"
VACATION_CANCEL = "vacation_cancel"
FORCE_DRY_RUN = "force_dry_run"

CHANGE_TYPES: tuple[str, ...] = (
    CONTROL_PARAMETER,
    SCHEDULE_POINT,
    OVERRIDE,
    BOOST,
    VACATION,
    SENSOR_FAILURE_DEFAULTS,
    SENSOR_FAILURE_POLICY,
    OVERRIDE_CANCEL,
    VACATION_CANCEL,
    FORCE_DRY_RUN,
)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

REASON_REVERT = "Eine Rücknahme wird nicht erneut zurückgenommen; setzen Sie den Wert erneut."
REASON_OVERRIDE_CANCEL = (
    "Eine beendete Übersteuerung wird nicht wiederhergestellt; legen Sie sie bei Bedarf neu an."
)
REASON_VACATION_CANCEL = (
    "Ein vorzeitig beendeter Urlaub wird nicht wiederhergestellt; legen Sie ihn bei Bedarf neu an."
)
REASON_FORCE_DRY_RUN = (
    "Das Scharfschalten ist bewusst keine Rücknahme: Es bleibt ein Handgriff in der "
    "Weboberfläche oder über die REST-Schnittstelle."
)

_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_BOOLEAN_PARAMETERS = ("valve_protection_enabled", "pi_enabled")
_CURVE_PREFIX = "curve_point:"
_OFFSET_PREFIX = "backup_offset:"

type Fields = dict[str, str | None]
type Change = tuple[str, str | None, str | None]


@dataclass(frozen=True)
class Actor:
    """Wer handelt und über welchen Adapter -- genau das, was ins Protokoll gehört."""

    user_id: int | None
    token_id: int | None
    source: str


@dataclass(frozen=True)
class Recorded[T]:
    """Was die Domänenfunktion zurückgegeben hat, plus die Id des Protokolleintrags.

    `change_id` ist `None`, wenn die Änderung nichts verändert hat (kein Eintrag).
    """

    result: T
    change_id: int | None


# --- Fehler -----------------------------------------------------------------------------


class ChangeLogError(ValueError):
    """Basis aller Fehler des Änderungsprotokolls; die Meldung ist für Menschen gedacht."""


class ChangeNotFound(ChangeLogError):
    """Es gibt keinen Eintrag mit dieser Id."""


class ChangeNotRevertible(ChangeLogError):
    """Diese Art von Änderung (oder eine Rücknahme selbst) lässt sich nicht zurücknehmen."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Diese Änderung lässt sich nicht zurücknehmen: {reason}")
        self.reason = reason


class ChangeAlreadyReverted(ChangeLogError):
    """Der Eintrag wurde schon zurückgenommen."""


class ChangeAlreadyEffective(ChangeLogError):
    """Die Rücknahme ist gegenstandslos: Die Wirkung ist ohnehin schon beendet.

    Kein Fehlerzustand. Geschrieben wird nichts außer der Markierung am Eintrag; die
    Markierung bleibt nur bestehen, wenn der Aufrufer die Transaktion trotz dieser
    Ausnahme abschließt.
    """


class ChangeNotPossible(ChangeLogError):
    """Das Ziel der Änderung gibt es nicht mehr (Zone gelöscht, Punkt entfernt)."""


@dataclass(frozen=True)
class FieldConflict:
    field: str
    current: str | None
    expected: str | None


class ChangeConflict(ChangeLogError):
    """Der aktuelle Wert ist nicht mehr der, den die Änderung gesetzt hat."""

    def __init__(self, conflicts: tuple[FieldConflict, ...]) -> None:
        parts = "; ".join(
            f"{c.field} ist jetzt {_shown(c.current)}, die Änderung hatte {_shown(c.expected)} "
            "gesetzt"
            for c in conflicts
        )
        super().__init__(
            f"Zurücknehmen nicht ohne Weiteres möglich, der Wert wurde inzwischen geändert: "
            f"{parts}. Mit force trotzdem zurücksetzen."
        )
        self.conflicts = conflicts

    @property
    def current(self) -> str:
        return "; ".join(f"{c.field}={_shown(c.current)}" for c in self.conflicts)

    @property
    def expected(self) -> str:
        return "; ".join(f"{c.field}={_shown(c.expected)}" for c in self.conflicts)


def _shown(value: str | None) -> str:
    return "leer" if value is None else value


# --- Kanonische Zeichenkettenform ---------------------------------------------------------


def canonical(value: object) -> str | None:
    """Die eine Schreibweise, in der ein Wert im Protokoll steht.

    Zwei Lesungen desselben Werts (aus der Sitzung, aus der Datenbank) müssen gleich
    aussehen, sonst meldet die Konfliktprüfung Konflikte, die keine sind: `Decimal("0.50")`
    und `Decimal("0.5")` sind beide `"0.5"`, und ein Zeitpunkt verliert die Mikrosekunden,
    die MariaDB ohnehin abschneidet.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(int(value))
        return format(value.normalize(), "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo is None else value.astimezone(UTC).replace(tzinfo=None)
        return moment.replace(microsecond=0).isoformat() + "Z"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _label_for(minute: int, weekday: int) -> str:
    return f"{_WEEKDAYS[weekday - 1]} {minute // 60:02d}:{minute % 60:02d}"


def _with_unit(value: str | None, unit: str | None) -> str:
    if value is None:
        return "geerbt"
    return f"{value} {unit}" if unit else value


# --- Eintrag schreiben --------------------------------------------------------------------


def _source_id(session: Session, code: str) -> int:
    source_id = session.scalar(select(ActorSource.id).where(ActorSource.code == code))
    if source_id is None:
        # Wie `audit.record`: sonst ginge ein NULL in eine NOT-NULL-Spalte, und die
        # Meldung nennte den eigentlichen Fehler (ein Tippfehler im Adapter) nirgends.
        raise ValueError(f"Unbekannte Quelle {code!r}")
    return source_id


def _record(
    session: Session,
    actor: Actor,
    *,
    change_type: str,
    zone: Zone | None,
    object_ref: str | None,
    summary: str,
    changes: list[Change],
    revertible: bool = True,
    reason: str | None = None,
    reverts: ChangeLog | None = None,
    forced: bool = False,
) -> ChangeLog:
    entry = ChangeLog(
        occurred_at=utcnow(),
        source_id=_source_id(session, actor.source),
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        change_type=change_type,
        zone_id=zone.id if zone is not None else None,
        zone_name=zone.display_name if zone is not None else None,
        object_ref=object_ref,
        summary=summary,
        revertible=revertible,
        not_revertible_reason=None if revertible else reason,
        reverts_id=reverts.id if reverts is not None else None,
        forced=forced,
    )
    session.add(entry)
    session.flush()
    for field, before, after in changes:
        session.add(
            ChangeLogField(change_id=entry.id, field=field, before_value=before, after_value=after)
        )
    session.flush()
    return entry


def _diff(before: Fields, after: Fields) -> list[Change]:
    """Nur die Felder, deren Wert sich geändert hat."""
    return [
        (name, before.get(name), after.get(name))
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    ]


# --- Schreibfunktionen ---------------------------------------------------------------------


def set_parameter(
    session: Session, actor: Actor, zone: Zone, name: str, value: Decimal
) -> Recorded[Decimal]:
    """`zone_settings.set_parameter` mit Protokolleintrag. Gibt dasselbe zurück."""
    before = canonical(getattr(zone, name)) if name in zone_settings_domain.BY_NAME else None
    with session.begin_nested():
        result = zone_settings_domain.set_parameter(
            session,
            zone,
            name,
            value,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        after = canonical(getattr(zone, name))
        entry = None
        if before != after:
            description = zone_settings_domain.BY_NAME[name]
            entry = _record(
                session,
                actor,
                change_type=CONTROL_PARAMETER,
                zone=zone,
                object_ref=None,
                summary=(
                    f"Regelparameter „{description.label}“ der Zone „{zone.display_name}“ "
                    f"von {_with_unit(before, description.unit)} auf "
                    f"{_with_unit(after, description.unit)} geändert"
                ),
                changes=[(name, before, after)],
            )
    return Recorded(result, entry.id if entry is not None else None)


def move_point(
    session: Session, actor: Actor, zone: Zone, point: SchedulePoint, *, weekday: int, minute: int
) -> Recorded[SchedulePoint]:
    """`schedule.move_schedule_point` mit Protokolleintrag."""
    before_weekday, before_minute = point.weekday, point.minute_of_day
    with session.begin_nested():
        result = schedule_domain.move_schedule_point(
            session,
            zone,
            point,
            weekday=weekday,
            minute=minute,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        entry = None
        if (before_weekday, before_minute) != (point.weekday, point.minute_of_day):
            entry = _record(
                session,
                actor,
                change_type=SCHEDULE_POINT,
                zone=zone,
                object_ref=str(point.id),
                summary=(
                    f"Zeitplanpunkt der Zone „{zone.display_name}“ von "
                    f"{_label_for(before_minute, before_weekday)} auf "
                    f"{_label_for(point.minute_of_day, point.weekday)} verschoben"
                ),
                changes=_diff(
                    {"weekday": str(before_weekday), "minute_of_day": str(before_minute)},
                    {"weekday": str(point.weekday), "minute_of_day": str(point.minute_of_day)},
                ),
            )
    return Recorded(result, entry.id if entry is not None else None)


def _override_fields(entry: ZoneOverride) -> Fields:
    return {"temperature_c": canonical(entry.temperature_c), "ends_at": canonical(entry.ends_at)}


def create_override(
    session: Session,
    actor: Actor,
    zone: Zone,
    temperature_c: Decimal,
    ends_at: datetime | None,
    *,
    now: datetime | None = None,
) -> Recorded[ZoneOverride]:
    """`schedule.create_override` mit Protokolleintrag."""
    with session.begin_nested():
        result = schedule_domain.create_override(
            session,
            zone,
            temperature_c,
            ends_at,
            now=now,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        fields = _override_fields(result)
        until = "bis auf Weiteres" if result.ends_at is None else f"bis {fields['ends_at']}"
        entry = _record(
            session,
            actor,
            change_type=OVERRIDE,
            zone=zone,
            object_ref=str(result.id),
            summary=(
                f"Übersteuerung der Zone „{zone.display_name}“ auf "
                f"{fields['temperature_c']} °C {until} angelegt"
            ),
            changes=[(name, None, value) for name, value in fields.items()],
        )
    return Recorded(result, entry.id)


def boost(session: Session, actor: Actor, zone: Zone, now: datetime) -> Recorded[Boost]:
    """`remote_control.boost` mit Protokolleintrag (die entstandene Übersteuerung)."""
    with session.begin_nested():
        result = remote_control_domain.boost(
            session,
            zone,
            now,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        created = session.scalars(
            select(ZoneOverride)
            .where(ZoneOverride.zone_id == zone.id)
            .order_by(ZoneOverride.id.desc())
        ).first()
        assert created is not None, "boost hat keine Übersteuerung angelegt"
        fields = _override_fields(created)
        entry = _record(
            session,
            actor,
            change_type=BOOST,
            zone=zone,
            object_ref=str(created.id),
            summary=(
                f"Nächste Schaltung der Zone „{zone.display_name}“ vorgezogen "
                f"(Modus {result.mode_code}, {fields['temperature_c']} °C bis "
                f"{fields['ends_at']})"
            ),
            changes=[(name, None, value) for name, value in fields.items()],
        )
    return Recorded(result, entry.id)


def cancel_override(session: Session, actor: Actor, zone: Zone) -> Recorded[ZoneOverride | None]:
    """`schedule.cancel_override` mit Protokolleintrag -- nicht zurücknehmbar."""
    with session.begin_nested():
        result = schedule_domain.cancel_override(session, zone)
        entry = None
        if result is not None:
            entry = _record(
                session,
                actor,
                change_type=OVERRIDE_CANCEL,
                zone=zone,
                object_ref=str(result.id),
                summary=(
                    f"Übersteuerung der Zone „{zone.display_name}“ "
                    f"({canonical(result.temperature_c)} °C) beendet"
                ),
                changes=[("cancelled_at", None, canonical(result.cancelled_at))],
                revertible=False,
                reason=REASON_OVERRIDE_CANCEL,
            )
    return Recorded(result, entry.id if entry is not None else None)


def create_vacation(
    session: Session,
    actor: Actor,
    *,
    start_date: date,
    end_date: date,
    setback_temperature_c: Decimal,
    timezone_name: str,
    now: datetime | None = None,
) -> Recorded[Vacation]:
    """`schedule.create_vacation` mit Protokolleintrag."""
    with session.begin_nested():
        result = schedule_domain.create_vacation(
            session,
            start_date=start_date,
            end_date=end_date,
            setback_temperature_c=setback_temperature_c,
            timezone_name=timezone_name,
            now=now,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        fields: Fields = {
            "starts_at": canonical(result.starts_at),
            "ends_at": canonical(result.ends_at),
            "setback_temperature_c": canonical(result.setback_temperature_c),
        }
        entry = _record(
            session,
            actor,
            change_type=VACATION,
            zone=None,
            object_ref=str(result.id),
            summary=(
                f"Urlaubsbetrieb vom {start_date.isoformat()} bis {end_date.isoformat()} "
                f"mit {fields['setback_temperature_c']} °C angesetzt"
            ),
            changes=[(name, None, value) for name, value in fields.items()],
        )
    return Recorded(result, entry.id)


def cancel_vacation(
    session: Session, actor: Actor, *, now: datetime | None = None
) -> Recorded[Vacation | None]:
    """`schedule.cancel_vacation` mit Protokolleintrag -- nicht zurücknehmbar."""
    with session.begin_nested():
        result = schedule_domain.cancel_vacation(
            session, now=now, user_id=actor.user_id, token_id=actor.token_id, source=actor.source
        )
        entry = None
        if result is not None:
            entry = _record(
                session,
                actor,
                change_type=VACATION_CANCEL,
                zone=None,
                object_ref=str(result.id),
                summary="Urlaubsbetrieb vorzeitig beendet",
                changes=[("cancelled_at", None, canonical(result.cancelled_at))],
                revertible=False,
                reason=REASON_VACATION_CANCEL,
            )
    return Recorded(result, entry.id if entry is not None else None)


def force_dry_run(session: Session, actor: Actor, *, reason: str = "") -> Recorded[bool]:
    """`control.arm(False)` mit Protokolleintrag -- nicht zurücknehmbar.

    Der Rückgabewert ist, wie bei `arm`, ob sich etwas geändert hat.
    """
    with session.begin_nested():
        changed = control_domain.arm(
            session,
            False,
            reason=reason,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        entry = None
        if changed:
            suffix = f" (Grund: {reason.strip()})" if reason.strip() else ""
            entry = _record(
                session,
                actor,
                change_type=FORCE_DRY_RUN,
                zone=None,
                object_ref=None,
                summary=f"Regelung in den Trockenlauf zurückgenommen{suffix}",
                changes=[("control_armed", "true", "false")],
                revertible=False,
                reason=REASON_FORCE_DRY_RUN,
            )
    return Recorded(changed, entry.id if entry is not None else None)


def _defaults_state(session: Session, profile_id: int) -> Fields:
    profile = read_profile(session, profile_id)
    values = profile.values
    state: Fields = {
        "profile_name": values.name,
        "fixed_on_seconds": canonical(values.fixed_on_seconds),
        "fixed_off_seconds": canonical(values.fixed_off_seconds),
        "recovery_seconds": canonical(values.recovery_seconds),
        "recovery_samples": canonical(values.recovery_samples),
        "warm_restart_hysteresis_k": canonical(values.warm_restart_hysteresis_k),
        "emergency_setpoint_c": canonical(read_defaults(session).emergency_setpoint_c),
    }
    for point in values.curve_points:
        state[f"{_CURVE_PREFIX}{canonical(point.outdoor_c)}"] = (
            f"{point.on_seconds}:{point.off_seconds}"
        )
    return state


def save_sensor_failure_defaults(
    session: Session,
    actor: Actor,
    values: ProfileValues,
    *,
    profile_id: int,
    emergency_setpoint_c: Decimal,
) -> Recorded[Profile]:
    """`control.save_sensor_failure_plant_defaults` mit Protokolleintrag.

    Die Kennlinie steht je Punkt als eigenes Feld `curve_point:<Außentemperatur>` mit
    `<ein>:<aus>` (Sekunden); ein entfernter Punkt hat Nachher `None`.
    """
    with session.begin_nested():
        before = _defaults_state(session, profile_id)
        result = control_domain.save_sensor_failure_plant_defaults(
            session,
            values,
            profile_id=profile_id,
            emergency_setpoint_c=emergency_setpoint_c,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        changes = _diff(before, _defaults_state(session, profile_id))
        entry = None
        if changes:
            entry = _record(
                session,
                actor,
                change_type=SENSOR_FAILURE_DEFAULTS,
                zone=None,
                object_ref=str(profile_id),
                summary=(
                    "Anlagenweite Notbetriebsvorgaben geändert ("
                    + ", ".join(name for name, _b, _a in changes)
                    + ")"
                ),
                changes=changes,
            )
    return Recorded(result, entry.id if entry is not None else None)


def _offset_value(session: Session, assignment_id: int) -> str | None:
    row = session.get(ZoneDevice, assignment_id)
    return None if row is None else canonical(row.temperature_backup_offset_k)


def _policy_state(session: Session, zone: Zone, assignment_ids: list[int]) -> Fields:
    state: Fields = {
        "enabled": canonical(zone.sensor_failure_enabled),
        "profile_id": canonical(zone.sensor_failure_profile_id),
        "emergency_setpoint_c": canonical(zone.sensor_failure_emergency_setpoint_c),
    }
    for assignment_id in assignment_ids:
        state[f"{_OFFSET_PREFIX}{assignment_id}"] = _offset_value(session, assignment_id)
    return state


def save_sensor_failure_policy(
    session: Session,
    actor: Actor,
    zone: Zone,
    *,
    enabled: bool,
    profile_id: int | None = None,
    emergency_setpoint_c: Decimal | None = None,
    backup_offsets: Mapping[int, Decimal | None] | None = None,
) -> Recorded[None]:
    """Notbetriebs-Einstellung einer Zone samt Ausgleichswerten mit Protokolleintrag.

    Umhüllt `zone_settings.save_sensor_failure_parameters` und
    `control.save_sensor_failure_backup_offset`. Ausgleichswerte, die nicht zu einer
    Thermostat-Zuordnung dieser Zone gehören, weist die Funktion mit `ValueError` ab,
    bevor etwas geschrieben wird.
    """
    offsets = dict(backup_offsets or {})
    if offsets:
        valid = {c.zone_device_id for c in list_backup_offset_candidates(session, zone)}
        unknown = set(offsets) - valid
        if unknown:
            raise ValueError(
                f"Zuordnung(en) {sorted(unknown)} gehören nicht zu dieser Zone "
                "oder sind kein Thermostat-Aktor."
            )
    ids = sorted(offsets)
    with session.begin_nested():
        before = _policy_state(session, zone, ids)
        zone_settings_domain.save_sensor_failure_parameters(
            session,
            zone,
            enabled=enabled,
            profile_id=profile_id,
            emergency_setpoint_c=emergency_setpoint_c,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
        for assignment_id, value in offsets.items():
            control_domain.save_sensor_failure_backup_offset(
                session,
                assignment_id,
                value,
                user_id=actor.user_id,
                token_id=actor.token_id,
                source=actor.source,
            )
        changes = _diff(before, _policy_state(session, zone, ids))
        entry = None
        if changes:
            entry = _record(
                session,
                actor,
                change_type=SENSOR_FAILURE_POLICY,
                zone=zone,
                object_ref=None,
                summary=(
                    f"Notbetriebseinstellung der Zone „{zone.display_name}“ geändert ("
                    + ", ".join(name for name, _b, _a in changes)
                    + ")"
                ),
                changes=changes,
            )
    return Recorded(None, entry.id if entry is not None else None)


# --- Lesen ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldView:
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ChangeView:
    """Ein Eintrag mit Feldern und den Namen, die man zum Anzeigen braucht."""

    id: int
    occurred_at: datetime
    source: str
    user_id: int | None
    user_name: str | None
    token_id: int | None
    token_name: str | None
    change_type: str
    zone_id: int | None
    zone_name: str | None
    object_ref: str | None
    summary: str
    revertible: bool
    not_revertible_reason: str | None
    reverted_at: datetime | None
    reverted_by_id: int | None
    reverts_id: int | None
    forced: bool
    fields: tuple[FieldView, ...]


def _pairs[K, V](session: Session, query: Select[tuple[K, V]]) -> dict[K, V]:
    return {row[0]: row[1] for row in session.execute(query)}


def _views(session: Session, entries: list[ChangeLog]) -> list[ChangeView]:
    """Baut die Ansichten mit einer Abfrage je Nachschlagetabelle statt je Zeile."""
    if not entries:
        return []
    ids = [e.id for e in entries]
    fields_by_change: dict[int, list[FieldView]] = {i: [] for i in ids}
    for row in session.scalars(
        select(ChangeLogField).where(ChangeLogField.change_id.in_(ids)).order_by(ChangeLogField.id)
    ):
        fields_by_change[row.change_id].append(
            FieldView(row.field, row.before_value, row.after_value)
        )
    sources = _pairs(
        session,
        select(ActorSource.id, ActorSource.code).where(
            ActorSource.id.in_({e.source_id for e in entries})
        ),
    )
    user_ids = {e.actor_user_id for e in entries if e.actor_user_id is not None}
    users: dict[int, str] = {}
    if user_ids:
        users = _pairs(session, select(User.id, User.display_name).where(User.id.in_(user_ids)))
    token_ids = {e.actor_token_id for e in entries if e.actor_token_id is not None}
    tokens: dict[int, str] = {}
    if token_ids:
        tokens = _pairs(
            session, select(ApiToken.id, ApiToken.name).where(ApiToken.id.in_(token_ids))
        )
    zone_ids = {e.zone_id for e in entries if e.zone_id is not None}
    zones: dict[int, str] = {}
    if zone_ids:
        zones = _pairs(session, select(Zone.id, Zone.display_name).where(Zone.id.in_(zone_ids)))
    return [
        ChangeView(
            id=e.id,
            occurred_at=e.occurred_at,
            source=sources[e.source_id],
            user_id=e.actor_user_id,
            user_name=users.get(e.actor_user_id) if e.actor_user_id is not None else None,
            token_id=e.actor_token_id,
            token_name=tokens.get(e.actor_token_id) if e.actor_token_id is not None else None,
            change_type=e.change_type,
            zone_id=e.zone_id,
            zone_name=zones.get(e.zone_id, e.zone_name) if e.zone_id is not None else e.zone_name,
            object_ref=e.object_ref,
            summary=e.summary,
            revertible=e.revertible,
            not_revertible_reason=e.not_revertible_reason,
            reverted_at=e.reverted_at,
            reverted_by_id=e.reverted_by_id,
            reverts_id=e.reverts_id,
            forced=e.forced,
            fields=tuple(fields_by_change[e.id]),
        )
        for e in entries
    ]


def list_changes(
    session: Session,
    *,
    zone_id: int | None = None,
    change_type: str | None = None,
    source: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    include_reverted: bool = True,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[ChangeView]:
    """Die Einträge, neueste zuerst.

    `include_reverted=False` blendet die Einträge aus, die zurückgenommen wurden (auch
    die als gegenstandslos markierten); die Rücknahme-Einträge selbst bleiben stehen.
    `from_at` ist einschließlich, `to_at` ausschließlich. Naive Zeitpunkte gelten als UTC.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit muss zwischen 1 und {MAX_LIMIT} liegen")
    if offset < 0:
        raise ValueError("offset darf nicht negativ sein")
    query = select(ChangeLog)
    if zone_id is not None:
        query = query.where(ChangeLog.zone_id == zone_id)
    if change_type is not None:
        query = query.where(ChangeLog.change_type == change_type)
    if source is not None:
        query = query.where(
            ChangeLog.source_id.in_(select(ActorSource.id).where(ActorSource.code == source))
        )
    from_naive, to_naive = naive_utc(from_at), naive_utc(to_at)
    if from_naive is not None:
        query = query.where(ChangeLog.occurred_at >= from_naive)
    if to_naive is not None:
        query = query.where(ChangeLog.occurred_at < to_naive)
    if not include_reverted:
        query = query.where(ChangeLog.reverted_at.is_(None))
    query = query.order_by(ChangeLog.occurred_at.desc(), ChangeLog.id.desc())
    entries = list(session.scalars(query.limit(limit).offset(offset)))
    return _views(session, entries)


def get_change(session: Session, change_id: int) -> ChangeView:
    entry = session.get(ChangeLog, change_id)
    if entry is None:
        raise ChangeNotFound(f"Die Änderung {change_id} gibt es nicht.")
    return _views(session, [entry])[0]


# --- Zurücknehmen ---------------------------------------------------------------------------

# Welches Recht eine Rücknahme je Art verlangt (und damit auch, wer Einträge einer Art
# überhaupt ansprechen darf): das Recht der ursprünglichen Änderung und, wo die
# Rücknahme eine andere Domänenoperation ist, auch das Recht dieser Operation.
_REVERT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    CONTROL_PARAMETER: ("zone.manage",),
    SCHEDULE_POINT: ("schedule.manage",),
    OVERRIDE: ("override.cancel", "override.create"),
    BOOST: ("override.cancel", "override.create"),
    VACATION: ("vacation.manage",),
    SENSOR_FAILURE_DEFAULTS: ("setting.manage",),
    SENSOR_FAILURE_POLICY: ("zone.manage",),
    OVERRIDE_CANCEL: ("override.cancel",),
    VACATION_CANCEL: ("vacation.manage",),
    FORCE_DRY_RUN: ("control.disarm",),
}


def _required_permissions(entry: ChangeLog, fields: list[ChangeLogField]) -> list[str]:
    codes = list(_REVERT_PERMISSIONS[entry.change_type])
    if entry.change_type == SENSOR_FAILURE_POLICY and any(
        f.field.startswith(_OFFSET_PREFIX) for f in fields
    ):
        codes.append("device.manage")
    return codes


def _check_conflicts(current: Fields, fields: list[ChangeLogField], force: bool) -> None:
    conflicts = tuple(
        FieldConflict(f.field, current.get(f.field), f.after_value)
        for f in fields
        if current.get(f.field) != f.after_value
    )
    if conflicts and not force:
        raise ChangeConflict(conflicts)


@dataclass(frozen=True)
class _Context:
    session: Session
    actor: Actor
    entry: ChangeLog
    fields: list[ChangeLogField]
    zone: Zone | None
    force: bool

    def require_zone(self) -> Zone:
        if self.zone is None:
            raise ChangeNotPossible(
                f"Die Zone „{self.entry.zone_name or 'unbekannt'}“ gibt es nicht mehr; "
                "die Änderung lässt sich nicht zurücknehmen."
            )
        return self.zone


def _revert_control_parameter(ctx: _Context) -> list[Change] | None:
    zone = ctx.require_zone()
    field = ctx.fields[0]
    name = field.field
    current = canonical(getattr(zone, name))
    _check_conflicts({name: current}, ctx.fields, ctx.force)
    actor = ctx.actor
    if field.before_value is None:
        # „Geerbt“ wiederherstellen: set_parameter kennt nur feste Werte, der Weg für
        # „leer“ ist das Speichern aller Felder mit None an dieser Stelle.
        values: dict[str, Decimal | int | bool | None] = {
            n: getattr(zone, n) for n in zone_settings_domain.ControlParameters.__dataclass_fields__
        }
        values[name] = None
        zone_settings_domain.save_control_parameters(
            ctx.session,
            zone,
            values,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
    else:
        if name in _BOOLEAN_PARAMETERS:
            target = Decimal(1 if field.before_value == "true" else 0)
        else:
            target = Decimal(field.before_value)
        zone_settings_domain.set_parameter(
            ctx.session,
            zone,
            name,
            target,
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
    return [(name, current, canonical(getattr(zone, name)))]


def _revert_schedule_point(ctx: _Context) -> list[Change] | None:
    zone = ctx.require_zone()
    point = ctx.session.get(SchedulePoint, int(ctx.entry.object_ref or 0))
    if point is None or point.zone_id != zone.id:
        raise ChangeNotPossible(
            f"Der Zeitplanpunkt der Zone „{zone.display_name}“ existiert nicht mehr; "
            "die Änderung lässt sich nicht zurücknehmen."
        )
    before_state: Fields = {
        "weekday": str(point.weekday),
        "minute_of_day": str(point.minute_of_day),
    }
    _check_conflicts(before_state, ctx.fields, ctx.force)
    target = {f.field: f.before_value for f in ctx.fields}
    weekday = int(target.get("weekday") or point.weekday)
    minute = int(target.get("minute_of_day") or point.minute_of_day)
    schedule_domain.move_schedule_point(
        ctx.session,
        zone,
        point,
        weekday=weekday,
        minute=minute,
        user_id=ctx.actor.user_id,
        token_id=ctx.actor.token_id,
        source=ctx.actor.source,
    )
    after_state: Fields = {
        "weekday": str(point.weekday),
        "minute_of_day": str(point.minute_of_day),
    }
    return [(f.field, before_state[f.field], after_state[f.field]) for f in ctx.fields]


def _swapped(fields: list[ChangeLogField]) -> list[Change]:
    return [(f.field, f.after_value, f.before_value) for f in fields]


def _revert_override(ctx: _Context) -> list[Change] | None:
    zone = ctx.require_zone()
    row = ctx.session.get(ZoneOverride, int(ctx.entry.object_ref or 0))
    if row is None or row.zone_id != zone.id:
        raise ChangeNotPossible(
            f"Die Übersteuerung der Zone „{zone.display_name}“ existiert nicht mehr; "
            "die Änderung lässt sich nicht zurücknehmen."
        )
    now = utcnow()
    if row.cancelled_at is not None or (row.ends_at is not None and row.ends_at <= now):
        return None
    # Die Domänenfunktion beendet die jüngste nicht beendete Übersteuerung der Zone --
    # nicht zwingend diese. Ist es eine andere, die noch läuft, ist das ein Konflikt.
    latest = ctx.session.scalars(
        select(ZoneOverride)
        .where(ZoneOverride.zone_id == zone.id, ZoneOverride.cancelled_at.is_(None))
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()
    assert latest is not None, "die Übersteuerung ist weder beendet noch abgelaufen"
    if latest.id != row.id:
        newer_running = latest.ends_at is None or latest.ends_at > now
        if newer_running and not ctx.force:
            raise ChangeConflict(
                (
                    FieldConflict(
                        "override",
                        f"neuere Übersteuerung {latest.id} "
                        f"({canonical(latest.temperature_c)} °C) läuft",
                        f"Übersteuerung {row.id} läuft",
                    ),
                )
            )
        # Beendet wird diese Übersteuerung wie dort: durch `cancelled_at`, die Zeile bleibt.
        row.cancelled_at = now
    else:
        schedule_domain.cancel_override(ctx.session, zone)
    return _swapped(ctx.fields)


def _revert_vacation(ctx: _Context) -> list[Change] | None:
    row = ctx.session.get(Vacation, int(ctx.entry.object_ref or 0))
    if row is None:
        raise ChangeNotPossible(
            "Der Urlaub existiert nicht mehr; die Änderung lässt sich nicht zurücknehmen."
        )
    now = utcnow()
    if row.cancelled_at is not None or row.ends_at <= now:
        return None
    running = schedule_domain.current_or_upcoming_vacation(ctx.session, now)
    if running is None or running.id != row.id:
        raise ChangeConflict(
            (
                FieldConflict(
                    "vacation",
                    "ein anderer Urlaub läuft oder ist geplant",
                    f"Urlaub {row.id} läuft",
                ),
            )
        )
    schedule_domain.cancel_vacation(
        ctx.session,
        now=now,
        user_id=ctx.actor.user_id,
        token_id=ctx.actor.token_id,
        source=ctx.actor.source,
    )
    return _swapped(ctx.fields)


def _revert_sensor_failure_defaults(ctx: _Context) -> list[Change] | None:
    session = ctx.session
    profile_id = int(ctx.entry.object_ref or 0)
    current = _defaults_state(session, profile_id)
    _check_conflicts(current, ctx.fields, ctx.force)
    target = dict(current)
    for f in ctx.fields:
        if f.before_value is None:
            target.pop(f.field, None)
        else:
            target[f.field] = f.before_value
    curve = []
    for key, value in target.items():
        if key.startswith(_CURVE_PREFIX) and value is not None:
            on_seconds, off_seconds = value.split(":")
            curve.append(
                CurvePoint(
                    Decimal(key.removeprefix(_CURVE_PREFIX)), int(on_seconds), int(off_seconds)
                )
            )
    values = ProfileValues(
        name=target["profile_name"] or "",
        fixed_on_seconds=int(target["fixed_on_seconds"] or 0),
        fixed_off_seconds=int(target["fixed_off_seconds"] or 0),
        recovery_seconds=int(target["recovery_seconds"] or 0),
        recovery_samples=int(target["recovery_samples"] or 0),
        warm_restart_hysteresis_k=Decimal(target["warm_restart_hysteresis_k"] or 0),
        curve_points=tuple(sorted(curve, key=lambda p: p.outdoor_c)),
    )
    control_domain.save_sensor_failure_plant_defaults(
        session,
        values,
        profile_id=profile_id,
        emergency_setpoint_c=Decimal(target["emergency_setpoint_c"] or 0),
        user_id=ctx.actor.user_id,
        token_id=ctx.actor.token_id,
        source=ctx.actor.source,
    )
    after = _defaults_state(session, profile_id)
    return [(f.field, current.get(f.field), after.get(f.field)) for f in ctx.fields]


def _revert_sensor_failure_policy(ctx: _Context) -> list[Change] | None:
    zone = ctx.require_zone()
    session = ctx.session
    names = [f.field for f in ctx.fields]
    offset_ids = [
        int(n.removeprefix(_OFFSET_PREFIX)) for n in names if n.startswith(_OFFSET_PREFIX)
    ]
    current = _policy_state(session, zone, offset_ids)
    _check_conflicts(current, ctx.fields, ctx.force)
    target = {f.field: f.before_value for f in ctx.fields}
    enabled = zone.sensor_failure_enabled
    profile_id = zone.sensor_failure_profile_id
    setpoint = zone.sensor_failure_emergency_setpoint_c
    if "enabled" in target:
        enabled = target["enabled"] == "true"
    if "profile_id" in target:
        profile_id = None if target["profile_id"] is None else int(target["profile_id"])
    if "emergency_setpoint_c" in target:
        raw = target["emergency_setpoint_c"]
        setpoint = None if raw is None else Decimal(raw)
    actor = ctx.actor
    zone_settings_domain.save_sensor_failure_parameters(
        session,
        zone,
        enabled=enabled,
        profile_id=profile_id,
        emergency_setpoint_c=setpoint,
        user_id=actor.user_id,
        token_id=actor.token_id,
        source=actor.source,
    )
    for assignment_id in offset_ids:
        raw_offset = target[f"{_OFFSET_PREFIX}{assignment_id}"]
        control_domain.save_sensor_failure_backup_offset(
            session,
            assignment_id,
            None if raw_offset is None else Decimal(raw_offset),
            user_id=actor.user_id,
            token_id=actor.token_id,
            source=actor.source,
        )
    after = _policy_state(session, zone, offset_ids)
    return [(n, current.get(n), after.get(n)) for n in names]


_REVERTERS: dict[str, Callable[[_Context], list[Change] | None]] = {
    CONTROL_PARAMETER: _revert_control_parameter,
    SCHEDULE_POINT: _revert_schedule_point,
    OVERRIDE: _revert_override,
    BOOST: _revert_override,
    VACATION: _revert_vacation,
    SENSOR_FAILURE_DEFAULTS: _revert_sensor_failure_defaults,
    SENSOR_FAILURE_POLICY: _revert_sensor_failure_policy,
}


def revert_change(
    session: Session,
    principal: Principal,
    change_id: int,
    *,
    force: bool = False,
    actor: Actor,
) -> ChangeView:
    """Nimmt eine Änderung zurück und gibt den neuen Rücknahme-Eintrag zurück.

    Ausnahmen (alle `ChangeLogError`, außer `Forbidden` aus `authz`): `ChangeNotFound`,
    `ChangeNotRevertible`, `ChangeAlreadyReverted`, `ChangeNotPossible`, `ChangeConflict`,
    `ChangeAlreadyEffective`. Schlägt die Domänenfunktion der Rücknahme selbst fehl
    (Grenzen, Validierung), bleibt alles unverändert und ihre Ausnahme geht durch.
    """
    entry = session.get(ChangeLog, change_id)
    if entry is None:
        raise ChangeNotFound(f"Die Änderung {change_id} gibt es nicht.")
    fields = list(
        session.scalars(
            select(ChangeLogField)
            .where(ChangeLogField.change_id == entry.id)
            .order_by(ChangeLogField.id)
        )
    )
    # Zuerst die Rechte, gegen die Zone des Eintrags -- vor jeder Auskunft über den
    # Zustand (Konflikt, schon zurückgenommen), die sonst ohne Recht lesbar wäre.
    for code in _required_permissions(entry, fields):
        require(principal, code, entry.zone_id)
    if not entry.revertible:
        raise ChangeNotRevertible(entry.not_revertible_reason or "keine Angabe")
    if entry.reverted_at is not None:
        if entry.reverted_by_id is None:
            raise ChangeAlreadyReverted(
                f"Die Änderung {entry.id} ist bereits wirkungslos "
                "(sie war schon beendet) und wurde so abgeschlossen."
            )
        raise ChangeAlreadyReverted(
            f"Die Änderung {entry.id} wurde bereits zurückgenommen "
            f"(Eintrag {entry.reverted_by_id})."
        )
    zone = session.get(Zone, entry.zone_id) if entry.zone_id is not None else None
    context = _Context(session, actor, entry, fields, zone, force)
    now = utcnow()
    new_entry: ChangeLog | None = None
    with session.begin_nested():
        changes = _REVERTERS[entry.change_type](context)
        if changes is not None:
            new_entry = _record(
                session,
                actor,
                change_type=entry.change_type,
                zone=zone,
                object_ref=entry.object_ref,
                summary=f"Rücknahme: {entry.summary}",
                changes=changes,
                revertible=False,
                reason=REASON_REVERT,
                reverts=entry,
                forced=force,
            )
            entry.reverted_at = now
            entry.reverted_by_id = new_entry.id
            audit.record(
                session,
                source=actor.source,
                action="revert",
                object_type="change_log",
                object_id=str(new_entry.id),
                summary=f"Änderung {entry.id} zurückgenommen" + (" (erzwungen)" if force else ""),
                detail=entry.summary,
                user_id=actor.user_id,
                token_id=actor.token_id,
            )
    if new_entry is None:
        entry.reverted_at = now
        session.flush()
        raise ChangeAlreadyEffective(
            "Die Wirkung der Änderung ist bereits beendet; es gibt nichts zurückzunehmen."
        )
    return _views(session, [new_entry])[0]
