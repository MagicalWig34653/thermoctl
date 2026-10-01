"""Wiring Notbetrieb (Auftrag 7a of `lokal/plaene/0.11.0-notbetrieb.md`) into
the shadow run -- shadow-only, no dispatch change (Auftrag 7b).

Hauptsession review of 88bc87a (2026-09-30) overruled the original plan text
(Auftrag 7a item 3) on one point: `ShadowDecision.would_heat`/`outcome_code`/
`reason` for a zone in `notbetrieb`/`rueckkehrpruefung` must stay **exactly**
what `decide()` + PI would already give without any of this module existing
-- that is exactly what the existing publisher
(`services/publishing.py::_latest_decision`/`_send_actuator_switches`) reads
and sends the moment an installation runs scharf. The per-actuator
handover/Takt plan is persisted *only* to `actuator_decision`/
`actuator_emergency_state` (`simulated=True`), never fed back into the
zone's own decision. `tests/test_publishing_sensor_failure.py` proves this at
the publisher boundary itself (Fake-Transport write-call counting); this
file proves it at the `shadow_run` boundary and covers the state machine,
actuator persistence, PI neutralisation, the Taktquelle/Wiederanlaufsperre
persistence (Auftrag 7a follow-up item 4) and the Ersatzquelle<->Wandfühler
comparison log (item 5).
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
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.sensor_failure import (
    ActuatorDecision,
    ActuatorEmergencyState,
    SensorFailureEpisode,
    SensorFailureProfile,
    SensorFailureSourceComparison,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain import emergency_actuator_plan, emergency_cycle, emergency_operation
from thermoctl.domain.control_loop import Decision, Situation, decide
from thermoctl.domain.pi_control import RESET_REASON_SENSOR_FAILURE
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.zone_settings import control_parameters
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


def _thermostat_profile(
    session: Session, *, curve_points: tuple[tuple[Decimal, int, int], ...] = ()
) -> int:
    """A small, fast-cycling profile -- Festtakt by default, or the given
    Kennlinie points -- bypassing `sensor_failure_policy.save_profile`'s
    validation on purpose: this test exercises the shadow-run wiring, not
    Auftrag 4's own validation (already covered by
    `tests/test_sensor_failure_policy.py`)."""
    profile = SensorFailureProfile(
        name=f"Testprofil {id(session)}-{len(curve_points)}",
        version=1,
        fixed_on_seconds=20,
        fixed_off_seconds=30,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1.00"),
    )
    session.add(profile)
    session.flush()
    if curve_points:
        from thermoctl.db.models.sensor_failure import SensorFailureCurvePoint

        session.add_all(
            SensorFailureCurvePoint(
                profile_id=profile.id, outdoor_c=outdoor_c, on_seconds=on_s, off_seconds=off_s
            )
            for outdoor_c, on_s, off_s in curve_points
        )
        session.flush()
    return profile.id


def _integration_id(session: Session) -> int:
    from tests.helpers import integration

    return integration(session).id


def _add_schedule(session: Session, zone: Zone, *, temperature_c: Decimal) -> None:
    """One setpoint mode, one fixed setpoint for it, one schedule point covering
    every cycle's weekday -- just enough for `resolved_setpoint()` to resolve
    something other than the frost setpoint; created once per zone (the mode's
    `code` column is unique)."""
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
        integration_id=_integration_id(session),
        external_id="bad-relais",
        display_name="Bad Relais",
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


def _wall_state(
    session: Session, zone: Zone, *, temperature_c: Decimal | None, at: datetime, status: str = "ok"
) -> ZoneState:
    row = session.get(ZoneState, zone.id)
    status_id = sensor_status_of(session, status).id
    if row is None:
        row = ZoneState(
            zone_id=zone.id,
            temperature_c=temperature_c,
            measured_at=at,
            sensor_status_id=status_id,
            updated_at=at,
        )
        session.add(row)
    else:
        row.temperature_c = temperature_c
        row.measured_at = at
        row.sensor_status_id = status_id
        row.updated_at = at
    session.flush()
    return row


def _mark_wall_stale(session: Session, zone: Zone) -> None:
    """Flips `ZoneState.sensor_status_id` to `veraltet` without touching the
    reading itself -- what `services/ingest.py::advance_zone_state` would do
    once the same reading ages past the zone's timeout, and exactly what
    `_process_zone`'s *ordinary* `decide()` path reads
    (`sensor_status_row.code`, never recomputed from `measured_at` itself).
    This test calls `shadow_run.cycle()` directly, bypassing ingest, so it
    has to set this explicitly to keep the ordinary decision path realistic.
    """
    row = session.get(ZoneState, zone.id)
    assert row is not None
    row.sensor_status_id = sensor_status_of(session, "veraltet").id
    session.flush()


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


def _expected_ordinary_decision(session: Session, zone: Zone, now: datetime) -> Decision:
    """Recomputes exactly what `decide()` (PI disabled) would give for `zone`
    at `now`, from the *same* building blocks `_process_zone` itself uses --
    an independent proof that the actual `ShadowDecision` was not overridden
    by the sensor-failure machinery, not a tautological restatement of it
    (every function called here is the production one; only their
    composition is duplicated, and only for this one assertion).
    """
    settings = session.get(Setting, 1)
    assert settings is not None
    state = session.get(ZoneState, zone.id)
    assert state is not None
    sensor_status_row = session.get(SensorStatus, state.sensor_status_id)
    assert sensor_status_row is not None
    window_open, window_closed_for_s = shadow_run._window_situation(session, zone, state, now)
    window_open_by_temperature = bool(state and state.window_open_by_temperature)
    setpoint = resolved_setpoint(session, zone, now)
    frost_c = shadow_run._frost_setpoint(session, zone, settings)
    parameter = control_parameters(session, zone)
    heating_now, held_for_s, _, _ = shadow_run._previous_state(session, zone.id, now)
    on_off_actuators_only = shadow_run._on_off_actuators_only(session, zone)
    situation = Situation(
        measured_c=state.temperature_c,
        setpoint_c=setpoint.temperature_c,
        setpoint_reason=setpoint.reason,
        frost_c=frost_c,
        operating_mode=zone.operating_mode.code,
        heating_now=heating_now,
        held_for_s=held_for_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        window_open_by_temperature=window_open_by_temperature,
        sensor_status=sensor_status_row.code,
        parameter=parameter,
        on_off_actuators_only=on_off_actuators_only,
    )
    return decide(situation)


def _assert_matches_ordinary_decision(session: Session, zone: Zone, now: datetime) -> None:
    """Computed *before* the cycle under test runs (`_previous_state` must see
    the same history the real cycle is about to see, not one extra row from
    itself), then compared against that cycle's actual `ShadowDecision`."""
    expected = _expected_ordinary_decision(session, zone, now)
    row = _row_for(shadow_run.cycle(session, now), zone)
    assert row.would_heat == expected.heating
    assert row.outcome_code == expected.reason_code
    assert row.reason == expected.reason
    return row


