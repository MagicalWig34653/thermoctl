"""The one read of the Notbetrieb runtime state, shared by every adapter.

Auftrag 8b of `lokal/plaene/0.11.0-notbetrieb.md`. Grundsatz 6: HTMX
(`web/control_views.py`, the start/tenant/kiosk banners), REST
(`GET /api/v1/zones/{id}/emergency-state`) and MCP (`read_emergency_state`)
must describe the exact same persisted state the exact same way -- this
module is where that single reading lives. Everything it returns is either a
column already written by `services/shadow_run.py`/`services/publishing.py`
or a derived value (`domain.emergency_display`) over those columns; nothing
here writes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import (
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.db.models.zone import Zone
from thermoctl.domain import emergency_display, emergency_operation
from thermoctl.domain.outdoor import OutdoorReading, outdoor_reading
from thermoctl.services.shadow_run import zone_actuator_assignments

# How far back the Ersatzquelle<->Wandfühler comparison looks (plan Auftrag
# 8b item 2, Entscheidung R2: "mittlere Abweichung ... über die letzten Tage").
# Not configurable -- it is a diagnostic window, not a policy value with its
# own correctness requirement (unlike every `sensor_failure_policy.py` field).
COMPARISON_WINDOW_DAYS = 7


@dataclass(frozen=True)
class ActuatorEmergencyView:
    """One actuator assignment's emergency state, as the control page and
    REST/MCP both need it -- the scharfe (not simulated) columns only: this
    view answers "what is actually happening on the plant", the shadow-run
    columns are a separate diagnostic concern (`shadow_decision`/`actuator_
    decision` with `simulated=True`) outside this view's scope."""

    device_id: int
    device_name: str
    kind: str
    phase: str | None
    phase_deadline_at: datetime | None
    on_seconds: int | None
    off_seconds: int | None
    cycle_source: str | None
    handover_attempted: bool
    handover_result: str | None
    handover_status_text: str
    restore_attempted: bool
    restore_result: str | None
    restore_status_text: str


@dataclass(frozen=True)
class SourceComparisonView:
    """`SensorFailureSourceComparison` aggregated per Ersatzquelle-Thermostat
    over `COMPARISON_WINDOW_DAYS` -- the calibration aid behind
    `temperature_backup_offset_k` (Entscheidung R2). Echo-rows (the candidate
    reading its own just-written setpoint back, not an independent
    temperature) are excluded: they would pull the mean toward zero for a
    reason that has nothing to do with calibration."""

    device_id: int
    device_name: str
    sample_count: int
    mean_deviation_k: Decimal | None
    suggested_offset_text: str | None


@dataclass(frozen=True)
class ZoneEmergencyView:
    zone_id: int
    zone_name: str
    stage: str
    stage_label: str
    episode_id: int | None
    failure_started_at: datetime | None
    active_source_device_id: int | None
    active_source_device_name: str | None
    source_measured_at: datetime | None
    sensor_timeout_seconds: int | None
    recovery_started_at: datetime | None
    recovery_sample_count: int
    recovery_samples: int | None
    emergency_setpoint_c: Decimal | None
    outdoor: OutdoorReading | None
    actuators: tuple[ActuatorEmergencyView, ...]
    comparisons: tuple[SourceComparisonView, ...]
    banner: emergency_display.ZoneEmergencyBanner | None


def entered_notice_text(view: ZoneEmergencyView) -> str:
    """The body text for `domain.fault_notice.emergency_entered_notice` --
    plan Auftrag 8: "mit tatsächlicher Strategie (Ersatzquelle oder
    Notbetrieb, welche Aktoren wie)". Built from the same `ZoneEmergencyView`
    the control page renders, so the notification can never claim a strategy
    the control page does not also show (Grundsatz 6).
    """
    if view.stage == emergency_operation.STAGE_ERSATZQUELLE:
        name = view.active_source_device_name or "ein anderes Thermostat"
        return (
            f"Der Wandfühler liefert keinen aktuellen Wert mehr. Die Zone "
            f"regelt vorübergehend nach der Temperatur von {name}."
        )
    lines = [
        "Kein Temperaturwert verfügbar. Die Heizung läuft im Notbetrieb:"
    ]
    for actuator in view.actuators:
        if actuator.kind == "thermostat":
            setpoint = view.emergency_setpoint_c
            lines.append(
                f"- {actuator.device_name}: regelt selbst auf einen Notsollwert"
                + (f" von {setpoint} °C." if setpoint is not None else ".")
            )
        else:
            on_m = round((actuator.on_seconds or 0) / 60)
            off_m = round((actuator.off_seconds or 0) / 60)
            lines.append(
                f"- {actuator.device_name}: taktet {on_m}/{off_m} min "
                f"({'Kennlinie' if actuator.cycle_source == 'kennlinie' else 'Festtakt'})."
            )
    if not view.actuators:
        lines.append("- noch kein Aktor zugeordnet.")
    return "\n".join(lines)


