from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
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
from thermoctl.domain.authz import principal_for_token, require, visible_zones
from thermoctl.domain.control import (
    LIMITS,
    arm,
    save_sensor_failure_backup_offset,
    save_sensor_failure_defaults,
    save_sensor_failure_profile,
    settings,
)
from thermoctl.domain.device_commands import DEFAULT_LIMIT, list_commands, naive_utc
from thermoctl.domain.principal import Principal
from thermoctl.domain.remote_control import boost as domain_boost
from thermoctl.domain.schedule import (
    cancel_override as domain_cancel_override,
)
from thermoctl.domain.schedule import (
    cancel_vacation as domain_cancel_vacation,
)
from thermoctl.domain.schedule import (
    create_override,
    create_vacation,
    current_or_upcoming_vacation,
    resolved_setpoint,
)
from thermoctl.domain.schedule import (
    move_schedule_point as domain_move_schedule_point,
)
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    ProfileValues,
    list_backup_offset_candidates,
    migration_default_profile_id,
    read_defaults,
    read_profile,
)
from thermoctl.domain.zone_settings import (
    PARAMETERS,
    control_parameters,
    save_sensor_failure_parameters,
    sensor_failure_parameters,
)
from thermoctl.domain.zone_settings import (
    set_parameter as domain_set_parameter,
)
from thermoctl.services.emergency_state import zone_emergency_view


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
    return [
        {
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
        for entry in entries
    ]


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
    entry = create_override(
        session,
        zone,
        temperature_c,
        ends_at,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "temperature_c": _decimal(entry.temperature_c),
        "ends_at": _moment(entry.ends_at),
    }


def cancel_override(session: Session, plaintext: str, zone_id: int) -> dict[str, object]:
    """Cancels the active override via the shared domain function."""
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "override.cancel", zone_id)
    cancelled = domain_cancel_override(session, zone)
    return {"zone": zone.name, "cancelled": cancelled is not None}


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
    entry = create_vacation(
        session,
        start_date=start_date,
        end_date=end_date,
        setback_temperature_c=setback_temperature_c,
        timezone_name=row.timezone,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "starts_at": _moment(entry.starts_at),
        "ends_at": _moment(entry.ends_at),
        "setback_temperature_c": _decimal(entry.setback_temperature_c),
    }