# --- enabled=False regression -------------------------------------------------


def test_disabled_zone_never_touches_sensor_failure_tables(session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "kein-notbetrieb")
    assert zone.sensor_failure_enabled is False
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))
    _wall_state(session, zone, temperature_c=Decimal("18.0"), at=NOW)

    shadow_run.cycle(session, NOW)

    assert session.get(ZoneSensorFailureState, zone.id) is None
    assert (
        session.scalar(
            select(SensorFailureEpisode).where(SensorFailureEpisode.zone_id == zone.id)
        )
        is None
    )


# --- full episode: state machine, actuator persistence, no zone-level override --


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

        # t0 -- wall probe healthy: ordinary NORMAL cycle.
        t0 = NOW
        _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0, status="ok")
        _trv_measurement(session, trv, value=Decimal("22.0"), at=t0)
        session.commit()
        _assert_matches_ordinary_decision(session, zone, t0)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone_id)
        assert sf_state is not None
        assert sf_state.stage == emergency_operation.STAGE_NORMAL
        assert sf_state.episode_id is None

        # t1 -- wall probe has gone silent (same reading, now stale against
        # the zone's 90s timeout, `ingest`-realistic via `_mark_wall_stale`);
        # the TRV has a fresh, echo-free reading -> Ersatzquelle. Zone-level
        # override *is* expected here (plan 1.4 Rang 2, unaffected by this
        # review) -- not compared against `_expected_ordinary_decision`.
        t1 = t0 + timedelta(seconds=100)
        _mark_wall_stale(session, zone)
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
        # sources gone -> Notbetrieb. Same, still-open episode. The zone-level
        # decision must be exactly `decide()`'s own answer (Hauptsession
        # review of 88bc87a) -- necessarily a sensor-failure fallback here,
        # since the *ordinary* path independently sees the same stale wall
        # reading.
        t2 = t1 + timedelta(seconds=100)
        session.commit()
        _assert_matches_ordinary_decision(session, zone, t2)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone_id)
        assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB
        assert sf_state.episode_id == episode_id
        assert sf_state.handover_due_signalled is True

        decisions_t2 = session.scalars(
            select(ActuatorDecision).where(ActuatorDecision.decided_at == t2)
        ).all()
        assert len(decisions_t2) == 2
        thermostat_decision = next(
            d for d in decisions_t2 if d.action == emergency_actuator_plan.ACTION_HANDOVER
        )
        switch_decision_t2 = next(
            d for d in decisions_t2 if d.action == emergency_actuator_plan.ACTION_SWITCH_OFF
        )
        assert switch_decision_t2.phase == emergency_cycle.PHASE_OFF
        assert switch_decision_t2.phase_deadline_at == t2 + timedelta(seconds=30)
        assert switch_decision_t2.cycle_source == emergency_cycle.SOURCE_FIXED
        assert switch_decision_t2.simulated is True
        assert thermostat_decision.simulated is True

        trv_state = session.get(ActuatorEmergencyState, _zone_device_id(session, zone_id, trv_id))
        assert trv_state.simulated_handover_attempted_at == t2
        # Scharfe Spalten bleiben unberührt -- Auftrag 7a schreibt nur simuliert.
        assert trv_state.handover_attempted_at is None

        # t3 -- past the 30s Aus deadline: switch turns Ein (pair 1's second
        # leg); thermostat stays silent (already handed over this episode).
        # Zone-level decision still matches ordinary `decide()`.
        t3 = t2 + timedelta(seconds=31)
        session.commit()
        _assert_matches_ordinary_decision(session, zone, t3)
        session.commit()
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
        _assert_matches_ordinary_decision(session, zone, t4)
        session.commit()
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
            _wall_state(restart_session, zone, temperature_c=Decimal("20.0"), at=t5, status="ok")
            restart_session.commit()
            _assert_matches_ordinary_decision(restart_session, zone, t5)
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

            # t6 -- a second, distinct wall-probe measurement, 61s after the
            # recovery attempt started at t5: recovery completes (>= 2
            # Messungen, >= 60s durchgehend brauchbar), episode ends.
            t6 = t5 + timedelta(seconds=61)
            _wall_state(
                restart_session, zone, temperature_c=Decimal("20.1"), at=t6, status="ok"
            )
            restart_session.commit()
            _assert_matches_ordinary_decision(restart_session, zone, t6)
            restart_session.commit()

            sf_state = restart_session.get(ZoneSensorFailureState, zone_id)
            assert sf_state.stage == emergency_operation.STAGE_NORMAL
            assert sf_state.episode_id is None
            episode = restart_session.get(SensorFailureEpisode, episode_id)
            assert episode.ended_at == t6
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


