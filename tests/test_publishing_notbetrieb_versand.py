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
    seed_switch_command,
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
from thermoctl.db.models.lookup import (
    ACTOR_SOURCES,
    CHANNEL_KINDS,
    ActorSource,
    ChannelKind,
    CommandOutcome,
)
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
from thermoctl.services.meross_session import MerossSessionCache
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


class _MerossTransportStub:
    """Stands in for a signed-in `MerossCommandTransport` -- answers `SETACK`
    like a real socket unless told to fail, same double
    `tests/test_actuator_switches.py::MerossTransportStub` uses for the
    ordinary switch path."""

    def __init__(self, *, method: str = "SETACK", errors: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str, dict[str, object]]] = []
        self.method = method
        self.errors = errors

    async def send(
        self, device_uuid: str, namespace: str, method: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((device_uuid, namespace, method, dict(payload)))
        if self.errors is not None:
            raise self.errors
        return {"header": {"method": self.method}, "payload": {}}


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
    session: Session,
    *,
    capable: bool = True,
    armed: bool = True,
    relais_integration_code: str = "zigbee2mqtt",
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
        integration_id=integration(session, relais_integration_code).id,
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


# --- Schaltausgang über Meross, nicht verdrahtete Anbindung -----------------------


@pytest.mark.anyio
async def test_switch_via_meross_sends_and_a_broker_fault_invalidates_the_session(
    session: Session,
) -> None:
    """The emergency switch path goes through the identical Meross dispatch
    (`MerossSwitch`, `invalidate_meross_session`) as the ordinary one -- not a
    second, parallel implementation that could silently drift from the
    session-invalidation rule `tests/test_actuator_switches.py::
    test_a_broker_level_meross_failure_invalidates_the_cached_session` already
    protects for the ordinary path."""
    zone, _trv, relais = _setup_zone_no_sensor(session, relais_integration_code="meross")
    pub_state = PublicationState()
    cache = MerossSessionCache()

    t = NOW
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(
        session,
        _FakeClient(),
        pub_state,
        "thermoctl",
        t,
        meross_transport=_MerossTransportStub(),  # type: ignore[arg-type]
        meross_session_cache=cache,
        meross_switching_allowed=True,
    )
    session.commit()
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "aus"
    assert cache.invalid is False

    # The Aus->Ein transition's send fails at broker level -- the cached
    # session must be invalidated, exactly like the ordinary path. 31 s:
    # `_profile()`'s default Aus-Dauer (30 s) is the applicable one here,
    # clamped *up* by `min_off_seconds`, never down (`_resolve_pair`'s
    # `max(...)` cannot shorten the profile's own fixed duration).
    t = t + timedelta(seconds=31)
    shadow_run.cycle(session, t)
    session.commit()
    faulty_transport = _MerossTransportStub(
        errors=ConnectionRefusedError("CONNACK: Not authorized")
    )
    await publishing.cycle(
        session,
        _FakeClient(),
        pub_state,
        "thermoctl",
        t,
        meross_transport=faulty_transport,  # type: ignore[arg-type]
        meross_session_cache=cache,
        meross_switching_allowed=True,
    )
    session.commit()
    assert cache.invalid is True
    session.refresh(row)
    assert row.phase == "aus"  # the failed Ein attempt never committed

    # A fresh, working transport recovers -- confirms the Meross path (not
    # just the invalidation branch) genuinely sends for this actuator.
    cache = MerossSessionCache()
    t = t + timedelta(seconds=1)
    shadow_run.cycle(session, t)
    session.commit()
    working_transport = _MerossTransportStub()
    await publishing.cycle(
        session,
        _FakeClient(),
        pub_state,
        "thermoctl",
        t,
        meross_transport=working_transport,  # type: ignore[arg-type]
        meross_session_cache=cache,
        meross_switching_allowed=True,
    )
    session.commit()
    session.refresh(row)
    assert row.phase == "ein"
    assert working_transport.calls[0][3] == {"togglex": {"channel": 0, "onoff": 1}}


@pytest.mark.anyio
async def test_switch_via_an_unwired_integration_is_reported_not_sent(
    session: Session,
) -> None:
    """A capability this version has no dispatch path for at all (neither
    Zigbee2MQTT nor Meross) must be reported visibly, armed or not -- never
    silently skipped and never mistaken for a dry-run suppression."""
    zone, _trv, relais = _setup_zone_no_sensor(session, relais_integration_code="mqtt_generic")
    pub_state = PublicationState()

    t = NOW
    shadow_run.cycle(session, t)
    session.commit()
    await publishing.cycle(session, _FakeClient(), pub_state, "thermoctl", t)
    session.commit()

    commands = session.scalars(
        select(DeviceCommand).where(DeviceCommand.device_id == relais.id)
    ).all()
    assert len(commands) == 1
    outcome_row = session.get(CommandOutcome, commands[0].outcome_id)
    assert outcome_row is not None
    # Armed (`_setup_zone_no_sensor`'s default): a real attempt that could not
    # be dispatched is `failed`, not `suppressed` -- `suppressed` is reserved
    # for the dry-run bolt, which never engaged here.
    assert outcome_row.code == "failed"
    assert "nicht verdrahtet" in (commands[0].error or "")


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
    # The real relay was switched OFF by the Notbetrieb at t1; the minimum off
    # duration (10 s) counts only from the recovery marker (t3, 0 s elapsed) --
    # never from the simulated "on" history that ran alongside (Grundsatz 7).
    # The resend therefore carries the *real* state (OFF), not the ordinary
    # decision's "heat".
    assert relais_after[0] == (topic, '{"state": "OFF"}')

    # Once the minimum off duration has passed (counted from the marker), the
    # ordinary decision takes over: heat.
    t4 = t3 + timedelta(seconds=11)
    state_row.measured_at = t4
    session.flush()
    shadow_run.cycle(session, t4)
    session.commit()
    messages_before = len(client.messages)
    await publishing.cycle(session, client, pub_state, "thermoctl", t4)
    session.commit()
    assert [m for m in client.messages[messages_before:] if m[0] == topic] == [
        (topic, '{"state": "ON"}')
    ]


# --- Rückkehr: operating_mode wird auf den Vorwert zurückgestellt ----------------


@pytest.mark.anyio
async def test_recovery_restores_the_previous_operating_mode_exactly_once_with_restart(
    tmp_path: Path,
) -> None:
    """Projektinhaber-Entscheidung: "exakt der Zustand wie davor" -- die
    Übergabe setzt `operating_mode=manual`, die Rückkehr schreibt genau
    einmal den vor der Übergabe tatsächlich vom Gerät gemeldeten Wert zurück
    (hier `pause`, wie in `lokal/plaene/0.11.0-geraetevertrag.md` (b) für den
    real eingesetzten Bosch BTH-RA beobachtet). Eigene SQLite-Datei und eine
    zweite `Session`, damit der simulierte Prozessneustart zwischen Übergabe
    und Rückkehr echte Persistenz prüft, nicht In-Prozess-Zustand.
    """
    from tests.helpers import sensor_status_of
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.state import ZoneState
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    engine, maker = _own_database(tmp_path, "nb-versand-restore")
    session = maker()
    try:
        create_settings(session)
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.control_armed = True
        create_all_command_outcomes(session)

        zone = create_zone(session, "nb-restore")
        zone.min_on_seconds = 10
        zone.min_off_seconds = 10
        zone.sensor_timeout_seconds = 90
        zone.sensor_failure_enabled = True
        zone.sensor_failure_profile_id = _profile(session)
        zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")

        mode = SetpointMode(code="nb-restore-heizen", name="Heizen")
        session.add(mode)
        session.flush()
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
        )
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=NOW.weekday(), minute_of_day=0, setpoint_mode_id=mode.id
            )
        )
        session.flush()

        wall = Device(
            integration_id=integration(session, "zigbee2mqtt").id,
            external_id="nb-restore-wand",
            display_name="Wandfühler",
        )
        session.add(wall)
        session.flush()
        _link(session, wall, "temperature")
        zone.temperature_source_device_id = wall.id

        trv = Device(
            integration_id=integration(session, "zigbee2mqtt").id,
            external_id="nb-restore-trv",
            display_name="TRV",
        )
        session.add(trv)
        session.flush()
        _link(session, trv, "thermostat")
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
        # The device itself reports "pause" before anything goes wrong --
        # exactly the real-plant observation for a Bosch BTH-RA.
        operating_mode_property.last_value_text = "pause"
        operating_mode_property.last_value_at = NOW - timedelta(minutes=5)
        session.add(
            ZoneDevice(
                zone_id=zone.id,
                device_id=trv.id,
                device_role_id=role(session, "actuator").id,
                self_regulating=True,
            )
        )
        session.flush()

        client = _FakeClient()
        pub_state = PublicationState()
        trv_topic = _trv_topic(trv)

        t0 = NOW
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
        # Healthy zone: the ordinary self-regulating setpoint path sends its
        # usual `occupied_heating_setpoint`, but never touches `operating_mode`.
        assert all(
            "operating_mode" not in m[1]
            for m in client.messages
            if m[0] == trv_topic
        )

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

        handover_messages = [
            m for m in client.messages if m[0] == trv_topic and "operating_mode" in m[1]
        ]
        assert len(handover_messages) == 1
        assert json.loads(handover_messages[0][1]) == {
            "operating_mode": "manual",
            "occupied_heating_setpoint": 18.0,
        }
        zone_device_id = _zone_device_id(session, zone.id, trv.id)
        row = session.get(ActuatorEmergencyState, zone_device_id)
        assert row is not None
        assert row.handover_previous_operating_mode == "pause"
        session.close()

        # --- simulated process restart between Übergabe and Rückkehr.
        restart_session = maker()
        try:
            zone = restart_session.get(Zone, zone.id)
            assert zone is not None

            t2 = t1 + timedelta(seconds=5)
            state_row = restart_session.get(ZoneState, zone.id)
            assert state_row is not None
            state_row.temperature_c = Decimal("19.0")
            state_row.measured_at = t2
            state_row.sensor_status_id = sensor_status_of(restart_session, "ok").id
            restart_session.flush()
            shadow_run.cycle(restart_session, t2)
            restart_session.commit()
            await publishing.cycle(restart_session, client, pub_state, "thermoctl", t2)
            restart_session.commit()

            t3 = t2 + timedelta(seconds=61)
            state_row.measured_at = t3
            restart_session.flush()
            shadow_run.cycle(restart_session, t3)
            restart_session.commit()
            sf_state = restart_session.get(ZoneSensorFailureState, zone.id)
            assert sf_state is not None
            assert sf_state.stage == emergency_operation.STAGE_NORMAL

            messages_before = len(client.messages)
            await publishing.cycle(restart_session, client, pub_state, "thermoctl", t3)
            restart_session.commit()
            # The ordinary setpoint path resumes in this same cycle too (the
            # zone is `normal` again) -- filter for the Rückstellung itself,
            # not the unrelated `occupied_heating_setpoint` message beside it.
            restore_messages = [
                m
                for m in client.messages[messages_before:]
                if m[0] == trv_topic and "operating_mode" in m[1]
            ]
            assert len(restore_messages) == 1
            assert json.loads(restore_messages[0][1]) == {"operating_mode": "pause"}

            # The row's own bookkeeping is cleared immediately once the
            # Rückkehr fully resolves this cycle -- same convention the
            # handover fields already follow (`_send_emergency_actuators`'s
            # "not active" branch). The durable record lives in the audit
            # trail instead: exactly one `restore` command, `executed`.
            row = restart_session.get(ActuatorEmergencyState, zone_device_id)
            assert row is not None
            assert row.restore_result is None
            assert row.handover_previous_operating_mode is None
            restore_commands = restart_session.scalars(
                select(DeviceCommand).where(
                    DeviceCommand.device_id == trv.id, DeviceCommand.command == "restore"
                )
            ).all()
            assert len(restore_commands) == 1
            outcome_row = restart_session.get(CommandOutcome, restore_commands[0].outcome_id)
            assert outcome_row is not None
            assert outcome_row.code == "executed"

            # Further cycles: no third write of `operating_mode` to this device.
            t4 = t3 + timedelta(seconds=30)
            shadow_run.cycle(restart_session, t4)
            restart_session.commit()
            await publishing.cycle(restart_session, client, pub_state, "thermoctl", t4)
            restart_session.commit()
            total_mode_messages = [
                m for m in client.messages if m[0] == trv_topic and "operating_mode" in m[1]
            ]
            assert len(total_mode_messages) == 2  # handover + restore, nothing more
        finally:
            restart_session.close()
    finally:
        engine.dispose()


