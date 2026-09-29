"""Wiring Notbetrieb (Auftrag 7a of `lokal/plaene/0.11.0-notbetrieb.md`) into
the shadow run -- shadow-only, no dispatch change (Auftrag 7b).

Covers the required scenario end to end with a simulated clock: wall probe
fails -> Ersatzquelle (thermostat, no echo) -> both sources fail -> Notbetrieb
(handover exactly once, still exactly once after a simulated process restart
-- a brand-new `Session` against the same committed database, not merely a
second call on the same one), switch-actuator Takt decisions across at least
two Aus/Ein pairs, and recovery after two measurements/60s back to `normal`.
Separate, smaller tests cover the `enabled=False` regression requirement and
PI neutralisation during an active emergency stage.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import capability, create_settings, create_zone, role, sensor_status_of
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import (
    ActuatorDecision,
    ActuatorEmergencyState,
    SensorFailureEpisode,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain import emergency_actuator_plan, emergency_cycle, emergency_operation
from thermoctl.domain.pi_control import RESET_REASON_SENSOR_FAILURE
from thermoctl.services import shadow_run

NOW = datetime(2026, 9, 29, 12, 0, 0)


def _own_database(tmp_path: Path, name: str) -> tuple[Engine, sessionmaker[Session]]:
    """A dedicated, named SQLite file -- the only way to open a genuinely
    *second*, independent `Session` against the same already-committed data,
    which the single-handover-per-episode guarantee has to survive. Same
    pattern `tests/test_shadow_run.py::_own_database` uses for the same
    reason."""
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db", future=True)
    Base.metadata.create_all(engine)
    return engine, session_factory(engine)


def _link(session: Session, device: Device, code: str) -> None:
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id)
    )
    session.flush()


def _thermostat_profile(session: Session) -> int:
    """A small, fast-cycling profile -- Festtakt only (no Kennlinie points),
    bypassing `sensor_failure_policy.save_profile`'s validation on purpose:
    this test exercises the shadow-run wiring, not Auftrag 4's own validation
    (already covered by `tests/test_sensor_failure_policy.py`)."""
    from thermoctl.db.models.sensor_failure import SensorFailureProfile

    profile = SensorFailureProfile(
        name="Testprofil",
        version=1,
        fixed_on_seconds=20,
        fixed_off_seconds=30,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1.00"),
    )
    session.add(profile)
    session.flush()
    return profile.id


def _setup_zone(session: Session) -> tuple[Zone, Device, Device, Device]:
    """A zone shaped like the real "Bad" zone (geraetevertrag.md (e)): a wall
    probe, one thermostat actuator (self-regulating, echo-free -- no
    `DeviceCommand` for it in this test) and one switch actuator."""
    create_settings(session)
    zone = create_zone(session, "bad")
    zone.min_on_seconds = 10
    zone.min_off_seconds = 10
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _thermostat_profile(session)
    zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))

    wall = Device(
        integration_id=_integration_id(session),
        external_id="bad-wand",
        display_name="Bad Wandfühler",
    )
    session.add(wall)
    session.flush()
    _link(session, wall, "temperature")
    zone.temperature_source_device_id = wall.id

    trv = Device(
        integration_id=_integration_id(session), external_id="bad-trv", display_name="Bad TRV"
    )
    session.add(trv)
    session.flush()
    _link(session, trv, "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=True,
        )
    )

    relais = Device(
        integration_id=_integration_id(session), external_id="bad-relais", display_name="Bad Relais"
    )
    session.add(relais)
    session.flush()
    _link(session, relais, "switch")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=relais.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()
    return zone, wall, trv, relais


def _add_schedule(session: Session, zone: Zone, *, temperature_c: Decimal) -> None:
    """One setpoint mode, one fixed setpoint for it, one schedule point covering
    every cycle's weekday -- just enough for `resolved_setpoint()` to resolve
    something other than the frost setpoint; created once per zone (the mode's
    `code` column is unique)."""
    from thermoctl.db.models.zone import SetpointMode

    mode = SetpointMode(code=f"heizen-{zone.name}", name="Heizen")
    session.add(mode)
    session.flush()
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=temperature_c)
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=NOW.weekday(), minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()


def _integration_id(session: Session) -> int:
    from tests.helpers import integration

    return integration(session).id


def _wall_state(session: Session, zone: Zone, *, temperature_c: Decimal, at: datetime) -> ZoneState:
    row = session.get(ZoneState, zone.id)
    status_ok = sensor_status_of(session, "ok").id
    if row is None:
        row = ZoneState(
            zone_id=zone.id,
            temperature_c=temperature_c,
            measured_at=at,
            sensor_status_id=status_ok,
            updated_at=at,
        )
        session.add(row)
    else:
        row.temperature_c = temperature_c
        row.measured_at = at
        row.sensor_status_id = status_ok
        row.updated_at = at
    session.flush()
    return row


def _trv_measurement(session: Session, device: Device, *, value: Decimal, at: datetime) -> None:
    temperature = capability(session, "temperature")
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=temperature.id,
            value_numeric=value,
            measured_at=at,
            received_at=at,
        )
    )
    session.flush()


def _row_for(rows: list[ShadowDecision], zone: Zone) -> ShadowDecision:
    return next(r for r in rows if r.zone_id == zone.id)


# --- enabled=False regression -------------------------------------------------


def test_disabled_zone_never_touches_sensor_failure_tables(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "kein-notbetrieb")
    assert zone.sensor_failure_enabled is False
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))
    _wall_state(session, zone, temperature_c=Decimal("18.0"), at=NOW)

    rows = shadow_run.cycle(session, NOW)

    row = _row_for(rows, zone)
    assert row.outcome_code != emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB
    assert session.get(ZoneSensorFailureState, zone.id) is None
    assert (
        session.scalar(
            select(SensorFailureEpisode).where(SensorFailureEpisode.zone_id == zone.id)
        )
        is None
    )


# --- full episode --------------------------------------------------------------


def test_full_episode_ersatzquelle_notbetrieb_takt_restart_and_recovery(
    tmp_path: Path,
) -> None:
    engine, maker = _own_database(tmp_path, "notbetrieb")
    session = maker()
    try:
        zone, _wall, trv, _relais = _setup_zone(session)
        zone_id = zone.id
        trv_id = trv.id
        session.commit()

        # t0 -- wall probe healthy: ordinary NORMAL cycle, no sensor-failure
        # machinery visibly engaged beyond the (always-created, for an
        # enabled zone) `stage='normal'` bookkeeping row.
        t0 = NOW
        _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0)
        _trv_measurement(session, trv, value=Decimal("22.0"), at=t0)
        session.commit()
        row0 = _row_for(shadow_run.cycle(session, t0), zone)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone_id)
        assert sf_state is not None
        assert sf_state.stage == emergency_operation.STAGE_NORMAL
        assert sf_state.episode_id is None
        assert row0.outcome_code != emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB

        # t1 -- wall probe has gone silent (measured_at still t0, now stale
        # against the zone's 90s timeout); the TRV has a fresh, echo-free
        # reading -> Ersatzquelle.
        t1 = t0 + timedelta(seconds=100)
        _trv_measurement(session, trv, value=Decimal("22.5"), at=t1)
        session.commit()
        row1 = _row_for(shadow_run.cycle(session, t1), zone)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone_id)
        assert sf_state.stage == emergency_operation.STAGE_ERSATZQUELLE
        assert sf_state.episode_id is not None
        episode_id = sf_state.episode_id
        episode = session.get(SensorFailureEpisode, episode_id)
        assert episode.trigger_kind == emergency_operation.TRIGGER_WANDFUEHLER
        assert episode.ended_at is None
        assert row1.temperature_c == Decimal("22.5")
        assert "Ersatzquelle aktiv" in row1.reason

        # t2 -- the TRV's reading is now stale too (last one was t1) -> both
        # sources gone -> Notbetrieb. Same, still-open episode.
        t2 = t1 + timedelta(seconds=100)
        session.commit()
        row2 = _row_for(shadow_run.cycle(session, t2), zone)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone_id)
        assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB
        assert sf_state.episode_id == episode_id
        assert sf_state.handover_due_signalled is True
        assert row2.outcome_code == emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB
        assert row2.would_heat is False  # Notbetriebstakt startet mit Aus

        decisions_t2 = session.scalars(
            select(ActuatorDecision).where(ActuatorDecision.decided_at == t2)
        ).all()
        assert len(decisions_t2) == 2
        thermostat_decision = next(
            d for d in decisions_t2 if d.action in (emergency_actuator_plan.ACTION_HANDOVER,)
        )
        assert thermostat_decision.action == emergency_actuator_plan.ACTION_HANDOVER
        switch_decision_t2 = next(
            d for d in decisions_t2 if d.action in (emergency_actuator_plan.ACTION_SWITCH_OFF,)
        )
        assert switch_decision_t2.phase == emergency_cycle.PHASE_OFF
        assert switch_decision_t2.phase_deadline_at == t2 + timedelta(seconds=30)
        assert switch_decision_t2.cycle_source == emergency_cycle.SOURCE_FIXED

        trv_state = session.get(ActuatorEmergencyState, _zone_device_id(session, zone_id, trv_id))
        assert trv_state.simulated_handover_attempted_at == t2

        # t3 -- past the 30s Aus deadline: switch turns Ein (pair 1's second
        # leg); thermostat stays silent (already handed over this episode).
        t3 = t2 + timedelta(seconds=31)
        session.commit()
        row3 = _row_for(shadow_run.cycle(session, t3), zone)
        session.commit()
        assert row3.would_heat is True
        decisions_t3 = session.scalars(
            select(ActuatorDecision).where(ActuatorDecision.decided_at == t3)
        ).all()
        thermostat_decision_t3 = next(
            d for d in decisions_t3 if d.zone_device_id != switch_decision_t2.zone_device_id
        )
        assert thermostat_decision_t3.action == emergency_actuator_plan.ACTION_NO_WRITE
        switch_decision_t3 = next(
            d for d in decisions_t3 if d.zone_device_id == switch_decision_t2.zone_device_id
        )
        assert switch_decision_t3.phase == emergency_cycle.PHASE_ON
        assert switch_decision_t3.phase_deadline_at == t3 + timedelta(seconds=20)

        # t4 -- past the 20s Ein deadline: switch turns Aus again -- the
        # second pair's own Aus leg (>= 2 Aus/Ein pairs observed: t2 aus, t3
        # ein, t4 aus).
        t4 = t3 + timedelta(seconds=21)
        session.commit()
        row4 = _row_for(shadow_run.cycle(session, t4), zone)
        session.commit()
        assert row4.would_heat is False
        switch_decision_t4 = next(
            d
            for d in session.scalars(
                select(ActuatorDecision).where(ActuatorDecision.decided_at == t4)
            ).all()
            if d.zone_device_id == switch_decision_t2.zone_device_id
        )
        assert switch_decision_t4.phase == emergency_cycle.PHASE_OFF
        assert switch_decision_t4.phase_deadline_at == t4 + timedelta(seconds=30)

        handover_count_before_restart = len(
            session.scalars(
                select(ActuatorDecision).where(
                    ActuatorDecision.action == emergency_actuator_plan.ACTION_HANDOVER
                )
            ).all()
        )
        assert handover_count_before_restart == 1
        session.close()

        # --- simulated process restart: a brand-new Session against the
        # same committed database -- nothing about the single-handover latch
        # may live only in the first Session's Python objects.
        restart_session = maker()
        try:
            zone = restart_session.get(Zone, zone_id)
            t5 = t4 + timedelta(seconds=5)
            _wall_state(restart_session, zone, temperature_c=Decimal("20.0"), at=t5)
            restart_session.commit()
            row5 = _row_for(shadow_run.cycle(restart_session, t5), zone)
            restart_session.commit()

            sf_state = restart_session.get(ZoneSensorFailureState, zone_id)
            assert sf_state.stage == emergency_operation.STAGE_RUECKKEHRPRUEFUNG
            assert sf_state.episode_id == episode_id
            handover_count_after_restart = len(
                restart_session.scalars(
                    select(ActuatorDecision).where(
                        ActuatorDecision.action == emergency_actuator_plan.ACTION_HANDOVER
                    )
                ).all()
            )
            assert handover_count_after_restart == 1  # kein zweiter Versuch
            assert row5.outcome_code == emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB

            # t6 -- a second, distinct wall-probe measurement, 61s after the
            # recovery attempt started at t5: recovery completes (>= 2
            # Messungen, >= 60s durchgehend brauchbar), episode ends.
            t6 = t5 + timedelta(seconds=61)
            _wall_state(restart_session, zone, temperature_c=Decimal("20.1"), at=t6)
            restart_session.commit()
            row6 = _row_for(shadow_run.cycle(restart_session, t6), zone)
            restart_session.commit()

            sf_state = restart_session.get(ZoneSensorFailureState, zone_id)
            assert sf_state.stage == emergency_operation.STAGE_NORMAL
            assert sf_state.episode_id is None
            episode = restart_session.get(SensorFailureEpisode, episode_id)
            assert episode.ended_at == t6
            assert row6.outcome_code != emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB
        finally:
            restart_session.close()
    finally:
        engine.dispose()


def _zone_device_id(session: Session, zone_id: int, device_id: int) -> int:
    row = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == zone_id, ZoneDevice.device_id == device_id)
    )
    assert row is not None
    return row.id


# --- PI neutralisation ----------------------------------------------------------


def test_pi_stays_neutral_while_an_emergency_stage_is_active(session: Session) -> None:
    zone, _wall, trv, _relais = _setup_zone(session)
    zone.pi_enabled = True
    zone.pi_gain_per_k = Decimal("0.25")
    zone.pi_integral_time_minutes = 180
    zone.pi_min_on_seconds = 60
    zone.pi_min_off_seconds = 60
    session.flush()

    t0 = NOW
    state = _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0)
    state.pi_last_control_armed = False
    state.pi_last_evaluated_at = t0 - timedelta(seconds=60)
    _trv_measurement(session, trv, value=Decimal("22.0"), at=t0)
    session.flush()
    shadow_run.cycle(session, t0)

    t1 = t0 + timedelta(seconds=100)
    _trv_measurement(session, trv, value=Decimal("22.5"), at=t1)
    row1 = _row_for(shadow_run.cycle(session, t1), zone)

    assert row1.effective_controller == "hysteresis"
    assert row1.pi_reset_reason == RESET_REASON_SENSOR_FAILURE
    assert row1.pi_integrator_action is not None


# --- Notbetrieb ohne jede Aktorzuordnung -----------------------------------------


def test_notbetrieb_without_any_actuator_assignment_reports_no_switch(
    session: Session,
) -> None:
    """A zone with `sensor_failure_enabled` but no actuator assignment at all
    (and, crucially, the `actuator` `DeviceRole` itself never created in this
    test's database, not merely unused -- same pattern
    `tests/test_shadow_run.py::test_on_off_actuators_only_is_false_for_a_zone_
    without_any_actuator` already uses for the analogous existing guard):
    `zone_candidates()` finds no replacement candidates (straight to
    Notbetrieb, no Ersatzquelle stage) and `_zone_actuator_assignments()`
    finds no assignments either -- the `has_switch=False` branch of
    `_apply_emergency_actuators` (plan Auftrag 7a, item 3: a zone with no
    Schaltausgang stays informatively `would_heat=False`)."""
    create_settings(session)
    zone = create_zone(session, "ohne-aktoren")
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _thermostat_profile(session)
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))

    t0 = NOW
    _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0)
    shadow_run.cycle(session, t0)

    t1 = t0 + timedelta(seconds=100)
    row1 = _row_for(shadow_run.cycle(session, t1), zone)

    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB
    assert row1.outcome_code == emergency_actuator_plan.OUTCOME_CODE_NOTBETRIEB
    assert row1.would_heat is False
    assert "kein Schaltausgang" in row1.reason
