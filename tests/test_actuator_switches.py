"""Wiring ordinary (non-self-regulating) actuators to the control decision.

`_send_actuator_switches` in `services/publishing.py` is the one place an on/off
decision (`domain/control_loop.py::decide`, logged as `shadow_decision`) reaches a
real switching device. Everything here runs with `setting.control_armed` false unless
a test explicitly arms it — CLAUDE.md forbids arming anything in this suite.
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_all_command_outcomes,
    create_settings,
    create_zone,
    integration,
    role,
    source,
)
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import CommandOutcome, DeviceCapability
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import arm
from thermoctl.services.publishing import PublicationState, cycle

NOW = datetime(2026, 9, 1, 7, 0)


class Mitschrift:
    """A publisher that always accepts and only records what it was sent — same
    double as `tests/test_publishing.py`."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.switched: list[str] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        self.messages.append((topic, payload))
        if switches:
            self.switched.append(topic)
        return True


class FailingClient:
    """Every switching command raises -- the broker is gone. Non-switching messages
    are still recorded, so a test can check that the rest of the cycle kept going."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            raise ConnectionError("Broker nicht erreichbar")
        self.messages.append((topic, payload))
        return True


def _switch_capability(session: Session) -> DeviceCapability:
    existing = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    if existing is not None:
        return existing
    capability = DeviceCapability(code="switch", label="Schaltausgang")
    session.add(capability)
    session.flush()
    return capability


def _actuator_zone(
    session: Session,
    name: str,
    *,
    external_id: str | None = None,
    integration_code: str = "zigbee2mqtt",
    self_regulating: bool = False,
    with_switch_capability: bool = True,
) -> tuple[Zone, Device]:
    zone = create_zone(session, name)
    device = Device(
        integration_id=integration(session, integration_code).id,
        external_id=external_id or f"{name}-relais",
        display_name=f"{name}-relais",
    )
    session.add(device)
    session.flush()
    if with_switch_capability:
        session.add(
            DeviceCapabilityLink(
                device_id=device.id, capability_id=_switch_capability(session).id
            )
        )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=self_regulating,
        )
    )
    session.flush()
    return zone, device


def _decision(session: Session, zone: Zone, *, heating: bool, reason: str = "Testgrund") -> None:
    session.add(
        ShadowDecision(
            decided_at=NOW,
            zone_id=zone.id,
            setpoint_reason="Zeitplan",
            would_heat=heating,
            outcome_code="heizen" if heating else "aus",
            reason=reason,
        )
    )
    session.flush()


def _command_log(session: Session) -> list[tuple[DeviceCommand, str]]:
    rows = session.execute(
        select(DeviceCommand, CommandOutcome.code)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
        .order_by(DeviceCommand.id)
    ).all()
    return [(entry, code) for entry, code in rows]


@pytest.mark.anyio
async def test_dry_run_sends_nothing_and_the_log_shows_it_was_suppressed(
    session: Session,
) -> None:
    """The whole point of the dry-run bolt: nothing leaves, and the log says why."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "trockenlaufzone")
    _decision(session, zone, heating=True)
    # Deliberately not armed.

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "suppressed"
    assert entry.command == "switch"
    assert entry.error is None


@pytest.mark.anyio
async def test_an_armed_heating_decision_reaches_the_right_adapter(
    session: Session,
) -> None:
    """The gegenbeweis: armed, a heating decision really does go out -- to the
    Zigbee2MQTT `.../set` topic, with an ON payload, marked as a switching message,
    and logged as executed."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "scharfzone")
    _decision(session, zone, heating=True, reason="Sollwert unterschritten")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    sent = await cycle(session, client, PublicationState(), "thermoctl", NOW)

    commands = [(t, p) for t, p in client.messages if t.endswith("/set")]
    assert len(commands) == 1
    topic, payload = commands[0]
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {"state": "ON"}
    assert topic in client.switched
    assert sent >= 1

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "executed"
    assert entry.command == "switch"
    assert entry.device_name == device.display_name
    assert entry.zone_id == zone.id
    assert entry.reason == "Sollwert unterschritten"
    assert entry.error is None


@pytest.mark.anyio
async def test_an_armed_off_decision_sends_off(session: Session) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "auszone")
    _decision(session, zone, heating=False)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {"state": "OFF"}


@pytest.mark.anyio
async def test_the_same_decision_sent_twice_writes_only_one_command(
    session: Session,
) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "wiederholungszone")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client, state = Mitschrift(), PublicationState()
    await cycle(session, client, state, "thermoctl", NOW)
    await cycle(session, client, state, "thermoctl", NOW)

    commands = [(t, p) for t, p in client.messages if t.endswith("/set")]
    assert len(commands) == 1
    assert len(_command_log(session)) == 1


@pytest.mark.anyio
async def test_a_self_regulating_valve_gets_no_switch_command(session: Session) -> None:
    """Rule 1: a self-regulating valve never also gets an on/off command -- it
    would fight the device's own regulation loop."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "selbstregelndzone", self_regulating=True)
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    assert _command_log(session) == []