def _comparisons(
    session: Session, zone: Zone, now: datetime, device_names: dict[int, str]
) -> tuple[SourceComparisonView, ...]:
    since = now - timedelta(days=COMPARISON_WINDOW_DAYS)
    rows = session.scalars(
        select(SensorFailureSourceComparison).where(
            SensorFailureSourceComparison.zone_id == zone.id,
            SensorFailureSourceComparison.measured_at >= since,
            SensorFailureSourceComparison.usable.is_(True),
            SensorFailureSourceComparison.echo.is_(False),
            SensorFailureSourceComparison.wall_probe_c.is_not(None),
            SensorFailureSourceComparison.raw_c.is_not(None),
        )
    ).all()
    by_device: dict[int, list[Decimal]] = {}
    names: dict[int, str] = {}
    for row in rows:
        if row.device_id is None:
            continue
        assert row.wall_probe_c is not None and row.raw_c is not None  # narrowed above
        by_device.setdefault(row.device_id, []).append(row.wall_probe_c - row.raw_c)
        names[row.device_id] = row.device_name

    views: list[SourceComparisonView] = []
    for device_id, deviations in by_device.items():
        mean = sum(deviations, Decimal(0)) / len(deviations)
        views.append(
            SourceComparisonView(
                device_id=device_id,
                device_name=device_names.get(device_id, names[device_id]),
                sample_count=len(deviations),
                mean_deviation_k=mean,
                suggested_offset_text=emergency_display.suggested_offset_text(mean),
            )
        )
    views.sort(key=lambda view: view.device_name)
    return tuple(views)


def zone_emergency_view(
    session: Session, zone: Zone, now: datetime, setting_row: Setting | None
) -> ZoneEmergencyView:
    """Everything the control page and REST/MCP show for one zone's Notbetrieb
    state. Never raises for a zone that has never seen a failure -- `stage`
    falls back to `"normal"` and every other field to its "nothing recorded"
    default, exactly like a fresh `ZoneSensorFailureState` row would.
    """
    db_state = session.get(ZoneSensorFailureState, zone.id)
    stage = db_state.stage if db_state is not None else emergency_operation.STAGE_NORMAL
    episode = (
        session.get(SensorFailureEpisode, db_state.episode_id)
        if db_state is not None and db_state.episode_id is not None
        else None
    )
    source_device = (
        session.get(Device, db_state.active_source_device_id)
        if db_state is not None and db_state.active_source_device_id is not None
        else None
    )

    assignments = zone_actuator_assignments(session, zone)
    device_names = {device.id: device.display_name for _, device, _ in assignments}
    actuator_views: list[ActuatorEmergencyView] = []
    for zone_device, device, kind in assignments:
        emergency_row = session.get(ActuatorEmergencyState, zone_device.id)
        handover_attempted = (
            emergency_row is not None and emergency_row.handover_attempted_at is not None
        )
        restore_attempted = (
            emergency_row is not None and emergency_row.restore_attempted_at is not None
        )
        actuator_views.append(
            ActuatorEmergencyView(
                device_id=device.id,
                device_name=device.display_name,
                kind=kind,
                phase=emergency_row.phase if emergency_row is not None else None,
                phase_deadline_at=(
                    emergency_row.phase_deadline_at if emergency_row is not None else None
                ),
                on_seconds=emergency_row.on_seconds if emergency_row is not None else None,
                off_seconds=emergency_row.off_seconds if emergency_row is not None else None,
                cycle_source=(
                    emergency_row.cycle_source if emergency_row is not None else None
                ),
                handover_attempted=handover_attempted,
                handover_result=(
                    emergency_row.handover_result if emergency_row is not None else None
                ),
                handover_status_text=emergency_display.handover_status_text(
                    handover_attempted,
                    emergency_row.handover_result if emergency_row is not None else None,
                ),
                restore_attempted=restore_attempted,
                restore_result=(
                    emergency_row.restore_result if emergency_row is not None else None
                ),
                restore_status_text=emergency_display.handover_status_text(
                    restore_attempted,
                    emergency_row.restore_result if emergency_row is not None else None,
                ),
            )
        )

    outdoor = outdoor_reading(session, setting_row, now) if setting_row is not None else None

    primary_actuator = actuator_views[0] if actuator_views else None
    banner = emergency_display.zone_banner(
        stage=stage,
        actuator_kind=primary_actuator.kind if primary_actuator is not None else None,
        source_device_name=(
            source_device.display_name if source_device is not None else None
        ),
        emergency_setpoint_c=episode.emergency_setpoint_c if episode is not None else None,
        cycle_phase=primary_actuator.phase if primary_actuator is not None else None,
        on_seconds=primary_actuator.on_seconds if primary_actuator is not None else None,
        off_seconds=primary_actuator.off_seconds if primary_actuator is not None else None,
        cycle_source=primary_actuator.cycle_source if primary_actuator is not None else None,
        outdoor_c=outdoor.temperature_c if outdoor is not None else None,
        recovery_sample_count=db_state.recovery_sample_count if db_state is not None else 0,
        recovery_samples=episode.recovery_samples if episode is not None else None,
    )

    return ZoneEmergencyView(
        zone_id=zone.id,
        zone_name=zone.display_name,
        stage=stage,
        stage_label=emergency_display.STAGE_LABELS.get(stage, stage),
        episode_id=episode.id if episode is not None else None,
        failure_started_at=db_state.failure_started_at if db_state is not None else None,
        active_source_device_id=(
            db_state.active_source_device_id if db_state is not None else None
        ),
        active_source_device_name=(
            source_device.display_name if source_device is not None else None
        ),
        source_measured_at=db_state.source_measured_at if db_state is not None else None,
        sensor_timeout_seconds=(
            episode.sensor_timeout_seconds if episode is not None else None
        ),
        recovery_started_at=db_state.recovery_started_at if db_state is not None else None,
        recovery_sample_count=db_state.recovery_sample_count if db_state is not None else 0,
        recovery_samples=episode.recovery_samples if episode is not None else None,
        emergency_setpoint_c=episode.emergency_setpoint_c if episode is not None else None,
        outdoor=outdoor,
        actuators=tuple(actuator_views),
        comparisons=_comparisons(session, zone, now, device_names),
        banner=banner,
    )