def _setup_recoverable_zone_with_trv(
    session: Session, name: str, *, previous_operating_mode: str | None
) -> tuple[Zone, Device, Device]:
    """A zone with a wall probe (so it can actually recover to `normal`,
    unlike `_setup_zone_no_sensor`) and one capable, self-regulating TRV --
    shared setup for the Rückstellungs-Grenzfälle below. `previous_
    operating_mode=None` means the device never reported one at all."""
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    create_settings(session)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = True
    create_all_command_outcomes(session)

    zone = create_zone(session, name)
    zone.min_on_seconds = 10
    zone.min_off_seconds = 10
    zone.sensor_timeout_seconds = 90
    zone.sensor_failure_enabled = True
    zone.sensor_failure_profile_id = _profile(session)
    zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")

    mode = SetpointMode(code=f"{name}-heizen", name="Heizen")
    session.add(mode)
    session.flush()
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=NOW.weekday(), minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()

    wall = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id=f"{name}-wand",
        display_name="Wandfühler",
    )
    session.add(wall)
    session.flush()
    _link(session, wall, "temperature")
    zone.temperature_source_device_id = wall.id

    trv = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id=f"{name}-trv",
        display_name="TRV",
    )
    session.add(trv)
    session.flush()
    _link(session, trv, "thermostat")
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
        device_id=trv.id, name="operating_mode", value_type="text",
        is_readable=True, is_writable=True,
    )
    session.add(operating_mode_property)
    session.flush()
    session.add_all(
        DevicePropertyValue(property_id=operating_mode_property.id, value=value)
        for value in ("schedule", "manual", "pause")
    )
    operating_mode_property.last_value_text = previous_operating_mode
    operating_mode_property.last_value_at = NOW - timedelta(minutes=5)
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=True,
        )
    )
    session.flush()
    return zone, wall, trv