def test_notbetrieb_without_any_actuator_assignment_persists_nothing_for_actuators(
    session: Session,
) -> None:
    """A zone with `sensor_failure_enabled` but no actuator assignment at all
    (and, crucially, the `actuator` `DeviceRole` itself never created in this
    test's database, not merely unused -- same pattern
    `tests/test_shadow_run.py::test_on_off_actuators_only_is_false_for_a_zone_
    without_any_actuator` already uses for the analogous existing guard):
    `zone_candidates()` finds no replacement candidates (straight to
    Notbetrieb, no Ersatzquelle stage) and `_zone_actuator_assignments()`
    finds no assignments either -- `_apply_emergency_actuators` then simply
    persists nothing, and the zone-level decision still matches
    `decide()`'s own answer."""
    create_settings(session)
    zone = create_zone(session, "ohne-aktoren")
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _thermostat_profile(session)
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))

    t0 = NOW
    _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0, status="ok")
    shadow_run.cycle(session, t0)

    t1 = t0 + timedelta(seconds=100)
    _mark_wall_stale(session, zone)
    row1 = _assert_matches_ordinary_decision(session, zone, t1)

    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB
    assert (
        session.scalar(
            select(ActuatorDecision).where(ActuatorDecision.episode_id == sf_state.episode_id)
        )
        is None
    )
    assert row1.zone_id == zone.id  # sanity: the row asserted above is this zone's


