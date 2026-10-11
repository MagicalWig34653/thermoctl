import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import wraps
from importlib import import_module
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.auth.tokens import resolve_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.device import Device, DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability, Integration, SensorStatus
from thermoctl.db.models.measurement import DeviceHealth
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import change_log as change_log_domain
from thermoctl.domain.authz import (
    Forbidden,
    has_permission,
    principal_for_token,
    require,
    visible_zones,
)
from thermoctl.domain.change_log import Actor, ChangeAlreadyEffective, ChangeLogError, ChangeView
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    settings,
)
from thermoctl.domain.device_commands import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    CommandLogEntry,
    list_commands,
    naive_utc,
)
from thermoctl.domain.modes import DomainError
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import RemoteControlError
from thermoctl.domain.schedule import (
    ScheduleError,
    current_or_upcoming_vacation,
    resolved_setpoint,
)
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    list_backup_offset_candidates,
    migration_default_profile_id,
    read_defaults,
    read_profile,
)
from thermoctl.domain.zone_history_series import (
    DEFAULT_HOURS,
    DEFAULT_POINTS,
    HistoryRangeError,
    HistorySeries,
    history_series,
    resolve_window,
)
from thermoctl.domain.zone_settings import (
    PARAMETERS,
    ParameterOutOfRange,
    UnknownParameter,
    control_parameters,
    sensor_failure_parameters,
)
from thermoctl.mcp.http_auth import current_key_prefix
from thermoctl.services.emergency_state import zone_emergency_view

log = logging.getLogger("thermoctl.mcp")

# Ausnahmen, mit denen die Domäne eine Eingabe abweist ("so nicht", kein Fehler des Dienstes).
# Die REST-Schnittstelle gibt ihren Text als 4xx weiter; hier gilt dasselbe, sonst erführe ein
# Modell nie, warum etwas abgelehnt wurde. Die Meldungen formuliert die Domäne selbst.
_REJECTIONS = (
    ChangeLogError,
    ControlError,
    DomainError,
    HistoryRangeError,
    ParameterOutOfRange,
    PolicyError,
    RemoteControlError,
    ScheduleError,
    UnknownParameter,
)


def _reason(error: Exception) -> str:
    """Der Text, den die Domäne für die Ablehnung formuliert hat.

    Mehrere Domänenfehler sind Dataklassen mit `field` und `notice` und haben kein eigenes
    `__str__`; `str(error)` ergäbe dort das Argument-Tupel statt des lesbaren Satzes.
    """
    notice = getattr(error, "notice", None)
    return notice if isinstance(notice, str) else str(error)


def _log_tool(name: str, error: Exception | None) -> None:
    """Ein Eintrag je Werkzeugaufruf: Werkzeug, Token-Präfix, Ergebnis, bei Fehlern der Grund.

    Der Grund ist die Meldung der Domäne (z. B. "Recht zone.manage fehlt"), nie das Token.
    """
    if error is None:
        outcome = "ok"
    elif isinstance(error, (Forbidden, PermissionError)):
        outcome = "denied"
    elif isinstance(error, _REJECTIONS):
        outcome = "rejected"
    elif isinstance(error, LookupError):
        outcome = "not_found"
    else:
        outcome = "error"
    extra: dict[str, object] = {
        "event": "mcp.tool",
        "tool": name,
        "outcome": outcome,
        "key_prefix": current_key_prefix.get(),
    }
    if error is not None:
        # Nur Meldungen, die die Domäne selbst formuliert (fehlendes Recht, unbekannte
        # Zone), kommen ins Protokoll. Andere Ausnahmen können Eingaben des Clients
        # enthalten -- ein dort eingefügtes Token oder Geheimnis gehört nicht ins Log.
        extra["reason"] = (
            _reason(error)
            if isinstance(error, (Forbidden, PermissionError, LookupError, *_REJECTIONS))
            else type(error).__name__
        )
    log.info("mcp.tool", extra=extra)