async def _drive_to_recovery_cycle(
    session: Session, zone: Zone, client: _FakeClient, pub_state: PublicationState
) -> datetime:
    """Healthy -> stale (Notbetrieb, handover) -> two fresh readings 61s
    apart -> `normal` again. Returns `t3`, the cycle recovery completes on --
    the caller still has to run `publishing.cycle(session, client, pub_state,
    "thermoctl", t3)` itself, since what happens on that one cycle (dry run,
    a failing topic, ...) is exactly what each test below varies."""
    from tests.helpers import sensor_status_of
    from thermoctl.db.models.state import ZoneState

    t0 = NOW
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
    return t3


# --- Rückstellung: Grenzfälle (unbekannter Vorwert, Trockenlauf, Fehlschlag) ------


@pytest.mark.anyio
async def test_restore_with_unknown_previous_value_writes_nothing_and_logs_no_command(
    session: Session,
) -> None:
    """The device never reported `operating_mode` at all before the Übergabe
    -- plan item 2's own rule applies symmetrically on the way back: no
    guessed restore value, only a single, visible `decided_no_command`."""
    zone, _wall, trv = _setup_recoverable_zone_with_trv(
        session, "nb-restore-unbekannt", previous_operating_mode=None
    )
    client = _FakeClient()
    pub_state = PublicationState()
    trv_topic = _trv_topic(trv)

    t3 = await _drive_to_recovery_cycle(session, zone, client, pub_state)
    messages_before = len(client.messages)
    await publishing.cycle(session, client, pub_state, "thermoctl", t3)
    session.commit()

    mode_messages = [
        m
        for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ]
    assert mode_messages == []

    restore_commands = session.scalars(
        select(DeviceCommand).where(
            DeviceCommand.device_id == trv.id, DeviceCommand.command == "restore"
        )
    ).all()
    assert len(restore_commands) == 1
    outcome_row = session.get(CommandOutcome, restore_commands[0].outcome_id)
    assert outcome_row is not None
    assert outcome_row.code == "decided_no_command"

    decisions = session.scalars(
        select(ActuatorDecision).where(
            ActuatorDecision.zone_device_id == _zone_device_id(session, zone.id, trv.id),
            ActuatorDecision.action == emergency_actuator_plan.ACTION_NO_WRITE,
            ActuatorDecision.reason_code == emergency_actuator_plan.REASON_RESTORE_UNBEKANNT,
        )
    ).all()
    assert len(decisions) == 1