# --- Taktquelle/Wiederanlaufsperre wirklich persistiert (Auftrag 7a, Punkt 4) ----


def test_warm_lock_persists_across_a_pair_boundary(session: Session) -> None:
    """The 15 °C Aus-Punkt of a Kennlinie, held across a pair boundary where
    the outdoor value has *dropped back into* the warm-restart-hysteresis
    band (but not below it) -- exactly the case that only holds if
    `CycleState.source`/`.warm_locked` are read back from persistence
    (`ActuatorEmergencyState.simulated_cycle_source`/`.simulated_warm_locked`)
    rather than re-derived live from this cycle's own outdoor value: recomputed
    live, `warm_locked_prev` is always `False`, so at 14.5 °C (inside the 1 K
    band, `is_warm_now` false because 14.5 < 15) the switch would incorrectly
    turn back on. Persisted, `warm_locked_prev=True` keeps the lock through
    the band (`exits_band` requires `< 15 - 1 = 14`), and the switch stays
    off. Confirmed by hand against `domain.emergency_cycle._resolve_pair`
    before writing this test -- the assertion below is exactly the point
    where the two implementations diverge.
    """
    create_settings(session)
    zone = create_zone(session, "warmlock")
    zone.min_on_seconds = 10
    zone.min_off_seconds = 10
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _thermostat_profile(
        session,
        curve_points=((Decimal("0"), 600, 1200), (Decimal("15"), 0, 1800)),
    )
    _add_schedule(session, zone, temperature_c=Decimal("21.0"))

    relais = Device(
        integration_id=_integration_id(session), external_id="warmlock-relais", display_name="R"
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
    outdoor = Device(
        integration_id=_integration_id(session), external_id="warmlock-outdoor", display_name="O"
    )
    session.add(outdoor)
    session.flush()
    _link(session, outdoor, "temperature")
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.outdoor_temperature_source_device_id = outdoor.id
    session.flush()

    def _outdoor_measurement(value: Decimal, at: datetime) -> None:
        temperature = capability(session, "temperature")
        session.add(
            Measurement(
                device_id=outdoor.id,
                capability_id=temperature.id,
                value_numeric=value,
                measured_at=at,
                received_at=at,
            )
        )
        session.flush()

    # t0 -- no wall probe at all, no thermostat candidate: straight to
    # Notbetrieb on the very first cycle. Outdoor 16 °C (above the 15 °C
    # Aus-Punkt) -> the switch's first pair locks warm immediately.
    t0 = NOW
    _outdoor_measurement(Decimal("16.0"), t0)
    shadow_run.cycle(session, t0)
    zone_device_id = _zone_device_id(session, zone.id, relais.id)
    state_t0 = session.get(ActuatorEmergencyState, zone_device_id)
    assert state_t0.simulated_phase == emergency_cycle.PHASE_OFF
    assert state_t0.simulated_warm_locked is True
    assert state_t0.simulated_cycle_source == emergency_cycle.SOURCE_CURVE
    deadline_t0 = state_t0.simulated_phase_deadline_at
    assert deadline_t0 == t0 + timedelta(seconds=1800)

    # t1 -- exactly at the pair boundary, outdoor now 14.5 °C: inside the
    # warm-restart-hysteresis band, not below it. The lock must hold.
    t1 = deadline_t0
    _outdoor_measurement(Decimal("14.5"), t1)
    shadow_run.cycle(session, t1)
    decision_t1 = session.scalar(
        select(ActuatorDecision).where(
            ActuatorDecision.zone_device_id == zone_device_id, ActuatorDecision.decided_at == t1
        )
    )
    assert decision_t1 is not None
    assert decision_t1.action == emergency_actuator_plan.ACTION_SWITCH_OFF
    assert decision_t1.reason_code == emergency_cycle.REASON_WARM_LOCK
    assert decision_t1.on_seconds == 0
    state_t1 = session.get(ActuatorEmergencyState, zone_device_id)
    assert state_t1.simulated_warm_locked is True


# --- Vergleichsprotokoll Ersatzquelle <-> Wandfühler (Plan Abschnitt 6, R2) ------


def test_source_comparison_is_logged_once_per_new_candidate_measurement(
    session: Session,
) -> None:
    zone, _wall, trv, _relais = _setup_zone(session)

    t0 = NOW
    _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0, status="ok")
    _trv_measurement(session, trv, value=Decimal("22.0"), at=t0)
    shadow_run.cycle(session, t0)

    rows_after_first = session.scalars(select(SensorFailureSourceComparison)).all()
    assert len(rows_after_first) == 1
    logged = rows_after_first[0]
    assert logged.zone_id == zone.id
    assert logged.device_id == trv.id
    assert logged.measured_at == t0
    assert logged.wall_probe_c == Decimal("19.0")
    assert logged.raw_c == Decimal("22.0")
    assert logged.echo is False
    # Der Kandidat selbst ist brauchbar (frisch, kein Echo) -- unabhängig
    # davon, dass der Wandfühler diesen Zyklus ohnehin brauchbar ist und
    # deshalb nicht als Ersatzquelle ausgewählt wird.
    assert logged.usable is True

    # t1 -- same candidate reading, no new measurement: cycling again without
    # a fresh candidate value must not grow the table.
    t1 = t0 + timedelta(seconds=30)
    shadow_run.cycle(session, t1)
    assert len(session.scalars(select(SensorFailureSourceComparison)).all()) == 1

    # t2 -- a genuinely new candidate measurement: exactly one more row.
    t2 = t1 + timedelta(seconds=30)
    _trv_measurement(session, trv, value=Decimal("22.3"), at=t2)
    shadow_run.cycle(session, t2)
    rows_after_third = session.scalars(select(SensorFailureSourceComparison)).all()
    assert len(rows_after_third) == 2
    assert {r.measured_at for r in rows_after_third} == {t0, t2}