def cancel_vacation(session: Session, plaintext: str) -> dict[str, object]:
    """Ends the running or planned vacation early via the shared domain function."""
    token, principal = _log_in(session, plaintext)
    require(principal, "vacation.manage")
    cancelled = domain_cancel_vacation(
        session, user_id=principal.user_id, token_id=token.id, source="mcp"
    )
    return {"cancelled": cancelled is not None}


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
    result = domain_boost(
        session,
        zone,
        utcnow(),
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "mode": result.mode_code,
        "temperature_c": _decimal(result.temperature),
        "valid_until": _moment(result.bis),
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
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.manage", zone_id)
    set_value = domain_set_parameter(
        session, zone, name, value, user_id=principal.user_id, source="mcp"
    )
    return {"zone": zone.name, "name": name, "value": _decimal(set_value)}


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
    _token, principal = _log_in(session, plaintext)
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
    save_sensor_failure_profile(
        session,
        values,
        profile_id=profile_id,
        user_id=principal.user_id,
        token_id=_token.id,
        source="mcp",
    )
    save_sensor_failure_defaults(
        session,
        profile_id=profile_id,
        emergency_setpoint_c=emergency_setpoint_c,
        user_id=principal.user_id,
        token_id=_token.id,
        source="mcp",
    )
    return _sensor_failure_defaults_payload(session)


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
    _token, principal = _log_in(session, plaintext)
    zone = _visible_zone(session, principal, zone_id)
    require(principal, "zone.manage", zone_id)
    if backup_offsets:
        require(principal, "device.manage", zone_id)
        valid_ids = {c.zone_device_id for c in list_backup_offset_candidates(session, zone)}
        unknown = set(backup_offsets) - valid_ids
        if unknown:
            raise ValueError(
                f"Zuordnung(en) {sorted(unknown)} gehören nicht zu dieser Zone "
                "oder sind kein Thermostat-Aktor."
            )
    save_sensor_failure_parameters(
        session,
        zone,
        enabled=enabled,
        profile_id=profile_id,
        emergency_setpoint_c=emergency_setpoint_c,
        user_id=principal.user_id,
        token_id=_token.id,
        source="mcp",
    )
    for assignment_id, value in (backup_offsets or {}).items():
        save_sensor_failure_backup_offset(
            session,
            assignment_id,
            value,
            user_id=principal.user_id,
            token_id=_token.id,
            source="mcp",
        )
    return read_sensor_failure_policy(session, plaintext, zone_id)


def force_dry_run(
    session: Session, plaintext: str, reason: str = ""
) -> dict[str, object]:
    """Puts control back into the dry run.

    **Only this direction.** Arming deliberately does not exist here, even though REST
    and the interface can do it: the MCP server speaks for a language model, and the
    justification the domain requires for arming is no obstacle for a model -- it is
    exactly the kind of text it generates effortlessly. The barrier would thus be a
    formality instead of a decision. Going back to the dry run, on the other hand, is
    always the safe direction and should be open to anyone allowed to operate the
    plant. Whoever wants to arm the plant does so in the interface or via the REST
    interface, where a human is at the button.
    """
    token, principal = _log_in(session, plaintext)
    require(principal, "control.arm")
    changed = arm(
        session,
        False,
        reason=reason,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {"armed": False, "changed": changed}


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
    domain_move_schedule_point(
        session,
        zone,
        point,
        weekday=weekday,
        minute=minute,
        user_id=principal.user_id,
        token_id=token.id,
        source="mcp",
    )
    return {
        "zone": zone.name,
        "point_id": point.id,
        "weekday": point.weekday,
        "minute": point.minute_of_day,
    }


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


def _register_tools(
    server: _McpServer, factory: sessionmaker[Session], plaintext: str
) -> None:
    @server.tool(name="list_zones")
    def mcp_list_zones() -> list[dict[str, object]]:
        """Lists the zones visible to the authenticated token."""
        with session_scope(factory) as session:
            return list_zones(session, plaintext)

    @server.tool(name="zone_state")
    def mcp_zone_state(zone_id: int) -> dict[str, object]:
        """Returns the latest derived temperature and sensor state of a visible zone."""
        with session_scope(factory) as session:
            return zone_state(session, plaintext, zone_id)

    @server.tool(name="explain_setpoint")
    def mcp_explain_setpoint(zone_id: int) -> dict[str, object]:
        """Returns a visible zone's effective setpoint and the reason for it."""
        with session_scope(factory) as session:
            return explain_setpoint(session, plaintext, zone_id)

    @server.tool(name="read_schedule")
    def mcp_read_schedule(zone_id: int) -> list[dict[str, object]]:
        """Reads the schedule points and mode names of a visible zone."""
        with session_scope(factory) as session:
            return read_schedule(session, plaintext, zone_id)

    @server.tool(name="read_setpoints")
    def mcp_read_setpoints(zone_id: int) -> list[dict[str, object]]:
        """Reads the temperatures configured for each mode of a visible zone."""
        with session_scope(factory) as session:
            return read_setpoints(session, plaintext, zone_id)

    @server.tool(name="list_devices")
    def mcp_list_devices() -> list[dict[str, object]]:
        """Lists devices with integrations, capabilities, and signs of life."""
        with session_scope(factory) as session:
            return list_devices(session, plaintext)

    @server.tool(name="shadow_decisions")
    def mcp_shadow_decisions(zone_id: int, count: int = 10) -> list[dict[str, object]]:
        """Returns the latest reasoned shadow decisions of a visible zone."""
        with session_scope(factory) as session:
            return shadow_decisions(session, plaintext, zone_id, count)

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
            return device_commands(session, plaintext, zone, outcome, from_at, to_at, limit)

    @server.tool(name="read_emergency_state")
    def mcp_read_emergency_state(zone_id: int) -> dict[str, object]:
        """Reads the Notbetrieb state of a visible zone."""
        with session_scope(factory) as session:
            return read_emergency_state(session, plaintext, zone_id)

    @server.tool(name="override")
    def mcp_override(
        zone_id: int, temperature_c: Decimal, ends_at: datetime | None = None
    ) -> dict[str, object]:
        """Creates a temporary setpoint override for a visible zone."""
        with session_scope(factory) as session:
            return override_zone(session, plaintext, zone_id, temperature_c, ends_at)

    @server.tool(name="cancel_override")
    def mcp_cancel_override(zone_id: int) -> dict[str, object]:
        """Cancels the active setpoint override of a visible zone."""
        with session_scope(factory) as session:
            return cancel_override(session, plaintext, zone_id)

    @server.tool(name="boost")
    def mcp_boost(zone_id: int) -> dict[str, object]:
        """Pulls a visible zone's next scheduled setpoint switch forward."""
        with session_scope(factory) as session:
            return boost(session, plaintext, zone_id)

    @server.tool(name="read_vacation")
    def mcp_read_vacation() -> dict[str, object] | None:
        """Reads the plant-wide vacation currently running or still upcoming, if any."""
        with session_scope(factory) as session:
            return read_vacation(session, plaintext)

    @server.tool(name="vacation")
    def mcp_vacation(
        start_date: date, end_date: date, setback_temperature_c: Decimal
    ) -> dict[str, object]:
        """Sets a plant-wide vacation setback over a fixed local calendar range."""
        with session_scope(factory) as session:
            return set_vacation(session, plaintext, start_date, end_date, setback_temperature_c)

    @server.tool(name="cancel_vacation")
    def mcp_cancel_vacation() -> dict[str, object]:
        """Ends the running or planned plant-wide vacation early."""
        with session_scope(factory) as session:
            return cancel_vacation(session, plaintext)

    @server.tool(name="read_control_parameters")
    def mcp_read_control_parameters(zone_id: int) -> dict[str, object]:
        """Reads a visible zone's effective control parameters and their limits."""
        with session_scope(factory) as session:
            return read_control_parameters(session, plaintext, zone_id)

    @server.tool(name="set_control_parameters")
    def mcp_set_control_parameters(zone_id: int, name: str, value: Decimal) -> dict[str, object]:
        """Sets one control parameter of a visible zone within its limits."""
        with session_scope(factory) as session:
            return set_control_parameters(session, plaintext, zone_id, name, value)

    @server.tool(name="read_control")
    def mcp_read_control() -> dict[str, object]:
        """Reads the persisted latch, MQTT-latch observability, and global defaults."""
        with session_scope(factory) as session:
            return read_control(session, plaintext)

    @server.tool(name="force_dry_run")
    def mcp_force_dry_run(reason: str = "") -> dict[str, object]:
        """Returns control to dry run; this tool cannot arm the installation."""
        with session_scope(factory) as session:
            return force_dry_run(session, plaintext, reason)

    @server.tool(name="read_sensor_failure_defaults")
    def mcp_read_sensor_failure_defaults() -> dict[str, object]:
        """Reads the plant-wide Notbetrieb profile (Festtakt/Rückkehr/Kennlinie)
        and the plant-wide emergency setpoint."""
        with session_scope(factory) as session:
            return read_sensor_failure_defaults_tool(session, plaintext)

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
                plaintext,
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
            return read_sensor_failure_policy(session, plaintext, zone_id)

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
                plaintext,
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
                session, plaintext, zone_id, point_id, weekday, minute
            )


def main() -> None:
    """Starts the authenticated MCP server over stdio."""
    settings = get_settings()
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