@pytest.mark.anyio
async def test_restore_dry_run_sends_nothing_and_later_scharf_sends_exactly_once(
    session: Session,
) -> None:
    """A dry run at the exact Rückkehr cycle must not spend the one-shot
    latch -- the device still gets its real Rückstellung once the plant is
    armed again, not never."""
    zone, _wall, trv = _setup_recoverable_zone_with_trv(
        session, "nb-restore-trockenlauf", previous_operating_mode="pause"
    )
    client = _FakeClient()
    pub_state = PublicationState()
    trv_topic = _trv_topic(trv)

    t3 = await _drive_to_recovery_cycle(session, zone, client, pub_state)
    messages_before = len(client.messages)
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = False
    session.flush()

    await publishing.cycle(session, client, pub_state, "thermoctl", t3)
    session.commit()
    assert [
        m for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ] == []

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.armed_episode_id is not None  # Rückkehr still open
    assert row.restore_attempted_at is None

    settings.control_armed = True
    session.flush()
    t4 = t3 + timedelta(seconds=30)
    shadow_run.cycle(session, t4)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t4)
    session.commit()

    restore_messages = [
        m for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ]
    assert len(restore_messages) == 1
    assert json.loads(restore_messages[0][1]) == {"operating_mode": "pause"}
    session.refresh(row)
    assert row.armed_episode_id is None  # fully resolved now


