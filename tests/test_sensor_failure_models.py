from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS
from thermoctl.db.models.sensor_failure import SensorFailureCurvePoint, SensorFailureProfile


def profile(session: Session) -> SensorFailureProfile:
    value = SensorFailureProfile(
        name="Prüfprofil",
        version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
    )
    session.add(value)
    session.flush()
    return value


def test_curve_temperature_is_unique_within_profile(session: Session) -> None:
    first = profile(session)
    second = profile(session)
    for item in (first, second):
        session.add(
            SensorFailureCurvePoint(
                profile_id=item.id,
                outdoor_c=Decimal("0"),
                on_seconds=600,
                off_seconds=1200,
            )
        )
    session.flush()
    session.add(
        SensorFailureCurvePoint(
            profile_id=first.id,
            outdoor_c=Decimal("0"),
            on_seconds=0,
            off_seconds=1800,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("off_seconds", [0, -1])
@pytest.mark.parametrize("curve", [False, True])
def test_off_duration_must_be_positive(
    session: Session,
    off_seconds: int,
    curve: bool,
) -> None:
    item = profile(session)
    if curve:
        session.add(
            SensorFailureCurvePoint(
                profile_id=item.id,
                outdoor_c=Decimal("15"),
                on_seconds=0,
                off_seconds=off_seconds,
            )
        )
    else:
        item.fixed_off_seconds = off_seconds
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_warm_endpoint_survives_roundtrip_and_profile_delete_cascades(session: Session) -> None:
    item = profile(session)
    point = SensorFailureCurvePoint(
        profile_id=item.id,
        outdoor_c=Decimal("15.25"),
        on_seconds=0,
        off_seconds=1800,
    )
    session.add(point)
    session.flush()
    point_id = point.id
    session.expire_all()
    assert point.outdoor_c == Decimal("15.25")
    assert point.on_seconds == 0
    session.delete(item)
    session.flush()
    session.expunge_all()
    assert session.get(SensorFailureCurvePoint, point_id) is None


def test_deletion_preserves_history_and_removes_runtime_state(session: Session) -> None:
    from datetime import datetime

    from tests.helpers import create_device, create_device_command, create_zone, role
    from thermoctl.db.models.device import ZoneDevice
    from thermoctl.db.models.sensor_failure import (
        ActuatorDecision,
        ActuatorEmergencyState,
        SensorFailureEpisode,
        ZoneSensorFailureState,
    )

    zone = create_zone(session, "notbetrieb")
    device = create_device(session, "notbetrieb-aktor")
    assignment = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
    )
    session.add(assignment)
    session.flush()
    now = datetime(2026, 9, 28, 12)
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=now,
        trigger_kind="alle_quellen",
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
        source_device_id=device.id,
        source_device_name=device.display_name,
    )
    session.add(episode)
    session.flush()
    state = ActuatorEmergencyState(
        zone_device_id=assignment.id,
        episode_id=episode.id,
        profile_version=1,
        simulated_phase="ein",
        simulated_phase_deadline_at=now,
        simulated_on_seconds=600,
        simulated_off_seconds=1200,
        simulated_handover_attempted_at=now,
        simulated_last_command_at=now,
        simulated_last_command_state=True,
    )
    decision = ActuatorDecision(
        episode_id=episode.id,
        zone_device_id=assignment.id,
        zone_name=zone.display_name,
        device_name=device.display_name,
        decided_at=now,
        action="no_write",
        reason_code="notbetrieb_schweigen",
        reason="Thermostat übernimmt",
        simulated=True,
        profile_version=1,
    )
    session.add_all(
        [
            state,
            decision,
            ZoneSensorFailureState(
                zone_id=zone.id,
                episode_id=episode.id,
                stage="notbetrieb",
                active_source_device_id=device.id,
                failure_started_at=now,
            ),
        ]
    )
    session.flush()
    command = create_device_command(session, zone, device)
    command.episode_id = episode.id
    command.actuator_decision_id = decision.id
    command.reason_code = "notbetrieb_uebergabe"
    session.flush()
    command_id = command.id
    session.expire_all()
    # Simulated success must never establish a real attempt or switch deadline.
    assert state.handover_attempted_at is None
    assert state.last_successful_command_at is None
    assert state.phase_deadline_at is None
    assert state.simulated_last_command_at == now
    zone_id, assignment_id, episode_id, decision_id = (
        zone.id,
        assignment.id,
        episode.id,
        decision.id,
    )
    session.delete(zone)
    session.flush()
    session.expunge_all()
    assert session.get(ZoneSensorFailureState, zone_id) is None
    assert session.get(ActuatorEmergencyState, assignment_id) is None
    saved_episode = session.get(SensorFailureEpisode, episode_id)
    saved_decision = session.get(ActuatorDecision, decision_id)
    assert saved_episode is not None and saved_decision is not None
    assert saved_episode.zone_id is None
    assert saved_episode.zone_name == "Notbetrieb"
    assert saved_episode.fixed_off_seconds == 1200
    assert saved_decision.zone_device_id is None
    assert saved_decision.zone_name == "Notbetrieb"
    assert saved_decision.action == "no_write"
    session.delete(saved_episode)
    session.flush()
    session.expire_all()
    assert saved_decision.episode_id is None
    assert saved_decision.reason == "Thermostat übernimmt"
    from thermoctl.db.models.state import DeviceCommand

    saved_command = session.get(DeviceCommand, command_id)
    assert saved_command is not None
    assert saved_command.episode_id is None
    assert saved_command.actuator_decision_id == decision_id
    session.delete(saved_decision)
    session.flush()
    session.expire_all()
    assert saved_command.actuator_decision_id is None
    assert saved_command.reason_code == "notbetrieb_uebergabe"
    assert saved_command.payload == '{"occupied_heating_setpoint": 21.0}'


def test_unknown_episode_cannot_be_referenced(session: Session) -> None:
    from tests.helpers import create_zone
    from thermoctl.db.models.sensor_failure import ZoneSensorFailureState

    zone = create_zone(session, "unbekannte-episode")
    session.add(ZoneSensorFailureState(zone_id=zone.id, episode_id=987654))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("stage", ["unknown", "unbekannt"])
def test_invalid_source_stage_is_rejected(session: Session, stage: str) -> None:
    from tests.helpers import CONSTRAINT_ERRORS, create_zone
    from thermoctl.db.models.sensor_failure import ZoneSensorFailureState

    zone = create_zone(session, "ungueltig")
    session.add(ZoneSensorFailureState(zone_id=zone.id, stage=stage))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_deleted_profile_restores_inheritance_without_enabling_zone(session: Session) -> None:
    from tests.helpers import create_settings, create_zone

    item = profile(session)
    settings = create_settings(session)
    zone = create_zone(session, "profilvererbung")
    settings.sensor_failure_default_profile_id = item.id
    zone.sensor_failure_profile_id = item.id
    zone.sensor_failure_emergency_setpoint_c = Decimal("19.50")
    session.flush()
    session.delete(item)
    session.flush()
    session.expire_all()
    assert settings.sensor_failure_default_profile_id is None
    assert settings.sensor_failure_default_emergency_setpoint_c == Decimal("20")
    assert zone.sensor_failure_profile_id is None
    assert zone.sensor_failure_emergency_setpoint_c == Decimal("19.50")
    assert zone.sensor_failure_enabled is False