def test_source_comparison_skips_a_candidate_never_measured(session: Session) -> None:
    """`_record_source_comparisons` never even has a `measured_at` to log
    against for a thermostat that has never sent a `temperature`
    measurement -- the candidate exists (assigned as an actuator), the
    comparison log simply has nothing to log yet."""
    zone, _wall, _trv, _relais = _setup_zone(session)
    t0 = NOW
    _wall_state(session, zone, temperature_c=Decimal("19.0"), at=t0, status="ok")
    # Deliberately no `_trv_measurement` call -- the candidate is assigned but
    # has never reported a reading.

    shadow_run.cycle(session, t0)

    assert session.scalars(select(SensorFailureSourceComparison)).all() == []


def test_source_comparison_is_not_logged_without_a_wall_probe_value_outside_ersatzquelle(
    session: Session,
) -> None:
    """No `ZoneState` at all (no wall-probe value) and a candidate that is
    itself unusable (stale) -- straight to `notbetrieb`, not `ersatzquelle`,
    so the "wall probe and candidate both have a value" write condition is
    never met and the "stage is ersatzquelle" alternative doesn't apply
    either: nothing gets logged this cycle."""
    zone, _wall, trv, _relais = _setup_zone(session)
    t0 = NOW
    # Stale by a wide margin (zone timeout is 90s) -- unusable, but still "a
    # value" in the sense the comparison log cares about; kept far enough in
    # the past that it cannot accidentally count as fresh.
    _trv_measurement(session, trv, value=Decimal("22.0"), at=t0 - timedelta(hours=1))

    rows = shadow_run.cycle(session, t0)

    db_state = session.get(ZoneSensorFailureState, zone.id)
    assert db_state.stage == emergency_operation.STAGE_NOTBETRIEB
    assert rows  # sanity: the cycle actually produced a row
    assert session.scalars(select(SensorFailureSourceComparison)).all() == []