@pytest.mark.anyio
async def test_a_device_without_the_switch_capability_gets_no_command(
    session: Session,
) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(
        session, "fehlendefaehigkeitzone", with_switch_capability=False
    )
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    assert _command_log(session) == []


@pytest.mark.anyio
async def test_a_failing_adapter_is_logged_and_does_not_stop_other_zones(
    session: Session,
) -> None:
    """`SwitchResult` reports rather than raises -- but this checks the caller too:
    a failure at one device's send must not abort the cycle before the next zone's
    own state publication runs."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    broken_zone, _broken_device = _actuator_zone(session, "fehlerzone")
    _decision(session, broken_zone, heating=True)
    healthy_zone = create_zone(session, "gesundezone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = FailingClient()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "failed"
    assert entry.error is not None
    assert "Broker nicht erreichbar" in entry.error
    # The other zone's ordinary state publication still ran -- the failure did not
    # abort the cycle.
    topics = [t for t, _p in client.messages]
    assert f"thermoctl/zones/{healthy_zone.id}/state/setpoint" in topics


@pytest.mark.anyio
async def test_no_decision_yet_sends_nothing(session: Session) -> None:
    """A zone the control loop has never produced a decision for has nothing to
    act on -- inventing a state would be a decision this wiring has no business
    making."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _actuator_zone(session, "ohneentscheidungzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    assert _command_log(session) == []


@pytest.mark.anyio
async def test_after_a_restart_the_state_is_sent_unconditionally(session: Session) -> None:
    """This project's chosen answer for what happens after a restart: a fresh
    `PublicationState` (as after every process restart) has no memory of what was
    last sent, so the very first cycle acts regardless of whether the decision
    happens to match whatever was last commanded before the restart -- exactly
    the same call already made for the self-regulating setpoint and the Home
    Assistant registration."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "neustartzone")
    _decision(session, zone, heating=False)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    # Simulates a restart: a brand-new PublicationState, as `_lifespan` builds one
    # on every process start.
    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert json.loads(payload) == {"state": "OFF"}
    entries = _command_log(session)
    assert len(entries) == 1
    assert entries[0][1] == "executed"


@pytest.mark.anyio
async def test_a_meross_actuator_is_reported_as_not_wired_and_nothing_is_sent(
    session: Session,
) -> None:
    """Meross needs a signed-in cloud session and is explicitly out of scope for
    this change (see docs/offene-entscheidungen.md) -- but the gap must be visible
    in the command log once armed, not silent."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "merosszone", integration_code="meross")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "failed"
    assert entry.device_name == device.display_name
    assert entry.error is not None
    assert "meross" in entry.error.lower()


@pytest.mark.anyio
async def test_the_minimum_switching_duration_still_applies(session: Session) -> None:
    """The hysteresis and minimum switching duration live in `domain/control_loop.py`
    and are not touched by this wiring -- they are already baked into `would_heat`
    by the time `shadow_run.cycle()` writes it. This locks down that the actuator
    wiring sends exactly what the decision says, blocked run included, rather than
    deriving its own on/off state."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "mindestdauerzone")
    _decision(
        session,
        zone,
        heating=True,
        reason="Mindestschaltdauer noch nicht erreicht -- Heizen bleibt an.",
    )
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert json.loads(payload) == {"state": "ON"}
    entry, _outcome = _command_log(session)[0]
    assert "Mindestschaltdauer" in (entry.reason or "")