class _McpServer(Protocol):
    def tool(
        self, name: str | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...

    def run(self, transport: str = "stdio", **kwargs: Any) -> None: ...


def _log_in(session: Session, plaintext: str) -> tuple[ApiToken, Principal]:
    token = resolve_token(session, plaintext)
    if token is None:
        raise PermissionError("Ungueltiges oder nicht mehr gueltiges MCP-Token")
    if token.is_kiosk:
        # Same reason as in the REST adapter: the kiosk page is what keeps a wall
        # tablet's token harmless, and MCP offers the unrestricted override the
        # kiosk deliberately does not.
        raise PermissionError("Kiosk-Token sind nur an der Kioskoberflaeche gueltig")
    return token, principal_for_token(session, token)


def _visible_zone(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = next(
        (
            entry
            for entry in visible_zones(session, principal, "zone.read")
            if entry.id == zone_id
        ),
        None,
    )
    if zone is None:
        raise LookupError("Zone nicht gefunden")
    require(principal, "zone.read", zone_id)
    return zone


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _actor(principal: Principal, token: ApiToken) -> Actor:
    """Wer im Änderungsprotokoll steht: der Besitzer des Tokens und das Token selbst."""
    return Actor(principal.user_id, token.id, "mcp")


def _resolved_default_profile_id(session: Session) -> int:
    defaults = read_defaults(session)
    return (
        defaults.profile_id
        if defaults.profile_id is not None
        else migration_default_profile_id(session)
    )


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def list_zones(session: Session, plaintext: str) -> list[dict[str, object]]:
    """Lists only the zones visible for the token."""
    _token, principal = _log_in(session, plaintext)
    zones = visible_zones(session, principal, "zone.read")
    if not zones:
        require(principal, "zone.read")
    return [
        {
            "name": zone.name,
            "display_name": zone.display_name,
            "operating_mode": zone.operating_mode.code,
            "solar_gain_factor": str(zone.solar_gain_factor.quantize(Decimal("0.01"))),
        }
        for zone in zones
    ]


def zone_state(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Returns the most recently derived state of a visible zone."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    row = session.get(ZoneState, zone.id)
    if row is None:
        return {
            "temperature_c": None,
            "measured_at": None,
            "sensor_state": None,
            "sensor_stuck": False,
        }
    status = session.get(SensorStatus, row.sensor_status_id)
    return {
        "temperature_c": _decimal(row.temperature_c),
        "measured_at": _moment(row.measured_at),
        "sensor_state": None if status is None else status.code,
        # Independent of `sensor_state` above -- see
        # `domain/fault_notice.py::stuck_sensor_notice` for why the two never both
        # describe a problem for the same zone at once.
        "sensor_stuck": row.sensor_stuck,
    }


def explain_setpoint(
    session: Session, plaintext: str, zone_id: int, now: datetime | None = None
) -> dict[str, object]:
    """Passes through the value and reasoning of the shared setpoint logic."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    setpoint = resolved_setpoint(session, zone, now or utcnow())
    return {
        "temperature_c": _decimal(setpoint.temperature_c),
        "reason": setpoint.reason,
        "mode": setpoint.mode_code,
    }


def read_schedule(session: Session, plaintext: str, zone_id: int) -> list[dict[str, object]]:
    """Reads the schedule points of a visible zone along with mode names."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    rows = session.execute(
        select(SchedulePoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == SchedulePoint.setpoint_mode_id)
        .where(SchedulePoint.zone_id == zone.id)
        .order_by(SchedulePoint.weekday, SchedulePoint.minute_of_day)
    )
    return [
        {
            "weekday": point.weekday,
            "minute_of_day": point.minute_of_day,
            "mode": mode.name,
        }
        for point, mode in rows
    ]


def read_setpoints(session: Session, plaintext: str, zone_id: int) -> list[dict[str, object]]:
    """Reads the setpoints set for a visible zone, per mode."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.read", zone.id)
    rows = session.execute(
        select(ZoneSetpoint, SetpointMode)
        .join(SetpointMode, SetpointMode.id == ZoneSetpoint.setpoint_mode_id)
        .where(ZoneSetpoint.zone_id == zone.id)
        .order_by(SetpointMode.sort_order, SetpointMode.code)
    )
    return [
        {"mode": mode.name, "temperature_c": _decimal(setpoint.temperature_c)}
        for setpoint, mode in rows
    ]


def list_devices(session: Session, plaintext: str) -> list[dict[str, object]]:
    """Lists devices along with integration, capabilities, and signs of life."""
    _token, principal = _log_in(session, plaintext)
    require(principal, "device.read")
    rows = session.execute(
        select(Device, Integration, DeviceHealth)
        .join(Integration, Integration.id == Device.integration_id)
        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
        .order_by(Device.display_name, Device.external_id)
    ).all()
    capabilities = session.execute(
        select(DeviceCapabilityLink.device_id, DeviceCapability.code)
        .join(DeviceCapability, DeviceCapability.id == DeviceCapabilityLink.capability_id)
        .order_by(DeviceCapability.code)
    ).all()
    by_device: dict[int, list[str]] = {}
    for device_id, code in capabilities:
        by_device.setdefault(device_id, []).append(code)
    return [
        {
            "name": device.display_name,
            "integration": integration.code,
            "capabilities": by_device.get(device.id, []),
            "letzte_nachricht": None if healthy is None else _moment(healthy.last_payload_at),
            "batterie_prozent": None if healthy is None else _decimal(healthy.battery_percent),
        }
        for device, integration, healthy in rows
    ]


def shadow_decisions(
    session: Session, plaintext: str, zone_id: int, count: int = 10
) -> list[dict[str, object]]:
    """Returns the most recent, reasoned shadow decisions of a zone."""
    if count < 1 or count > 100:
        raise ValueError("Anzahl muss zwischen 1 und 100 liegen")
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    rows = session.scalars(
        select(ShadowDecision)
        .where(ShadowDecision.zone_id == zone.id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(count)
    )
    return [
        {
            "moment": _moment(row.decided_at),
            "ist_c": _decimal(row.temperature_c),
            "soll_c": _decimal(row.setpoint_c),
            "setpoint_reason": row.setpoint_reason,
            "would_heat": row.would_heat,
            "outcome": row.outcome_code,
            "reason": row.reason,
        }
        for row in rows
    ]


def _moment_utc(value: datetime | None) -> str | None:
    """Like `_moment`, but never ambiguous about the zone.

    Every other timestamp in this module reports naive UTC as a bare `isoformat()`,
    which reads as local time to a caller in a different zone. The command log is
    the one place a language model may be asked to reconcile against an
    incident report someone else wrote down in their own zone, so it always
    carries the offset explicitly.
    """
    return None if value is None else value.replace(tzinfo=UTC).isoformat()


def _command_entry(entry: CommandLogEntry) -> dict[str, object]:
    """Ein Eintrag des Schaltprotokolls in der Form, die `device_commands` und
    `temperature_history` gemeinsam ausgeben."""
    return {
        "sent_at": _moment_utc(entry.sent_at),
        "source": entry.source,
        "zone": entry.zone_name,
        "device": entry.device_name,
        "command": entry.command,
        "payload": entry.payload,
        "outcome": entry.outcome,
        "error": entry.error,
        "reason": entry.reason,
        "entry_kind": entry.entry_kind,
        "simulated": entry.simulated,
    }


def device_commands(
    session: Session,
    plaintext: str,
    zone: str | None = None,
    outcome: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, object]]:
    """Reads the actuator command log -- the same protocol as `/device-commands`.

    `audit.read`, not a zone permission: like the web view and the REST endpoint,
    this is a protocol over the whole plant, not over a single zone.
    """
    _token, principal = _log_in(session, plaintext)
    require(principal, "audit.read")
    entries = list_commands(
        session,
        zone_name=zone,
        from_at=naive_utc(from_at),
        to_at=naive_utc(to_at),
        outcome=outcome,
        limit=limit,
    )
    return [_command_entry(entry) for entry in entries]


SWITCHING_LOG_NOTE = "Das Schaltprotokoll braucht das Recht audit.read; dieses Token hat es nicht."


def _history_points(series: HistorySeries) -> list[dict[str, object]]:
    return [
        {
            "at": point.at.isoformat(),
            "measured_c": point.measured_c,
            "measured_min_c": point.measured_min_c,
            "measured_max_c": point.measured_max_c,
            "setpoint_c": point.setpoint_c,
            "scheduled_setpoint_c": point.scheduled_setpoint_c,
            "solar_setback_k": point.solar_setback_k,
            "heat_share": point.heat_share,
        }
        for point in series.points
    ]


def temperature_history(
    session: Session,
    plaintext: str,
    zone_id: int,
    hours: int = DEFAULT_HOURS,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    max_points: int = DEFAULT_POINTS,
    log_limit: int = DEFAULT_LIMIT,
) -> dict[str, object]:
    """Temperaturverlauf einer sichtbaren Zone, samt Schaltprotokoll dieser Zone.

    `zone.read` für die Zone. Das Schaltprotokoll ist ein Protokoll über die ganze Anlage und
    gehört deshalb nur dazu, wenn das Token `audit.read` hat; sonst ist `switching_log` null
    und `switching_log_note` sagt warum -- die Messreihe kommt trotzdem.
    """
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    start, end = resolve_window(hours=hours, from_at=from_at, to_at=to_at, now=utcnow())
    if log_limit < 1 or log_limit > MAX_LIMIT:
        raise HistoryRangeError(f"log_limit muss zwischen 1 und {MAX_LIMIT} liegen")
    series = history_series(session, zone.id, start, end, max_points)
    summary = series.summary

    switching_log: list[dict[str, object]] | None = None
    note: str | None = SWITCHING_LOG_NOTE
    if has_permission(principal, "audit.read"):
        # Das Schaltprotokoll führt die Zone unter ihrem Anzeigenamen (Momentaufnahme).
        entries = list_commands(
            session, zone_name=zone.display_name, from_at=start, to_at=end, limit=log_limit
        )
        switching_log = [_command_entry(entry) for entry in entries]
        note = None
    return {
        "zone": zone.name,
        "display_name": zone.display_name,
        "from": _moment_utc(start),
        "to": _moment_utc(end),
        "bucket_minutes": series.bucket_minutes,
        "summary": {
            "sample_count": summary.sample_count,
            "first_at": summary.first_at.isoformat() if summary.first_at else None,
            "last_at": summary.last_at.isoformat() if summary.last_at else None,
            "measured_min_c": summary.measured_min_c,
            "measured_avg_c": summary.measured_avg_c,
            "measured_max_c": summary.measured_max_c,
            "heat_share": summary.heat_share,
            "buckets_without_data": summary.buckets_without_data,
        },
        "points": _history_points(series),
        "switching_log": switching_log,
        "switching_log_note": note,
    }


def read_emergency_state(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Reads a visible zone's Notbetrieb state -- the MCP counterpart of
    `GET /api/v1/zones/{zone_id}/emergency-state` (Auftrag 8b item 3), same
    permission (`zone.read`) and zone isolation, same shared read
    (`services/emergency_state.zone_emergency_view`, Grundsatz 6).
    """
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    settings_row = session.get(Setting, 1)
    view = zone_emergency_view(session, zone, utcnow(), settings_row)
    return {
        "zone_id": view.zone_id,
        "zone_name": view.zone_name,
        "stage": view.stage,
        "stage_label": view.stage_label,
        "episode_id": view.episode_id,
        "failure_started_at": _moment(view.failure_started_at),
        "active_source_device_id": view.active_source_device_id,
        "active_source_device_name": view.active_source_device_name,
        "source_measured_at": _moment(view.source_measured_at),
        "sensor_timeout_seconds": view.sensor_timeout_seconds,
        "recovery_started_at": _moment(view.recovery_started_at),
        "recovery_sample_count": view.recovery_sample_count,
        "recovery_samples": view.recovery_samples,
        "emergency_setpoint_c": _decimal(view.emergency_setpoint_c),
        "outdoor_c": _decimal(view.outdoor.temperature_c) if view.outdoor is not None else None,
        "outdoor_status": view.outdoor.status if view.outdoor is not None else None,
        "banner_headline": view.banner.headline if view.banner is not None else None,
        "banner_detail": view.banner.detail if view.banner is not None else None,
        "actuators": [
            {
                "device_id": actuator.device_id,
                "device_name": actuator.device_name,
                "kind": actuator.kind,
                "phase": actuator.phase,
                "phase_deadline_at": _moment(actuator.phase_deadline_at),
                "on_seconds": actuator.on_seconds,
                "off_seconds": actuator.off_seconds,
                "cycle_source": actuator.cycle_source,
                "handover_attempted": actuator.handover_attempted,
                "handover_result": actuator.handover_result,
                "handover_status_text": actuator.handover_status_text,
                "restore_attempted": actuator.restore_attempted,
                "restore_result": actuator.restore_result,
                "restore_status_text": actuator.restore_status_text,
            }
            for actuator in view.actuators
        ],
        "comparisons": [
            {
                "device_id": comparison.device_id,
                "device_name": comparison.device_name,
                "sample_count": comparison.sample_count,
                "mean_deviation_k": _decimal(comparison.mean_deviation_k),
                "suggested_offset_text": comparison.suggested_offset_text,
            }
            for comparison in view.comparisons
        ],
    }


def override_zone(
    session: Session,
    plaintext: str,
    zone_id: int,
    temperature_c: Decimal,
    ends_at: datetime | None = None,
) -> dict[str, object]:
    """Creates an override via the shared domain function."""
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.create", zone_id)
    recorded = change_log_domain.create_override(
        session, _actor(principal, token), zone, temperature_c, ends_at
    )
    entry = recorded.result
    return {
        "zone": zone.name,
        "temperature_c": _decimal(entry.temperature_c),
        "ends_at": _moment(entry.ends_at),
        "change_id": recorded.change_id,
    }


def cancel_override(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Cancels the active override via the shared domain function."""
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.cancel", zone_id)
    recorded = change_log_domain.cancel_override(session, _actor(principal, token), zone)
    return {
        "zone": zone.name,
        "cancelled": recorded.result is not None,
        "change_id": recorded.change_id,
    }


def read_vacation(session: Session, plaintext: str) -> dict[str, object] | None:
    """The plant-wide vacation currently running or still upcoming, or `None`.

    `zone.read`, the same right the equivalent REST route uses: seeing whether the
    plant is about to run down for an absence is part of "seeing the plant", not a
    separate management capability.
    """
    _token, principal = _log_in(session, plaintext)
    require(principal, "zone.read")
    entry = current_or_upcoming_vacation(session, utcnow())
    if entry is None:
        return None
    return {
        "starts_at": _moment(entry.starts_at),
        "ends_at": _moment(entry.ends_at),
        "setback_temperature_c": _decimal(entry.setback_temperature_c),
    }


def set_vacation(
    session: Session,
    plaintext: str,
    start_date: date,
    end_date: date,
    setback_temperature_c: Decimal,
) -> dict[str, object]:
    """Sets the plant-wide vacation via the shared domain function.

    Its own permission `vacation.manage`, not `override.create`: a vacation covers
    every zone at once, an override only the one it names -- see the reasoning in
    `db/models/lookup.py`. A useful case for a language model in particular: "I'll be
    away from the 23rd to Epiphany, turn the heating down to 15" needs no per-zone
    reasoning at all.
    """
    token, principal = _log_in(session, plaintext)
    require(principal, "vacation.manage")
    row = settings(session)
    recorded = change_log_domain.create_vacation(
        session,
        _actor(principal, token),
        start_date=start_date,
        end_date=end_date,
        setback_temperature_c=setback_temperature_c,
        timezone_name=row.timezone,
    )
    entry = recorded.result
    return {
        "starts_at": _moment(entry.starts_at),
        "ends_at": _moment(entry.ends_at),
        "setback_temperature_c": _decimal(entry.setback_temperature_c),
        "change_id": recorded.change_id,
    }


def cancel_vacation(session: Session, plaintext: str) -> dict[str, object]:
    """Ends the running or planned vacation early via the shared domain function."""
    token, principal = _log_in(session, plaintext)
    require(principal, "vacation.manage")
    recorded = change_log_domain.cancel_vacation(session, _actor(principal, token))
    return {"cancelled": recorded.result is not None, "change_id": recorded.change_id}


def boost(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Pulls the next switch forward -- via the shared domain function.

    The same permission as an override, because it is one: just one whose value and end
    are determined by the schedule instead of the caller. For a language model this is
    the more reliable form of "make it warmer here" -- it has to guess neither a
    temperature nor a duration, and after the schedule point the intervention clears
    itself away.
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.create", zone_id)
    recorded = change_log_domain.boost(session, _actor(principal, token), zone, utcnow())
    result = recorded.result
    return {
        "zone": zone.name,
        "mode": result.mode_code,
        "temperature_c": _decimal(result.temperature),
        "valid_until": _moment(result.bis),
        "change_id": recorded.change_id,
    }


def read_control_parameters(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """The effective control parameters of a zone, along with their limits.

    The limits are included in the response because a language model would otherwise
    have to guess them: without them every write attempt would be a shot in the dark,
    and "0.05 kelvin hysteresis" would look just as plausible to a model as "0.5".
    """
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    effective = control_parameters(session, zone)
    return {
        "zone": zone.name,
        "parameter": [
            {
                "name": description.name,
                "label": description.label,
                "unit": description.unit,
                "value": str(getattr(effective, description.name)),
                "own_value": getattr(zone, description.name) is not None,
                "minimum": str(description.minimum),
                "maximum": str(description.maximum),
            }
            for description in PARAMETERS
        ],
    }


def set_control_parameters(
    session: Session, plaintext: str, zone_id: int, name: str, value: Decimal
) -> dict[str, object]:
    """Sets **one** control parameter and leaves the rest as they are.

    `zone.manage`, not `override.create`: a control parameter has a lasting effect on
    every future decision, an override only until the next schedule point.
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.manage", zone_id)
    recorded = change_log_domain.set_parameter(session, _actor(principal, token), zone, name, value)
    return {
        "zone": zone.name,
        "name": name,
        "value": _decimal(recorded.result),
        "change_id": recorded.change_id,
    }


def read_control(session: Session, plaintext: str) -> dict[str, object]:
    """The plant's operating state along with the defaults every zone inherits from.

    This reports the persisted database latch. The startup-built MQTT latch belongs to
    the separate web/MQTT process, so its state is explicitly reported as unknown from
    this process instead of being omitted. Once both latches are open, setpoints reach
    self-regulating valves and on/off commands reach ordinary actuators.
    """
    _token, principal = _log_in(session, plaintext)
    require(principal, "zone.read")
    row = settings(session)
    return {
        "armed": row.control_armed,
        "mqtt_startup_latch_state": "unknown_from_mcp_process",
        "timezone": row.timezone,
        # The solar setback is reported alongside the defaults it modifies: an
        # assistant asked why a zone is heating less than its schedule says should be
        # able to see that a setback is switched on without a second call.
        "solar_forecast_enabled": row.solar_forecast_enabled,
        "solar_forecast_latitude": _decimal(row.solar_forecast_latitude),
        "solar_forecast_longitude": _decimal(row.solar_forecast_longitude),
        **{field: str(getattr(row, field)) for field in LIMITS},
    }


def _sensor_failure_defaults_payload(session: Session) -> dict[str, object]:
    profile_id = _resolved_default_profile_id(session)
    profile = read_profile(session, profile_id)
    defaults = read_defaults(session)
    return {
        "profile_id": profile.id,
        "profile_name": profile.values.name,
        "fixed_on_seconds": profile.values.fixed_on_seconds,
        "fixed_off_seconds": profile.values.fixed_off_seconds,
        "recovery_seconds": profile.values.recovery_seconds,
        "recovery_samples": profile.values.recovery_samples,
        "warm_restart_hysteresis_k": _decimal(profile.values.warm_restart_hysteresis_k),
        "curve_points": [
            {
                "outdoor_c": _decimal(point.outdoor_c),
                "on_seconds": point.on_seconds,
                "off_seconds": point.off_seconds,
            }
            for point in profile.values.curve_points
        ],
        "emergency_setpoint_c": _decimal(defaults.emergency_setpoint_c),
    }


def read_sensor_failure_defaults_tool(session: Session, plaintext: str) -> dict[str, object]:
    """Anlagenweites Notbetriebsprofil (Festtakt, Rückkehr, Kennlinie) und den
    plantweiten Notsollwert -- wie unter „Regelvorgaben" (Auftrag 8a)."""
    _token, principal = _log_in(session, plaintext)
    require(principal, "zone.read")
    return _sensor_failure_defaults_payload(session)


def set_sensor_failure_defaults_tool(
    session: Session,
    plaintext: str,
    *,
    fixed_on_seconds: int,
    fixed_off_seconds: int,
    recovery_seconds: int,
    recovery_samples: int,
    warm_restart_hysteresis_k: Decimal,
    emergency_setpoint_c: Decimal,
    curve_points: list[dict[str, Any]] | None = None,
    profile_name: str | None = None,
) -> dict[str, object]:
    """Speichert das anlagenweite Notbetriebsprofil und den Notsollwert zusammen.

    `curve_points` leer oder weggelassen bedeutet Festtakt -- dieselbe Regel wie
    auf der Regelvorgaben-Seite.
    """
    token, principal = _log_in(session, plaintext)
    require(principal, "setting.manage")
    profile_id = _resolved_default_profile_id(session)
    current = read_profile(session, profile_id)
    values = ProfileValues(
        name=profile_name or current.values.name,
        fixed_on_seconds=fixed_on_seconds,
        fixed_off_seconds=fixed_off_seconds,
        recovery_seconds=recovery_seconds,
        recovery_samples=recovery_samples,
        warm_restart_hysteresis_k=warm_restart_hysteresis_k,
        curve_points=tuple(
            CurvePoint(
                Decimal(str(point["outdoor_c"])),
                int(point["on_seconds"]),
                int(point["off_seconds"]),
            )
            for point in (curve_points or [])
        ),
    )
    recorded = change_log_domain.save_sensor_failure_defaults(
        session,
        _actor(principal, token),
        values,
        profile_id=profile_id,
        emergency_setpoint_c=emergency_setpoint_c,
    )
    return {**_sensor_failure_defaults_payload(session), "change_id": recorded.change_id}


def read_sensor_failure_policy(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Wirksame und eigene Notbetriebs-Einstellungen einer Zone, samt Ausgleichswerten
    ihrer Thermostat-Aktor-Zuordnungen (Auftrag 8a)."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    effective = sensor_failure_parameters(session, zone)
    return {
        "zone": zone.name,
        "enabled": zone.sensor_failure_enabled,
        "profile_id": zone.sensor_failure_profile_id,
        "effective_profile_id": effective.profile.id,
        "profile_source": effective.profile_source,
        "emergency_setpoint_c": _decimal(zone.sensor_failure_emergency_setpoint_c),
        "effective_emergency_setpoint_c": _decimal(effective.emergency_setpoint_c),
        "setpoint_source": effective.setpoint_source,
        "backup_offsets": [
            {
                "zone_device_id": candidate.zone_device_id,
                "device_name": candidate.device_name,
                "temperature_backup_offset_k": _decimal(candidate.offset_k),
            }
            for candidate in list_backup_offset_candidates(session, zone)
        ],
    }


def set_sensor_failure_policy(
    session: Session,
    plaintext: str,
    zone_id: int,
    *,
    enabled: bool,
    profile_id: int | None = None,
    emergency_setpoint_c: Decimal | None = None,
    backup_offsets: dict[int, Decimal | None] | None = None,
) -> dict[str, object]:
    """Setzt Aktivierung, Profil-/Notsollwert-Herkunft und optional Ausgleichswerte.

    Ausgleichswerte brauchen zusätzlich `device.manage` -- dieselbe Trennung wie in
    REST (`PUT /api/v1/zones/{id}/sensor-failure`).
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.manage", zone_id)
    if backup_offsets:
        require(principal, "device.manage", zone_id)
    # Ausgleichswerte fremder Zuordnungen weist die Domäne ab, bevor etwas geschrieben wird.
    recorded = change_log_domain.save_sensor_failure_policy(
        session,
        _actor(principal, token),
        zone,
        enabled=enabled,
        profile_id=profile_id,
        emergency_setpoint_c=emergency_setpoint_c,
        backup_offsets=backup_offsets,
    )
    return {
        **read_sensor_failure_policy(session, plaintext, zone_id),
        "change_id": recorded.change_id,
    }


def force_dry_run(
    session: Session, plaintext: str, reason: str = ""
) -> dict[str, object]:
    """Puts control back into the dry run.

    **Only this direction.** Arming deliberately does not exist here, even though REST
    and the interface can do it: the MCP server speaks for a language model, and the
    justification the domain requires for arming is no obstacle for a model -- it is
    exactly the kind of text it generates effortlessly. The barrier would thus be a
    formality instead of a decision. Going back to the dry run, on the other hand, is
    always the safe direction and has its own permission, `control.disarm`, so that
    it does not require the right to arm. Whoever wants to arm the plant does so in
    the interface or via the REST interface, where a human is at the button.
    """
    token, principal = _log_in(session, plaintext)
    require(principal, "control.disarm")
    recorded = change_log_domain.force_dry_run(
        session, _actor(principal, token), reason=reason
    )
    return {"armed": False, "changed": recorded.result, "change_id": recorded.change_id}


def move_schedule_point(
    session: Session, plaintext: str, zone_id: int, point_id: int, weekday: int, minute: int
) -> dict[str, object]:
    """Moves a schedule point to a different time.

    The same domain function as dragging it in the week view -- the point keeps its id,
    and the audit log shows a move instead of a deletion followed by a re-creation.
    """
    token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "schedule.manage", zone_id)
    point = session.get(SchedulePoint, point_id)
    if point is None or point.zone_id != zone.id:
        raise ValueError("Zeitplanpunkt nicht gefunden")
    recorded = change_log_domain.move_point(
        session, _actor(principal, token), zone, point, weekday=weekday, minute=minute
    )
    return {
        "zone": zone.name,
        "point_id": point.id,
        "weekday": point.weekday,
        "minute": point.minute_of_day,
        "change_id": recorded.change_id,
    }


def _change_entry(view: ChangeView) -> dict[str, object]:
    """Ein Eintrag des Änderungsprotokolls in der Form, die `list_changes` und
    `revert_change` gemeinsam ausgeben. Zeitpunkte tragen die Zeitzone ausdrücklich."""
    return {
        "id": view.id,
        "at": _moment_utc(view.occurred_at),
        "source": view.source,
        "user": view.user_name,
        "token": view.token_name,
        "change_type": view.change_type,
        "zone": view.zone_name,
        "summary": view.summary,
        "fields": [
            {"field": item.field, "before": item.before, "after": item.after}
            for item in view.fields
        ],
        "revertible": view.revertible,
        "not_revertible_reason": view.not_revertible_reason,
        "reverted": view.reverted_at is not None,
        "reverted_at": _moment_utc(view.reverted_at),
        "reverted_by": view.reverted_by_id,
        "reverts": view.reverts_id,
        "forced": view.forced,
    }


def list_changes(
    session: Session,
    plaintext: str,
    zone: str | None = None,
    change_type: str | None = None,
    source: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    include_reverted: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, object]]:
    """Reads the change log -- the same protocol as the page "Änderungsprotokoll".

    `audit.read`, plant-wide: like the command log, this is a protocol over the whole
    plant. `zone` is the zone's name (as in `list_zones`); entries of a zone that has
    been deleted since are still found with `zone=None`.
    """
    _token, principal = _log_in(session, plaintext)
    require(principal, "audit.read")
    zone_id: int | None = None
    if zone is not None:
        found = next(
            (
                entry
                for entry in visible_zones(session, principal, "audit.read")
                if zone in (entry.name, entry.display_name)
            ),
            None,
        )
        if found is None:
            raise LookupError("Zone nicht gefunden")
        zone_id = found.id
    if limit < 1 or limit > change_log_domain.MAX_LIMIT:
        raise ChangeLogError(f"limit muss zwischen 1 und {change_log_domain.MAX_LIMIT} liegen")
    views = change_log_domain.list_changes(
        session,
        zone_id=zone_id,
        change_type=change_type,
        source=source,
        from_at=from_at,
        to_at=to_at,
        include_reverted=include_reverted,
        limit=limit,
    )
    return [_change_entry(view) for view in views]


def revert_change(
    session: Session, plaintext: str, change_id: int, force: bool = False
) -> dict[str, object]:
    """Takes one logged change back, through the domain's own functions.

    The rights are those of the original change and are checked by the domain, before
    anything about the current state is disclosed -- not repeated here. A revert whose
    effect has already ended is not an error: it comes back as `already_effective`
    and the entry is marked as settled (an exception would roll that marking back).
    """
    token, principal = _log_in(session, plaintext)
    try:
        view = change_log_domain.revert_change(
            session, principal, change_id, force=force, actor=_actor(principal, token)
        )
    except ChangeAlreadyEffective as notice:
        return {"status": "already_effective", "message": str(notice), "change_id": change_id}
    return {"status": "reverted", **_change_entry(view)}


def _mcp_server_class() -> Callable[[str], _McpServer]:
    try:
        # Two paths, because the two common MCP versions keep the server class in
        # different places. Which one applies depends on the installed version --
        # coverage therefore always sees only one of the two.
        try:
            module = import_module("mcp.server.mcpserver")
            return cast(Callable[[str], _McpServer], module.MCPServer)  # pragma: no cover
        except ModuleNotFoundError:
            module = import_module("mcp.server.fastmcp")
            return cast(Callable[[str], _McpServer], module.FastMCP)  # pragma: no cover
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Das optionale MCP-Paket fehlt. Installation: pip install 'thermoctl[mcp]'"
        ) from exc


def _visible_to_client(error: Exception) -> Exception | None:
    """Macht die Begründung einer Ablehnung für den Client lesbar.

    Das SDK ersetzt jede Ausnahme, die kein `ToolError` ist, durch "Error executing tool
    <name>" -- ein Modell erführe nie, dass ihm ein Recht fehlt oder die Zone nicht
    existiert, und müsste raten. Die Meldungen dieser Arten formuliert die Domäne selbst
    (Recht fehlt, Zone nicht gefunden, Token ungültig, Eingabe abgewiesen); andere Ausnahmen bleiben
    verborgen, weil ihr Text Eingaben des Clients enthalten kann.
    """
    if not isinstance(error, (Forbidden, PermissionError, LookupError, *_REJECTIONS)):
        return None
    try:
        tool_error = import_module("mcp.server.mcpserver.exceptions").ToolError
    except ModuleNotFoundError:  # pragma: no cover - ältere SDK-Versionen
        try:
            tool_error = import_module("mcp.server.fastmcp.exceptions").ToolError
        except ModuleNotFoundError:
            return None
    return cast(Exception, tool_error(_reason(error)))


class _LoggedServer:
    """Hüllt den MCP-Server, damit jeder Werkzeugaufruf protokolliert wird.

    Die Werkzeuge bleiben im Quelltext als Dekorator `server.tool` mit Namensargument
    lesbar -- Wächtertests und Dokumentation suchen genau diese Form.
    """

    def __init__(self, inner: _McpServer) -> None:
        self._inner = inner

    def tool(self, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        register = self._inner.tool(name=name)
        label = name or "?"

        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(function)
            def logged(*args: Any, **kwargs: Any) -> Any:
                try:
                    result = function(*args, **kwargs)
                except Exception as exc:
                    _log_tool(label, exc)
                    visible = _visible_to_client(exc)
                    if visible is not None:
                        raise visible from exc
                    raise
                _log_tool(label, None)
                return result

            return register(logged)

        return decorate

    def run(self, transport: str = "stdio", **kwargs: Any) -> None:
        self._inner.run(transport, **kwargs)  # pragma: no cover


def _register_tools(
    server: _McpServer,
    factory: sessionmaker[Session],
    plaintext: str | Callable[[], str],
) -> None:
    """Registriert die Werkzeuge.

    `plaintext` ist bei stdio das beim Start festgelegte Token, bei HTTP eine Funktion, die
    das Token der laufenden Anfrage liefert (`http_auth.current_token`).
    """
    current: Callable[[], str] = plaintext if callable(plaintext) else (lambda: plaintext)

    server = _LoggedServer(server)

    @server.tool(name="list_zones")
    def mcp_list_zones() -> list[dict[str, object]]:
        """Lists the zones visible to the authenticated token."""
        with session_scope(factory) as session:
            return list_zones(session, current())

    @server.tool(name="zone_state")
    def mcp_zone_state(zone_id: int) -> dict[str, object]:
        """Returns the latest derived temperature and sensor state of a visible zone."""
        with session_scope(factory) as session:
            return zone_state(session, current(), zone_id)

    @server.tool(name="explain_setpoint")
    def mcp_explain_setpoint(zone_id: int) -> dict[str, object]:
        """Returns a visible zone's effective setpoint and the reason for it."""
        with session_scope(factory) as session:
            return explain_setpoint(session, current(), zone_id)

    @server.tool(name="read_schedule")
    def mcp_read_schedule(zone_id: int) -> list[dict[str, object]]:
        """Reads the schedule points and mode names of a visible zone."""
        with session_scope(factory) as session:
            return read_schedule(session, current(), zone_id)

    @server.tool(name="read_setpoints")
    def mcp_read_setpoints(zone_id: int) -> list[dict[str, object]]:
        """Reads the temperatures configured for each mode of a visible zone."""
        with session_scope(factory) as session:
            return read_setpoints(session, current(), zone_id)

    @server.tool(name="list_devices")
    def mcp_list_devices() -> list[dict[str, object]]:
        """Lists devices with integrations, capabilities, and signs of life."""
        with session_scope(factory) as session:
            return list_devices(session, current())

    @server.tool(name="shadow_decisions")
    def mcp_shadow_decisions(zone_id: int, count: int = 10) -> list[dict[str, object]]:
        """Returns the latest reasoned shadow decisions of a visible zone."""
        with session_scope(factory) as session:
            return shadow_decisions(session, current(), zone_id, count)

    @server.tool(name="device_commands")
    def mcp_device_commands(
        zone: str | None = None,
        outcome: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, object]]:
        """Reads the actuator command log, newest first, filtered and capped."""
        with session_scope(factory) as session:
            return device_commands(session, current(), zone, outcome, from_at, to_at, limit)

    @server.tool(name="list_changes")
    def mcp_list_changes(
        zone: str | None = None,
        change_type: str | None = None,
        source: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        include_reverted: bool = True,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, object]]:
        """Reads the change log, newest first: who changed what, before and after.

        Filters: zone name, change_type, source, from_at (inclusive) / to_at (exclusive,
        naive = UTC), include_reverted. Each entry says whether it can be taken back
        (`revertible`, else `not_revertible_reason`) and by which entry it was
        (`reverted_by`); `reverts` is set on an entry that is itself a rollback.
        """
        with session_scope(factory) as session:
            return list_changes(
                session, current(), zone, change_type, source, from_at, to_at,
                include_reverted, limit,
            )

    @server.tool(name="revert_change")
    def mcp_revert_change(change_id: int, force: bool = False) -> dict[str, object]:
        """Takes back one logged change (id from list_changes) and returns the new entry.

        Needs the rights of the original change. If the value was changed again since,
        the call is refused and names the current and the expected value; `force=true`
        sets it back anyway. A rollback is not rolled back again; if the effect has
        already ended the answer is status `already_effective`.
        """
        with session_scope(factory) as session:
            return revert_change(session, current(), change_id, force)

    @server.tool(name="temperature_history")
    def mcp_temperature_history(
        zone_id: int,
        hours: int = 24,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        max_points: int = 300,
        log_limit: int = 100,
    ) -> dict[str, object]:
        """Temperature history of a visible zone, with the zone's command log.

        Window: from_at/to_at (naive = UTC) or the last `hours` (1-168, at most 7 days).
        Points are time buckets of `bucket_minutes`; a bucket without readings is absent
        (a gap, nothing interpolated), `summary.buckets_without_data` counts them. null =
        unknown, never 0. `heat_share` is the share of readings in which the controller's
        demand flag (`would_heat`) was set. `switching_log` needs audit.read, otherwise null.
        """
        with session_scope(factory) as session:
            return temperature_history(
                session, current(), zone_id, hours, from_at, to_at, max_points, log_limit
            )

    @server.tool(name="read_emergency_state")
    def mcp_read_emergency_state(zone_id: int) -> dict[str, object]:
        """Reads the Notbetrieb state of a visible zone."""
        with session_scope(factory) as session:
            return read_emergency_state(session, current(), zone_id)

    @server.tool(name="override")
    def mcp_override(
        zone_id: int, temperature_c: Decimal, ends_at: datetime | None = None
    ) -> dict[str, object]:
        """Creates a temporary setpoint override for a visible zone."""
        with session_scope(factory) as session:
            return override_zone(session, current(), zone_id, temperature_c, ends_at)

    @server.tool(name="cancel_override")
    def mcp_cancel_override(zone_id: int) -> dict[str, object]:
        """Cancels the active setpoint override of a visible zone."""
        with session_scope(factory) as session:
            return cancel_override(session, current(), zone_id)

    @server.tool(name="boost")
    def mcp_boost(zone_id: int) -> dict[str, object]:
        """Pulls a visible zone's next scheduled setpoint switch forward."""
        with session_scope(factory) as session:
            return boost(session, current(), zone_id)

    @server.tool(name="read_vacation")
    def mcp_read_vacation() -> dict[str, object] | None:
        """Reads the plant-wide vacation currently running or still upcoming, if any."""
        with session_scope(factory) as session:
            return read_vacation(session, current())

    @server.tool(name="vacation")
    def mcp_vacation(
        start_date: date, end_date: date, setback_temperature_c: Decimal
    ) -> dict[str, object]:
        """Sets a plant-wide vacation setback over a fixed local calendar range."""
        with session_scope(factory) as session:
            return set_vacation(session, current(), start_date, end_date, setback_temperature_c)

    @server.tool(name="cancel_vacation")
    def mcp_cancel_vacation() -> dict[str, object]:
        """Ends the running or planned plant-wide vacation early."""
        with session_scope(factory) as session:
            return cancel_vacation(session, current())

    @server.tool(name="read_control_parameters")
    def mcp_read_control_parameters(zone_id: int) -> dict[str, object]:
        """Reads a visible zone's effective control parameters and their limits."""
        with session_scope(factory) as session:
            return read_control_parameters(session, current(), zone_id)

    @server.tool(name="set_control_parameters")
    def mcp_set_control_parameters(zone_id: int, name: str, value: Decimal) -> dict[str, object]:
        """Sets one control parameter of a visible zone within its limits."""
        with session_scope(factory) as session:
            return set_control_parameters(session, current(), zone_id, name, value)

    @server.tool(name="read_control")
    def mcp_read_control() -> dict[str, object]:
        """Reads the persisted latch, MQTT-latch observability, and global defaults."""
        with session_scope(factory) as session:
            return read_control(session, current())

    @server.tool(name="force_dry_run")
    def mcp_force_dry_run(reason: str = "") -> dict[str, object]:
        """Returns control to dry run; this tool cannot arm the installation."""
        with session_scope(factory) as session:
            return force_dry_run(session, current(), reason)

    @server.tool(name="read_sensor_failure_defaults")
    def mcp_read_sensor_failure_defaults() -> dict[str, object]:
        """Reads the plant-wide Notbetrieb profile (Festtakt/Rückkehr/Kennlinie)
        and the plant-wide emergency setpoint."""
        with session_scope(factory) as session:
            return read_sensor_failure_defaults_tool(session, current())

    @server.tool(name="set_sensor_failure_defaults")
    def mcp_set_sensor_failure_defaults(
        fixed_on_seconds: int,
        fixed_off_seconds: int,
        recovery_seconds: int,
        recovery_samples: int,
        warm_restart_hysteresis_k: Decimal,
        emergency_setpoint_c: Decimal,
        curve_points: list[dict[str, Any]] | None = None,
        profile_name: str | None = None,
    ) -> dict[str, object]:
        """Saves the plant-wide Notbetrieb profile and emergency setpoint together.

        `curve_points` empty or omitted means Festtakt only, same as on the
        Regelvorgaben page.
        """
        with session_scope(factory) as session:
            return set_sensor_failure_defaults_tool(
                session,
                current(),
                fixed_on_seconds=fixed_on_seconds,
                fixed_off_seconds=fixed_off_seconds,
                recovery_seconds=recovery_seconds,
                recovery_samples=recovery_samples,
                warm_restart_hysteresis_k=warm_restart_hysteresis_k,
                emergency_setpoint_c=emergency_setpoint_c,
                curve_points=curve_points,
                profile_name=profile_name,
            )

    @server.tool(name="read_sensor_failure_policy")
    def mcp_read_sensor_failure_policy(zone_id: int) -> dict[str, object]:
        """Reads a visible zone's effective and own Notbetrieb settings, including
        the backup offsets of its thermostat actuator assignments."""
        with session_scope(factory) as session:
            return read_sensor_failure_policy(session, current(), zone_id)

    @server.tool(name="set_sensor_failure_policy")
    def mcp_set_sensor_failure_policy(
        zone_id: int,
        enabled: bool,
        profile_id: int | None = None,
        emergency_setpoint_c: Decimal | None = None,
        backup_offsets: dict[int, Decimal | None] | None = None,
    ) -> dict[str, object]:
        """Sets a visible zone's Notbetrieb activation, profile/setpoint source,
        and optionally its thermostat backup offsets (needs `device.manage`)."""
        with session_scope(factory) as session:
            return set_sensor_failure_policy(
                session,
                current(),
                zone_id,
                enabled=enabled,
                profile_id=profile_id,
                emergency_setpoint_c=emergency_setpoint_c,
                backup_offsets=backup_offsets,
            )

    @server.tool(name="move_schedule_point")
    def mcp_move_schedule_point(
        zone_id: int, point_id: int, weekday: int, minute: int
    ) -> dict[str, object]:
        """Moves an existing schedule point of a visible zone to another time."""
        with session_scope(factory) as session:
            return move_schedule_point(
                session, current(), zone_id, point_id, weekday, minute
            )


def main() -> None:
    """Startet den MCP-Server über stdio oder auf einem eigenen HTTP-Port."""
    import argparse

    parser = argparse.ArgumentParser(description="thermoctl MCP-Server")
    parser.add_argument("--http", action="store_true", help="MCP über HTTP starten")
    args, _unknown = parser.parse_known_args()
    settings = get_settings()
    if args.http:
        if settings.mcp_mode != "port":
            raise SystemExit(
                "--http verlangt THERMOCTL_MCP_MODE=port; im Modus path läuft MCP in der Web-App."
            )
        settings.validate_mcp_http()
        import uvicorn

        from thermoctl.logging import configure_logging
        from thermoctl.mcp.http_app import create_mcp_asgi_app

        configure_logging(settings)
        engine = create_engine_from_settings(settings)
        factory = session_factory(engine)
        app = create_mcp_asgi_app(settings, factory)
        assert settings.mcp_port is not None  # validate_mcp_http() hat den Port geprüft.
        try:
            # uvicorn nutzt proxy_headers=True als Standard; FORWARDED_ALLOW_IPS
            # begrenzt, welchen vorgeschalteten Proxys es dabei vertraut.
            uvicorn.run(app, host=settings.bind_host, port=settings.mcp_port)
        finally:
            engine.dispose()
        return
    if settings.mcp_token is None:
        raise SystemExit("THERMOCTL_MCP_TOKEN fehlt; der MCP-Server startet nicht ohne Anmeldung.")
    # From here on the process runs as a stdio server until stopped. This is wiring,
    # not behavior: every single part is tested -- the token check above it, the
    # registration in `test_registrierte_mcp_werkzeuge_rufen_die_adapterfunktionen_auf`,
    # and each tool individually. A test of these lines would have to start and then
    # kill a real stdio server, and would thereby test the library, not us.
    plaintext = settings.mcp_token.get_secret_value()  # pragma: no cover
    server_class = _mcp_server_class()  # pragma: no cover
    engine = create_engine_from_settings(settings)  # pragma: no cover
    factory = session_factory(engine)  # pragma: no cover
    server = server_class("thermoctl")  # pragma: no cover
    _register_tools(server, factory, plaintext)  # pragma: no cover
    try:  # pragma: no cover
        server.run(transport="stdio")
    finally:
        engine.dispose()  # pragma: no cover