@pytest.mark.anyio
async def test_failed_restore_leaves_a_visible_result_and_is_not_retried(
    session: Session,
) -> None:
    zone, _wall, trv = _setup_recoverable_zone_with_trv(
        session, "nb-restore-fehlschlag", previous_operating_mode="pause"
    )
    trv_topic = _trv_topic(trv)
    client = _FakeClient(fail_topics=frozenset({trv_topic}))
    pub_state = PublicationState()

    t3 = await _drive_to_recovery_cycle(session, zone, client, pub_state)
    messages_before = len(client.messages)
    await publishing.cycle(session, client, pub_state, "thermoctl", t3)
    session.commit()

    restore_messages = [
        m for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ]
    assert len(restore_messages) == 1
    restore_commands = session.scalars(
        select(DeviceCommand).where(
            DeviceCommand.device_id == trv.id, DeviceCommand.command == "restore"
        )
    ).all()
    assert len(restore_commands) == 1
    outcome_row = session.get(CommandOutcome, restore_commands[0].outcome_id)
    assert outcome_row is not None
    assert outcome_row.code == "failed"

    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, trv.id))
    assert row is not None
    assert row.armed_episode_id is None  # resolved (failed counts as resolved, no retry)

    t4 = t3 + timedelta(seconds=30)
    shadow_run.cycle(session, t4)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", t4)
    session.commit()
    restore_messages = [
        m for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ]
    assert len(restore_messages) == 1  # still just the one, never retried


# --- Befund 2: Absturz zwischen Markierung und Versand ----------------------------


@pytest.mark.anyio
async def test_handover_interrupted_between_marker_and_send_is_not_retried(
    session: Session,
) -> None:
    """Simulates a process crash between `handover_attempted_at` being
    committed and the send actually resolving (plan item 2: the marker is
    persisted *before* the send) -- the state must stay exactly as a crash
    at that point would leave it (`handover_attempted_at` set,
    `handover_result` still `None`), and no cycle since may ever attempt a
    second send."""
    zone, trv, _relais = _setup_zone_no_sensor(session)
    shadow_run.cycle(session, NOW)
    session.commit()
    zone_device_id = _zone_device_id(session, zone.id, trv.id)
    row = session.get(ActuatorEmergencyState, zone_device_id)
    assert row is not None
    sf_state = session.get(ZoneSensorFailureState, zone.id)
    assert sf_state is not None
    # The crash happens here: the marker committed (including the armed-
    # episode marker a real cycle would also have set), the send itself
    # never resolved (not executed, not failed -- simply unknown).
    row.armed_episode_id = sf_state.episode_id
    row.handover_attempted_at = NOW
    row.handover_result = None
    session.flush()
    session.commit()

    client = _FakeClient()
    pub_state = PublicationState()
    trv_topic = _trv_topic(trv)
    t = NOW
    for _ in range(3):
        t = t + timedelta(seconds=21)
        shadow_run.cycle(session, t)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t)
        session.commit()

    assert [m for m in client.messages if m[0] == trv_topic] == []
    session.refresh(row)
    assert row.handover_attempted_at == NOW
    assert row.handover_result is None


@pytest.mark.anyio
async def test_restore_interrupted_between_marker_and_send_is_not_retried(
    session: Session,
) -> None:
    """The Rückstellung's own version of Befund 2: a crash between committing
    `restore_attempted_at` and the send resolving must not be retried.

    Unlike the handover (whose episode stays open for as long as the zone
    remains `notbetrieb`/`rueckkehrpruefung`, so `handover_attempted_at`/
    `handover_result` stay on the row the whole time), the Rückstellung's own
    "episode" is exactly this one transition back to `normal` -- the very
    next cycle's housekeeping (`_send_emergency_actuators`'s "not active"
    branch) sees `restore_attempted_at` already set, correctly refuses a
    second attempt (`already_attempted` in `plan_restore`), and then clears
    the row's bookkeeping as "fully resolved" the same way a clean success or
    a reported failure would be cleared -- it has no way to tell "crashed,
    unknown" apart from either of those once `restore_attempted_at` alone is
    the signal. The durable record of *that* cycle's no-write decision is
    `actuator_decision` (`simulated=False`, written unconditionally,
    independent of whether anything got cleared afterwards) -- the safety
    property this test actually exists to prove is that no second message
    is **ever** sent, which holds regardless.
    """
    zone, _wall, trv = _setup_recoverable_zone_with_trv(
        session, "nb-restore-absturz", previous_operating_mode="pause"
    )
    client = _FakeClient()
    pub_state = PublicationState()
    trv_topic = _trv_topic(trv)

    t3 = await _drive_to_recovery_cycle(session, zone, client, pub_state)
    messages_before = len(client.messages)
    zone_device_id = _zone_device_id(session, zone.id, trv.id)
    row = session.get(ActuatorEmergencyState, zone_device_id)
    assert row is not None
    assert row.armed_episode_id is not None
    crashed_episode_id = row.armed_episode_id
    # The crash happens here: the marker would be committed, the send never
    # resolved.
    row.restore_attempted_at = t3
    row.restore_result = None
    session.flush()
    session.commit()

    for _ in range(3):
        t3 = t3 + timedelta(seconds=21)
        shadow_run.cycle(session, t3)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t3)
        session.commit()

    assert [
        m for m in client.messages[messages_before:]
        if m[0] == trv_topic and "operating_mode" in m[1]
    ] == []
    no_write_decisions = session.scalars(
        select(ActuatorDecision).where(
            ActuatorDecision.zone_device_id == zone_device_id,
            ActuatorDecision.episode_id == crashed_episode_id,
            ActuatorDecision.action == emergency_actuator_plan.ACTION_NO_WRITE,
            ActuatorDecision.simulated.is_(False),
        )
    ).all()
    assert len(no_write_decisions) >= 1  # the crash cycle's decision stayed on record


