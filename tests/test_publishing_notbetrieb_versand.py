"""Auftrag 7b of `lokal/plaene/0.11.0-notbetrieb.md`: the scharfe Versandweg.

Counts every write call a fake MQTT transport actually receives -- never just
the return value -- the same discipline `tests/test_publishing_sensor_failure.py`
(Auftrag 7a) already established for the shadow-only half of this feature.
This file is its Auftrag 7b successor and replaces its one test: the old
invariant ("a notbetrieb zone sends exactly what an ordinary no-source zone
sends") described Auftrag 7a's deliberately narrow, shadow-only scope and no
longer holds once the publisher itself drives the Notbetrieb-Takt and the
once-per-episode TRV-Übergabe (plan Auftrag 7b items 1/2).
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import (
    capability,
    create_all_command_outcomes,
    create_settings,
    create_zone,
    integration,
    role,
)
from thermoctl.config import get_settings
from thermoctl.db.base import Base
from thermoctl.db.engine import session_factory
from thermoctl.db.models.device import (
    Device,
    DeviceCapabilityLink,
    DeviceProperty,
    DevicePropertyValue,
    ZoneDevice,
)
from thermoctl.db.models.lookup import CHANNEL_KINDS, ChannelKind
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import (
    ActuatorDecision,
    ActuatorEmergencyState,
    SensorFailureProfile,
    ZoneSensorFailureState,
)
from thermoctl.db.models.state import DeviceCommand
from thermoctl.db.models.zone import Zone
from thermoctl.domain import emergency_actuator_plan, emergency_operation
from thermoctl.domain.controller_channels import ControllerChannelError, configure_channel
from thermoctl.services import publishing, shadow_run
from thermoctl.services.publishing import PublicationState

NOW = datetime(2026, 10, 1, 12, 0, 0)


class _FakeClient:
    """A minimal `MqttPublisher`: records every switching message, always
    succeeds unless its topic is in `fail_topics` -- `fail_topics` names a
    topic that should be *reported* as failed to the caller (the opposite
    reading, "do not even count this attempt", is never what a real MQTT
    client does: an attempt always leaves a trace)."""

    def __init__(self, *, fail_topics: frozenset[str] = frozenset()) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail_topics = fail_topics

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            self.messages.append((topic, payload))
            if topic in self.fail_topics:
                return False
        return True


def _profile(session: Session, *, fixed_on: int = 20, fixed_off: int = 30) -> int:
    profile = SensorFailureProfile(
        name=f"nb-versand-{fixed_on}-{fixed_off}",
        version=1,
        fixed_on_seconds=fixed_on,
        fixed_off_seconds=fixed_off,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1.00"),
    )
    session.add(profile)
    session.flush()
    return profile.id


def _link(session: Session, device: Device, code: str) -> None:
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id)
    )
    session.flush()


def _zone_device_id(session: Session, zone_id: int, device_id: int) -> int:
    row = session.scalar(
        select(ZoneDevice).where(ZoneDevice.zone_id == zone_id, ZoneDevice.device_id == device_id)
    )
    assert row is not None
    return row.id


def _setup_zone_no_sensor(
    session: Session, *, capable: bool = True, armed: bool = True
) -> tuple[Zone, Device, Device]:
    """A zone with no wall probe and no thermostat measurement at all -- it
    reaches `NOTBETRIEB` on its very first `shadow_run.cycle()` call (same
    setup `tests/test_shadow_run_sensor_failure.py::
    test_notbetrieb_without_any_actuator_assignment_persists_nothing_for_actuators`
    and the old `test_publishing_sensor_failure.py` both relied on), which
    keeps every test below that does not itself need the Ersatzquelle/
    Rückkehr machinery free of that timeline.
    """
    create_settings(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = armed
    create_all_command_outcomes(session)

    zone = create_zone(session, "nb-versand")
    zone.min_on_seconds = 10
    zone.min_off_seconds = 10
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _profile(session)
    zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")

    trv = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="nb-trv",
        display_name="Notbetrieb-TRV",
    )
    session.add(trv)
    session.flush()
    _link(session, trv, "thermostat")
    if capable:
        session.add(
            DeviceProperty(
                device_id=trv.id,
                name="occupied_heating_setpoint",
                value_type="numeric",
                min_value=Decimal("5"),
                max_value=Decimal("30"),
                is_readable=True,
                is_writable=True,
            )
        )
        operating_mode_property = DeviceProperty(
            device_id=trv.id,
            name="operating_mode",
            value_type="text",
            is_readable=True,
            is_writable=True,
        )
        session.add(operating_mode_property)
        session.flush()
        session.add_all(
            DevicePropertyValue(property_id=operating_mode_property.id, value=value)
            for value in ("schedule", "manual", "pause")
        )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=True,
        )
    )

    relais = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="nb-relais",
        display_name="Notbetrieb-Relais",
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
    return zone, trv, relais


def _trv_topic(trv: Device) -> str:
    return f"{get_settings().mqtt_base_topic}/{trv.external_id}/set"


def _relais_topic(relais: Device) -> str:
    return f"{get_settings().mqtt_base_topic}/{relais.external_id}/set"


# --- Übergabe: genau einmal, Inhalt, Schweigen danach ---------------------------


@pytest.mark.anyio
async def test_handover_sent_exactly_once_with_correct_content_then_silent(
    session: Session,
) -> None:
    zone, trv, _relais = _setup_zone_no_sensor(session)
    shadow_run.cycle(session, NOW)
    session.commit()
    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state is not None
    assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB

    client = _FakeClient()
    pub_state = PublicationState()
    topic = _trv_topic(trv)

    await publishing.cycle(session, client, pub_state, "thermoctl", NOW)
    session.commit()

    trv_messages = [m for m in client.messages if m[0] == topic]
    assert len(trv_messages) == 1
    assert json.loads(trv_messages[0][1]) == {
        "operating_mode": "manual",
        "occupied_heating_setpoint": 18.0,
    }

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.handover_attempted_at == NOW
    assert row.handover_result == "executed"

    device_commands = session.scalars(
        select(DeviceCommand).where(DeviceCommand.device_id == trv.id)
    ).all()
    assert len(device_commands) == 1
    assert device_commands[0].outcome_id is not None

    # >= 5 further cycles -- including a schedule change (irrelevant: the
    # ordinary setpoint path is never even called for this zone's TRV while
    # it is NOTBETRIEB) and a repeated evaluation at the same instant (a
    # second control pass before the next tick, e.g. after a webhook retry
    # upstream) -- send zero further messages to the TRV.
    t = NOW
    for i in range(5):
        t = t + timedelta(seconds=21)
        if i == 2:
            # Zeitplan/Einstellung geändert.
            zone.sensor_failure_emergency_setpoint_c = Decimal("19.5")
            session.flush()
        shadow_run.cycle(session, t)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t)
        session.commit()

    trv_messages = [m for m in client.messages if m[0] == topic]
    assert len(trv_messages) == 1


@pytest.mark.anyio
async def test_failed_handover_leaves_a_visible_result_and_is_not_retried(
    session: Session,
) -> None:
    zone, trv, _relais = _setup_zone_no_sensor(session)
    shadow_run.cycle(session, NOW)
    session.commit()

    topic = _trv_topic(trv)
    client = _FakeClient(fail_topics=frozenset({topic}))
    pub_state = PublicationState()

    await publishing.cycle(session, client, pub_state, "thermoctl", NOW)
    session.commit()

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.handover_attempted_at == NOW
    assert row.handover_result == "failed"

    t = NOW
    for _ in range(4):
        t = t + timedelta(seconds=21)
        shadow_run.cycle(session, t)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t)
        session.commit()

    trv_messages = [m for m in client.messages if m[0] == topic]
    assert len(trv_messages) == 1  # the one failed attempt, never retried


@pytest.mark.anyio
async def test_device_without_operating_mode_gets_zero_messages_ever(
    session: Session,
) -> None:
    zone, trv, _relais = _setup_zone_no_sensor(session, capable=False)
    shadow_run.cycle(session, NOW)
    session.commit()

    client = _FakeClient()
    pub_state = PublicationState()
    topic = _trv_topic(trv)

    t = NOW
    for _ in range(4):
        await publishing.cycle(session, client, pub_state, "thermoctl", t)
        session.commit()
        t = t + timedelta(seconds=21)
        shadow_run.cycle(session, t)
        session.commit()

    assert [m for m in client.messages if m[0] == topic] == []

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.handover_attempted_at is None

    # Only the scharf decisions (`simulated=False`, this module's own) are
    # relevant here -- the shadow preview (`simulated=True`) does not check
    # device capability at all (out of this Auftrag's scope, see
    # `services/shadow_run.py`), so its rows keep whatever reason the
    # capability-blind preview always gave.
    armed_decisions = session.scalars(
        select(ActuatorDecision).where(
            ActuatorDecision.zone_device_id == row.zone_device_id,
            ActuatorDecision.simulated.is_(False),
        )
    ).all()
    assert armed_decisions
    assert all(
        d.reason_code == emergency_actuator_plan.REASON_NO_WRITE_KEIN_VERTRAG
        for d in armed_decisions
    )

    # Exactly one Schaltprotokoll entry for the whole episode, not one per
    # cycle (plan item 4) -- the command log stays rare.
    no_contract_commands = session.scalars(
        select(DeviceCommand).where(DeviceCommand.device_id == trv.id)
    ).all()
    assert len(no_contract_commands) == 1
    assert no_contract_commands[0].outcome_id is not None


@pytest.mark.anyio
async def test_dry_run_sends_nothing_and_does_not_count_as_attempted(session: Session) -> None:
    zone, trv, relais = _setup_zone_no_sensor(session, armed=False)
    shadow_run.cycle(session, NOW)
    session.commit()

    client = _FakeClient()
    pub_state = PublicationState()

    t = NOW
    for _ in range(3):
        await publishing.cycle(session, client, pub_state, "thermoctl", t)
        session.commit()
        t = t + timedelta(seconds=21)
        shadow_run.cycle(session, t)
        session.commit()

    assert client.messages == []
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.handover_attempted_at is None
    assert row.phase is None  # switch side: no scharf Takt committed either

    # Arming the plant later still gets its one real attempt -- a dry run
    # never spends the once-per-episode latch.
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = True
    session.flush()
    await publishing.cycle(session, client, pub_state, "thermoctl", t)
    session.commit()

    trv_messages = [m for m in client.messages if m[0] == _trv_topic(trv)]
    assert len(trv_messages) == 1
    relais_messages = [m for m in client.messages if m[0] == _relais_topic(relais)]
    assert len(relais_messages) == 1


# --- Schaltausgang-Takt -----------------------------------------------------------


@pytest.mark.anyio
async def test_switch_cycles_through_at_least_two_on_off_pairs_at_the_right_times(
    session: Session,
) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    client = _FakeClient()
    pub_state = PublicationState()
    topic = _relais_topic(relais)

    t = NOW
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t)
    session.commit()
    assert client.messages[-1] == (topic, '{"state": "OFF"}')  # Entscheidung 1: Aus zuerst
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "aus"
    assert row.phase_deadline_at == t + timedelta(seconds=30)

    t = t + timedelta(seconds=31)
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t)
    session.commit()
    assert client.messages[-1] == (topic, '{"state": "ON"}')
    session.refresh(row)
    assert row.phase == "ein"
    assert row.phase_deadline_at == t + timedelta(seconds=20)

    t = t + timedelta(seconds=21)
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t)
    session.commit()
    assert client.messages[-1] == (topic, '{"state": "OFF"}')
    session.refresh(row)
    assert row.phase == "aus"

    on_off_messages = [m for m in client.messages if m[0] == topic]
    assert [m[1] for m in on_off_messages] == [
        '{"state": "OFF"}',
        '{"state": "ON"}',
        '{"state": "OFF"}',
    ]


@pytest.mark.anyio
async def test_switch_on_failure_is_retried_not_skipped_or_advanced(session: Session) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    topic = _relais_topic(relais)
    pub_state = PublicationState()

    t = NOW
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, _FakeClient(), pub_state, "thermoctl", t)
    session.commit()

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "aus"
    aus_deadline = row.phase_deadline_at

    # The Ein attempt at the Aus-phase's deadline fails -- twice.
    failing_client = _FakeClient(fail_topics=frozenset({topic}))
    for _ in range(2):
        t = t + timedelta(seconds=31)
        shadow_run.cycle(session, t)
        session.commit()
        await publishing.cycle(session, failing_client, pub_state, "thermoctl", t)
        session.commit()
        session.refresh(row)
        # Never committed to Ein, never silently skipped to a *later* Aus
        # either -- the scharf phase stays exactly what it was before the
        # first failed attempt (plan item 1: "Ein-Fehler bis/über
        # Phasenfrist").
        assert row.phase == "aus"
        assert row.phase_deadline_at == aus_deadline

    on_attempts = [m for m in failing_client.messages if m[0] == topic]
    assert [m[1] for m in on_attempts] == ['{"state": "ON"}', '{"state": "ON"}']

    # Recovers: the next attempt succeeds, and the phase finally transitions.
    recovered_client = _FakeClient()
    t = t + timedelta(seconds=31)
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, recovered_client, pub_state, "thermoctl", t)
    session.commit()
    session.refresh(row)
    assert row.phase == "ein"
    assert row.phase_deadline_at == t + timedelta(seconds=20)


@pytest.mark.anyio
async def test_switch_off_failure_blocks_a_new_on_phase(session: Session) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    topic = _relais_topic(relais)
    pub_state = PublicationState()

    t = NOW
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, _FakeClient(), pub_state, "thermoctl", t)
    session.commit()
    t = t + timedelta(seconds=31)
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, _FakeClient(), pub_state, "thermoctl", t)
    session.commit()

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "ein"
    ein_deadline = row.phase_deadline_at

    failing_client = _FakeClient(fail_topics=frozenset({topic}))
    t = t + timedelta(seconds=21)
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, failing_client, pub_state, "thermoctl", t)
    session.commit()
    session.refresh(row)
    # The Aus attempt failed -- still Ein, no new Ein-phase was started either.
    assert row.phase == "ein"
    assert row.phase_deadline_at == ein_deadline
    assert [m[1] for m in failing_client.messages if m[0] == topic] == ['{"state": "OFF"}']


# --- Rückkehr: Dedup-Invalidierung -------------------------------------------------


@pytest.mark.anyio
async def test_recovery_resends_the_current_setpoint_even_if_unchanged(
    session: Session,
) -> None:
    """A dedicated wall-probe-backed zone, so it can actually recover to
    `NORMAL` (the no-sensor setup above never can: there is nothing to come
    back online)."""
    create_settings(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = True
    create_all_command_outcomes(session)

    zone = create_zone(session, "nb-rueckkehr")
    zone.min_on_seconds = 10
    zone.min_off_seconds = 10
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _profile(session)
    zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")

    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    mode = SetpointMode(code="nb-rueckkehr-heizen", name="Heizen")
    session.add(mode)
    session.flush()
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id,
            weekday=NOW.weekday(),
            minute_of_day=0,
            setpoint_mode_id=mode.id,
        )
    )
    session.flush()

    wall = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="nb-rueckkehr-wand",
        display_name="Wandfühler",
    )
    session.add(wall)
    session.flush()
    _link(session, wall, "temperature")
    zone.temperature_source_device_id = wall.id

    relais = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="nb-rueckkehr-relais",
        display_name="Relais",
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

    from thermoctl.db.models.state import ZoneState

    client = _FakeClient()
    pub_state = PublicationState()
    topic = _relais_topic(relais)

    # t0 -- healthy: ordinary cycle, sends the (frost or schedule) decision
    # once and caches it.
    t0 = NOW
    from tests.helpers import sensor_status_of

    session.add(
        ZoneState(
            zone_id=zone.id,
            temperature_c=Decimal("19.0"),
            measured_at=t0,
            sensor_status_id=sensor_status_of(session, "ok").id,
            updated_at=t0,
        )
    )
    session.flush()
    shadow_run.cycle(session, t0)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t0)
    session.commit()
    baseline_count = len([m for m in client.messages if m[0] == topic])
    assert baseline_count == 1
    assert client.messages[-1] == (topic, '{"state": "ON"}')  # 19.0 < 21.0

    # t1 -- the wall probe goes stale -> straight to Notbetrieb (no thermostat
    # candidate configured in this zone at all).
    t1 = t0 + timedelta(seconds=100)
    state_row = session.get(ZoneState, zone.id)
    assert state_row is not None
    state_row.sensor_status_id = sensor_status_of(session, "veraltet").id
    session.flush()
    shadow_run.cycle(session, t1)
    session.commit()
    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state is not None
    assert sf_state.stage == emergency_operation.STAGE_NOTBETRIEB
    await publishing.cycle(session, client, pub_state, "thermoctl", t1)
    session.commit()

    # t2 -- the wall probe is readable again, with the *same* 19.0 reading --
    # two samples, 61s apart, both "ok" -- recovery completes.
    t2 = t1 + timedelta(seconds=5)
    state_row.temperature_c = Decimal("19.0")
    state_row.measured_at = t2
    state_row.sensor_status_id = sensor_status_of(session, "ok").id
    session.flush()
    shadow_run.cycle(session, t2)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t2)
    session.commit()

    t3 = t2 + timedelta(seconds=61)
    state_row.measured_at = t3
    session.flush()
    shadow_run.cycle(session, t3)
    session.commit()
    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state is not None
    assert sf_state.stage == emergency_operation.STAGE_NORMAL

    messages_before = len(client.messages)
    await publishing.cycle(session, client, pub_state, "thermoctl", t3)
    session.commit()
    messages_after = client.messages[messages_before:]
    relais_after = [m for m in messages_after if m[0] == topic]
    # The ordinary decision (still "heat, 19.0 < 21.0") is unchanged from the
    # cached pre-failure value -- without cache invalidation this would send
    # zero messages. Plan item 3: it must resend.
    assert len(relais_after) == 1
    assert relais_after[0] == (topic, '{"state": "ON"}')


# --- Kein Notbetrieb: bestehende Publisher-Tests bleiben unberührt ----------------
# (siehe tests/test_publishing.py -- vollständig weiterhin grün, siehe Bericht.)


# --- Bediengerätekanal: strukturell ausgeschlossen, nicht nur per Zufall ---------


def test_notbetrieb_thermostat_can_never_also_be_a_write_controller_channel(
    session: Session,
) -> None:
    """A device the Notbetrieb-Übergabe writes to can never *also* receive a
    write-direction Bediengerätekanal command -- `may_be_written()` already
    refuses that (actuator + write-controller is excluded by construction),
    so there is structurally nothing that could duplicate the handover onto
    a second channel. This proves the guard still holds for exactly the kind
    of device this plan introduces, not a new one.
    """
    zone, trv, _relais = _setup_zone_no_sensor(session)
    for code, label in CHANNEL_KINDS:
        session.add(ChannelKind(code=code, label=label))
    session.flush()
    with pytest.raises(ControllerChannelError):
        configure_channel(
            session, trv, "occupied_heating_setpoint", "write", "zone_setpoint", zone_id=zone.id
        )


# --- Latch überlebt einen echten Prozessneustart -----------------------------------


def _own_database(tmp_path: Path, name: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db", future=True)
    Base.metadata.create_all(engine)
    return engine, session_factory(engine)


@pytest.mark.anyio
async def test_handover_latch_survives_a_simulated_process_restart(tmp_path: Path) -> None:
    engine, maker = _own_database(tmp_path, "nb-versand-restart")
    session = maker()
    try:
        zone, trv, _relais = _setup_zone_no_sensor(session)
        zone_id, trv_id = zone.id, trv.id
        shadow_run.cycle(session, NOW)
        session.commit()
        client = _FakeClient()
        pub_state = PublicationState()
        await publishing.cycle(session, client, pub_state, "thermoctl", NOW)
        session.commit()
        assert len([m for m in client.messages if m[0] == _trv_topic(trv)]) == 1
        session.close()

        restart_session = maker()
        try:
            t = NOW + timedelta(seconds=30)
            shadow_run.cycle(restart_session, t)
            restart_session.commit()
            restart_client = _FakeClient()
            restart_state = PublicationState()  # fresh, in-process cache too
            await publishing.cycle(restart_session, restart_client, restart_state, "thermoctl", t)
            restart_session.commit()
            assert [m for m in restart_client.messages if m[0] == _trv_topic(trv)] == []

            row = restart_session.get(
                ActuatorEmergencyState, _zone_device_id(restart_session, zone_id, trv_id)
            )
            assert row is not None
            assert row.handover_attempted_at == NOW
        finally:
            restart_session.close()
    finally:
        engine.dispose()