@pytest.mark.anyio
async def test_recovery_does_not_violate_the_real_minimum_on_duration(
    tmp_path: Path,
) -> None:
    """Blocker 1 (Hauptsession, Grundsatz 7, konservativ): Notbetrieb schaltet
    das Relais ein; der Sensor kehrt zurück, während der Raum bereits warm
    ist (die gewöhnliche Hysterese-Entscheidung wäre sofort "aus") -- das
    Relais muss trotzdem bis zur Mindest-Ein-Dauer an bleiben, weil es real
    erst vor Kurzem eingeschaltet wurde. Zählung der Befehle und ihrer
    Zeitpunkte, nicht nur der Endzustand.
    """
    from tests.helpers import sensor_status_of
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.state import ZoneState
    from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint

    engine, maker = _own_database(tmp_path, "nb-versand-mindestdauer")
    session = maker()
    try:
        create_settings(session)
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.control_armed = True
        create_all_command_outcomes(session)

        zone = create_zone(session, "nb-mindestdauer")
        zone.min_on_seconds = 600  # 10 Minuten
        zone.min_off_seconds = 600
        zone.sensor_timeout_seconds = 90
        zone.sensor_failure_enabled = True
        # `domain.emergency_cycle._resolve_pair` klemmt Festtakt-Dauern nach
        # unten auf die Zonen-Mindestschaltdauer -- mit `min_on_seconds=600`/
        # `min_off_seconds=600` dauert deshalb *jede* Notbetriebsphase
        # tatsächlich 600 s, unabhängig vom hier gewählten Profilwert (20/30 s).
        zone.sensor_failure_profile_id = _profile(session)
        zone.sensor_failure_emergency_setpoint_c = Decimal("18.0")

        # Warmer Raum: 23 °C gegen einen 21 °C-Sollwert -- die gewöhnliche
        # Hysterese will sofort "aus", sobald der Sensor zurück ist.
        mode = SetpointMode(code="nb-mindestdauer-heizen", name="Heizen")
        session.add(mode)
        session.flush()
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("21.0"))
        )
        session.add(
            SchedulePoint(
                zone_id=zone.id, weekday=NOW.weekday(), minute_of_day=0, setpoint_mode_id=mode.id
            )
        )
        session.flush()

        wall = Device(
            integration_id=integration(session, "zigbee2mqtt").id,
            external_id="nb-md-wand",
            display_name="Wandfühler",
        )
        session.add(wall)
        session.flush()
        _link(session, wall, "temperature")
        zone.temperature_source_device_id = wall.id

        relais = Device(
            integration_id=integration(session, "zigbee2mqtt").id,
            external_id="nb-md-relais",
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

        client = _FakeClient()
        pub_state = PublicationState()
        topic = _relais_topic(relais)

        t0 = NOW
        session.add(
            ZoneState(
                zone_id=zone.id,
                temperature_c=Decimal("23.0"),
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
        assert client.messages[-1] == (topic, '{"state": "OFF"}')  # Entscheidung 1: Aus zuerst

        # t1 -- wall probe goes stale -> Notbetrieb (no thermostat candidate
        # configured): festtakt starts its Aus-Phase, 600 s (clamped, see above).
        t1 = t0 + timedelta(seconds=100)
        state_row = session.get(ZoneState, zone.id)
        assert state_row is not None
        state_row.sensor_status_id = sensor_status_of(session, "veraltet").id
        session.flush()
        shadow_run.cycle(session, t1)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t1)
        session.commit()
        row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
        assert row is not None
        assert row.phase == "aus"

        # t2 -- Aus-Phase's 600 s deadline passed: switches Ein (real relay now
        # genuinely on, confirmed via `last_successful_command_state`).
        t2 = t1 + timedelta(seconds=601)
        shadow_run.cycle(session, t2)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t2)
        session.commit()
        session.refresh(row)
        assert row.phase == "ein"
        assert row.last_successful_command_state is True
        assert row.last_successful_command_at == t2
        assert client.messages[-1] == (topic, '{"state": "ON"}')

        # t3 -- only 5s after the real relay turned on: the wall probe comes
        # back, warm room, two measurements 61s apart -- recovery completes
        # right as the ordinary hysteresis would want "aus" immediately.
        t3 = t2 + timedelta(seconds=5)
        state_row.temperature_c = Decimal("23.0")
        state_row.measured_at = t3
        state_row.sensor_status_id = sensor_status_of(session, "ok").id
        session.flush()
        shadow_run.cycle(session, t3)
        session.commit()
        await publishing.cycle(session, client, pub_state, "thermoctl", t3)
        session.commit()

        t4 = t3 + timedelta(seconds=61)
        state_row.measured_at = t4
        session.flush()
        shadow_run.cycle(session, t4)
        session.commit()
        sf_state = session.get(ZoneSensorFailureState, zone.id)
        assert sf_state is not None
        assert sf_state.stage == emergency_operation.STAGE_NORMAL
        # `held_for_s` is anchored conservatively at the recovery moment
        # itself (t4), 0 s elapsed so far -- nowhere near the 600 s minimum,
        # so the first regular cycle must not turn the relay off yet. The
        # ordinary path *does* resend (plan item 3's dedup invalidation), but
        # the resent state must still be "on", never "off".
        messages_before = len(client.messages)
        await publishing.cycle(session, client, pub_state, "thermoctl", t4)
        session.commit()
        new_messages = [m for m in client.messages[messages_before:] if m[0] == topic]
        assert new_messages == [(topic, '{"state": "ON"}')]  # held on, not switched off early

        # t5 -- well past the 600 s minimum counted conservatively from the
        # recovery moment itself (t4, not the earlier real switch-on at t2 --
        # `_seed_recovery_phase_marker`'s own documented conservative choice,
        # see its docstring): the ordinary hysteresis may now turn it off.
        t5 = t4 + timedelta(seconds=601)
        state_row.measured_at = t5
        session.flush()
        shadow_run.cycle(session, t5)
        session.commit()
        messages_before = len(client.messages)
        await publishing.cycle(session, client, pub_state, "thermoctl", t5)
        session.commit()
        new_messages = [m for m in client.messages[messages_before:] if m[0] == topic]
        assert new_messages == [(topic, '{"state": "OFF"}')]
    finally:
        engine.dispose()


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
    # `tests/conftest.py`'s shared `session` fixture seeds `actor_source` once
    # for its whole engine -- a dedicated engine like this one starts with an
    # empty table, and `record_command(source="system")` would otherwise fail
    # silently (it catches and only logs, see its own docstring) on every
    # call, never raising into the test. Seeded here once so that silent
    # failure cannot masquerade as "no command log entry expected".
    maker = session_factory(engine)
    seed_session = maker()
    try:
        for code, label in ACTOR_SOURCES:
            seed_session.add(ActorSource(code=code, label=label))
        seed_session.commit()
    finally:
        seed_session.close()
    return engine, maker


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


# --- Ersteintritt: realer letzter Relaiszustand zaehlt (Konzept 3.4) ----------------


async def _drive(
    session: Session,
    client: _FakeClient,
    pub_state: PublicationState,
    now: datetime,
) -> None:
    shadow_run.cycle(session, now)
    session.commit()
    await publishing.cycle(session, client, pub_state, "thermoctl", now)
    session.commit()


def _relais_payloads(client: _FakeClient, relais: Device) -> list[str]:
    topic = _relais_topic(relais)
    return [payload for t, payload in client.messages if t == topic]


@pytest.mark.anyio
async def test_entry_with_relay_on_for_30s_keeps_minimum_on_then_switches_off(
    session: Session,
) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    zone.min_on_seconds = 300
    seed_switch_command(session, zone, relais, sent_at=NOW - timedelta(seconds=30), on=True)
    client = _FakeClient()
    pub_state = PublicationState()

    await _drive(session, client, pub_state, NOW)
    # Kein Aus beim Eintritt: das Relais laeuft seine Mindest-Ein-Dauer zu Ende.
    assert '{"state": "OFF"}' not in _relais_payloads(client, relais)
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "ein"
    assert row.phase_deadline_at == NOW + timedelta(seconds=270)

    await _drive(session, client, pub_state, NOW + timedelta(seconds=269))
    assert '{"state": "OFF"}' not in _relais_payloads(client, relais)

    await _drive(session, client, pub_state, NOW + timedelta(seconds=271))
    assert _relais_payloads(client, relais)[-1] == '{"state": "OFF"}'
    session.refresh(row)
    assert row.phase == "aus"


@pytest.mark.anyio
async def test_entry_keeps_minimum_on_even_when_the_first_on_resend_fails(
    session: Session,
) -> None:
    """A failed send must not turn into "state unknown" on the next cycle."""
    zone, _trv, relais = _setup_zone_no_sensor(session)
    zone.min_on_seconds = 300
    seed_switch_command(session, zone, relais, sent_at=NOW - timedelta(seconds=30), on=True)
    client = _FakeClient(fail_topics=frozenset({_relais_topic(relais)}))
    pub_state = PublicationState()

    await _drive(session, client, pub_state, NOW)
    await _drive(session, client, pub_state, NOW + timedelta(seconds=10))
    assert '{"state": "OFF"}' not in _relais_payloads(client, relais)


@pytest.mark.anyio
async def test_entry_with_relay_on_longer_than_minimum_on_switches_off_at_once(
    session: Session,
) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    zone.min_on_seconds = 300
    seed_switch_command(session, zone, relais, sent_at=NOW - timedelta(seconds=900), on=True)
    client = _FakeClient()

    await _drive(session, client, PublicationState(), NOW)
    assert _relais_payloads(client, relais) == ['{"state": "OFF"}']


@pytest.mark.anyio
async def test_entry_with_relay_off_for_long_stays_off_then_takts_normally(
    session: Session,
) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    seed_switch_command(session, zone, relais, sent_at=NOW - timedelta(seconds=3600), on=False)
    client = _FakeClient()
    pub_state = PublicationState()

    await _drive(session, client, pub_state, NOW)
    assert _relais_payloads(client, relais) == ['{"state": "OFF"}']
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "aus"
    # Die bereits eingehaltene Aus-Zeit ist angerechnet -- aber nie mehr als die Aus-Dauer.
    assert row.phase_deadline_at == NOW

    await _drive(session, client, pub_state, NOW + timedelta(seconds=31))
    assert _relais_payloads(client, relais)[-1] == '{"state": "ON"}'


@pytest.mark.anyio
async def test_entry_with_relay_off_for_20s_credits_exactly_20s(session: Session) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    seed_switch_command(session, zone, relais, sent_at=NOW - timedelta(seconds=20), on=False)
    client = _FakeClient()
    pub_state = PublicationState()

    await _drive(session, client, pub_state, NOW)
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase_deadline_at == NOW + timedelta(seconds=10)
    await _drive(session, client, pub_state, NOW + timedelta(seconds=9))
    assert '{"state": "ON"}' not in _relais_payloads(client, relais)
    await _drive(session, client, pub_state, NOW + timedelta(seconds=11))
    assert _relais_payloads(client, relais)[-1] == '{"state": "ON"}'


@pytest.mark.anyio
async def test_entry_with_unknown_relay_state_sends_off_and_waits_the_full_off_duration(
    session: Session,
) -> None:
    zone, _trv, relais = _setup_zone_no_sensor(session)
    zone.min_on_seconds = 300
    # Nur Fehlschlaege und Trockenlauf-Eintraege: nichts davon belegt einen realen Zustand.
    seed_switch_command(
        session, zone, relais, sent_at=NOW - timedelta(seconds=30), on=True, outcome="failed"
    )
    seed_switch_command(
        session, zone, relais, sent_at=NOW - timedelta(seconds=20), on=True, outcome="suppressed"
    )
    client = _FakeClient()
    pub_state = PublicationState()

    await _drive(session, client, pub_state, NOW)
    assert _relais_payloads(client, relais) == ['{"state": "OFF"}']
    row = session.get(ActuatorEmergencyState, _zone_device_id(session, zone.id, relais.id))
    assert row is not None
    assert row.phase == "aus"
    assert row.phase_deadline_at == NOW + timedelta(seconds=30)
    await _drive(session, client, pub_state, NOW + timedelta(seconds=29))
    assert '{"state": "ON"}' not in _relais_payloads(client, relais)
