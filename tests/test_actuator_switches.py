"""Wiring ordinary (non-self-regulating) actuators to the control decision.

`_send_actuator_switches` in `services/publishing.py` is the one place an on/off
decision (`domain/control_loop.py::decide`, logged as `shadow_decision`) reaches a
real switching device. Everything here runs with `setting.control_armed` false unless
a test explicitly arms it — CLAUDE.md forbids arming anything in this suite.
"""

import json
from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal
from typing import Any

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
from thermoctl.db.models.device import Device, DeviceCapabilityLink, DeviceProperty, ZoneDevice
from thermoctl.db.models.lookup import CommandOutcome, DeviceCapability
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import arm
from thermoctl.domain.switch_commands import (
    SwitchCommand,
    ThermostatCommand,
    switch_commands,
    thermostat_commands,
)
from thermoctl.services.meross_session import MerossSessionCache
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
    are still recorded, so a test can check that the rest of the cycle kept going.
    `switch_attempts` counts every switching call, whether or not it raised --
    needed to prove a retry actually reaches the client (finding A)."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.switch_attempts = 0

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            self.switch_attempts += 1
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


def _capability(session: Session, code: str, label: str) -> DeviceCapability:
    existing = session.scalar(select(DeviceCapability).where(DeviceCapability.code == code))
    if existing is not None:
        return existing
    capability = DeviceCapability(code=code, label=label)
    session.add(capability)
    session.flush()
    return capability


def _thermostat_actuator_zone(
    session: Session,
    name: str,
    *,
    integration_code: str = "zigbee2mqtt",
    self_regulating: bool = False,
    has_system_mode: bool = True,
    also_switch_capable: bool = False,
) -> tuple[Zone, Device]:
    """A Zigbee2MQTT thermostatic actuator, run by thermoctl's own hysteresis
    (`self_regulating=False`) -- the wiring `thermostat_commands()` and
    `Zigbee2MqttThermostat` cover."""
    zone = create_zone(session, name)
    device = Device(
        integration_id=integration(session, integration_code).id,
        external_id=f"{name}-ventil",
        display_name=f"{name}-ventil",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=device.id,
            capability_id=_capability(session, "thermostat", "Thermostatausgang").id,
        )
    )
    if also_switch_capable:
        session.add(
            DeviceCapabilityLink(
                device_id=device.id, capability_id=_switch_capability(session).id
            )
        )
    session.add(
        DeviceProperty(
            device_id=device.id,
            name="occupied_heating_setpoint",
            value_type="numeric",
            unit="°C",
            min_value=Decimal("5"),
            max_value=Decimal("30"),
            is_readable=True,
            is_writable=True,
        )
    )
    if has_system_mode:
        session.add(
            DeviceProperty(
                device_id=device.id,
                name="system_mode",
                value_type="enum",
                is_readable=True,
                is_writable=True,
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


class MerossTransportStub:
    """Stands in for a signed-in `MerossCommandTransport`; answers `SETACK` like a
    real socket unless told to fail. Raising when never expected to be called would
    prove a dry run reached the network -- exactly the property this suite must not
    let slip through again."""

    def __init__(self, *, method: str = "SETACK") -> None:
        self.calls: list[tuple[str, str, str, dict[str, Any]]] = []
        self.method = method

    async def send(
        self, device_uuid: str, namespace: str, method: str, payload: Any
    ) -> dict[str, Any]:
        self.calls.append((device_uuid, namespace, method, dict(payload)))
        return {"header": {"method": self.method}, "payload": {}}


class _NetworkForbidden:
    """A transport that fails the test the moment anything calls it -- used to prove
    a dry run never reaches the network at all."""

    async def send(
        self, device_uuid: str, namespace: str, method: str, payload: Any
    ) -> dict[str, Any]:
        raise AssertionError("Der Trockenlauf haette den Transport nicht anfassen duerfen")


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
async def test_a_failed_switch_is_retried_every_cycle_but_logged_only_once(
    session: Session,
) -> None:
    """Cross-review finding A, for an ordinary (non-self-regulating) actuator --
    the twin of `test_publishing.py`'s test with the same name, for the code path
    `_send_actuator_switches` covers instead of `_send_self_regulating_valves`.
    Before the fix, an unchanged `(heating, armed)` cache key was written on a
    failed attempt too, so the second and third cycle with the same broken client
    and the same heating decision would skip the device outright -- not attempt
    to send, and not log anything."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "schalterwiederholzone")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    failing_client = FailingClient()
    state = PublicationState()
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)

    # Three cycles, three real attempts -- without the fix this would be 1.
    assert failing_client.switch_attempts == 3
    entries = _command_log(session)
    assert [code for _entry, code in entries] == ["failed"]

    # Gegenprobe: once the device answers again, exactly one further entry
    # appears.
    recovering_client = Mitschrift()
    await cycle(session, recovering_client, state, "thermoctl", NOW)

    entries = _command_log(session)
    assert [code for _entry, code in entries] == ["failed", "executed"]


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
async def test_a_switch_actuator_at_a_non_wired_integration_is_reported_not_sent(
    session: Session,
) -> None:
    """`_WIRED_INTEGRATIONS` today covers exactly `zigbee2mqtt` and `meross` -- a
    switch-capability actuator at any other integration code has nothing built to
    reach it, and that must show up in the command log once armed, not silently."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "unbekannteanbindungzone", integration_code="hue")
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
    assert "nicht verdrahtet" in entry.error


@pytest.mark.anyio
async def test_a_non_wired_switch_integration_is_logged_only_once_across_cycles(
    session: Session,
) -> None:
    """A device at an integration this version does not switch through can never
    recover on its own -- unlike a broken broker connection (finding A), retrying
    the send would be pointless here, there is nothing to retry. But the *log
    entry* dedup applies equally: an unchanged decision must not produce a fresh
    'not wired' line every single cycle."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "wiederholteanbindungzone", integration_code="hue")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    state = PublicationState()
    await cycle(session, Mitschrift(), state, "thermoctl", NOW)
    await cycle(session, Mitschrift(), state, "thermoctl", NOW)

    assert len(_command_log(session)) == 1


@pytest.mark.anyio
async def test_a_meross_actuator_without_a_signed_in_session_fails_visibly(
    session: Session,
) -> None:
    """Meross is wired now, but only through an already signed-in transport handed
    in from outside (`app.py`'s `_shadow_loop`, `services/meross_session.py`). No
    account configured, or a sign-in the cloud refused, means `meross_transport` is
    `None` here -- and that must be visible in the command log once armed, not
    silent."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "merosszone", integration_code="meross")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(
        session,
        client,
        PublicationState(),
        "thermoctl",
        NOW,
        # The frozen, start-of-process bolt (finding C) -- also armed here, same
        # as `setting.control_armed` above, so the runtime bolt does not mask
        # the very failure this test is about.
        meross_switching_allowed=True,
    )

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "failed"
    assert entry.device_name == device.display_name
    assert entry.error == "Keine gueltige Meross-Sitzung vorhanden"


@pytest.mark.anyio
async def test_a_meross_actuator_in_a_dry_run_never_touches_the_transport(
    session: Session,
) -> None:
    """Gegenprobe for the dry-run bolt on the Meross path specifically: even with a
    working, signed-in transport handed in, an unarmed cycle must not use it at
    all -- `_NetworkForbidden` fails the test the instant anything calls `send()`."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "merosstrockenlaufzone", integration_code="meross")
    _decision(session, zone, heating=True)
    # Deliberately not armed.

    client = Mitschrift()
    await cycle(
        session,
        client,
        PublicationState(),
        "thermoctl",
        NOW,
        meross_transport=_NetworkForbidden(),  # type: ignore[arg-type]
    )

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "suppressed"
    assert entry.error is None


@pytest.mark.anyio
async def test_an_armed_meross_actuator_with_a_signed_in_session_really_sends(
    session: Session,
) -> None:
    """The gegenbeweis for Meross: armed, with a signed-in transport handed in, a
    heating decision really goes out over `Appliance.Control.ToggleX`, is reported
    as executed, and is logged that way."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _actuator_zone(session, "merossscharfzone", integration_code="meross")
    _decision(session, zone, heating=True, reason="Sollwert unterschritten")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    transport = MerossTransportStub()
    client = Mitschrift()
    sent = await cycle(
        session,
        client,
        PublicationState(),
        "thermoctl",
        NOW,
        meross_transport=transport,  # type: ignore[arg-type]
        # The frozen, start-of-process bolt (finding C); the runtime bolt is
        # already armed above.
        meross_switching_allowed=True,
    )

    assert sent >= 1
    assert transport.calls == [
        (device.external_id, "Appliance.Control.ToggleX", "SET",
         {"togglex": {"channel": 0, "onoff": 1}}),
    ]
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "executed"
    assert entry.command == "switch"
    assert entry.device_name == device.display_name
    assert entry.reason == "Sollwert unterschritten"
    assert entry.error is None


@pytest.mark.anyio
async def test_a_frozen_bolt_left_at_its_safe_default_blocks_meross_too(
    session: Session,
) -> None:
    """Finding C, the property it must hold: `MqttClient` already freezes its own
    switching bolt at process start and enforces it unconditionally in
    `client.publishing()`, so a single forgotten caller elsewhere cannot switch a
    Zigbee2MQTT device for real. Before this fix, the Meross path had no equivalent
    -- only the runtime `setting.control_armed` gated it, checked fresh on every
    call. This proves the frozen bolt now covers Meross the same way: even with the
    runtime bolt armed *and* a working, signed-in transport handed in,
    `meross_switching_allowed` left at its default (`False`, the same safe default
    `MqttClient.__init__` chooses) must still keep the plant from switching for
    real -- `_NetworkForbidden` fails the test the instant anything reaches the
    transport."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "eingefrorenerbolzenzone", integration_code="meross")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(
        session,
        client,
        PublicationState(),
        "thermoctl",
        NOW,
        meross_transport=_NetworkForbidden(),  # type: ignore[arg-type]
        # `meross_switching_allowed` deliberately omitted -- proving the default
        # itself is safe, not just an explicit `False`.
    )

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "suppressed"
    assert entry.error is None


@pytest.mark.anyio
async def test_a_failed_meross_send_invalidates_the_cached_session(session: Session) -> None:
    """A real attempt that did not work marks the cached connection bad, so the
    *next* cycle signs in again instead of trusting a connection already known not
    to work for the rest of its TTL (`services/meross_session.py::invalidate`)."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _actuator_zone(session, "merossfehlerzone", integration_code="meross")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    transport = MerossTransportStub(method="ERROR")
    cache = MerossSessionCache()
    await cycle(
        session,
        Mitschrift(),
        PublicationState(),
        "thermoctl",
        NOW,
        meross_transport=transport,  # type: ignore[arg-type]
        meross_session_cache=cache,
        # The frozen, start-of-process bolt (finding C); the runtime bolt is
        # already armed above.
        meross_switching_allowed=True,
    )

    assert cache.invalid is True
    entry, outcome_code = _command_log(session)[0]
    assert outcome_code == "failed"


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


# A zone with no schedule and no override falls back to frost protection
# (`domain/schedule.py::resolved_setpoint`) -- `create_settings()` builds that mode
# without a per-zone setpoint of its own, so `frost_protection_temperature()` falls
# back further, to its own hardcoded 16.0 degrees. Deterministic without building a
# schedule just for these tests, and exactly the number `Zigbee2MqttThermostat`
# below is asked to arm.
FROST_FALLBACK_SETPOINT_C = "16.0"


@pytest.mark.anyio
async def test_a_thermostat_actuator_in_a_dry_run_sends_nothing_and_the_log_shows_it(
    session: Session,
) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _thermostat_actuator_zone(session, "thermotrockenlaufzone")
    _decision(session, zone, heating=True)
    # Deliberately not armed.

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert client.switched == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "suppressed"
    assert entry.command == "thermostat"
    assert entry.error is None


@pytest.mark.anyio
async def test_an_armed_thermostat_actuator_with_system_mode_really_sends(
    session: Session,
) -> None:
    """The gegenbeweis for the thermostatic-valve actuator path: armed, a heating
    decision really arms the valve with the zone's resolved setpoint and
    `system_mode: heat`, is reported as executed, and is logged that way."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(session, "thermoscharfzone")
    _decision(session, zone, heating=True, reason="Sollwert unterschritten")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    sent = await cycle(session, client, PublicationState(), "thermoctl", NOW)

    commands = [(t, p) for t, p in client.messages if t.endswith("/set")]
    assert len(commands) == 1
    topic, payload = commands[0]
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {
        "occupied_heating_setpoint": float(FROST_FALLBACK_SETPOINT_C),
        "system_mode": "heat",
    }
    assert sent >= 1

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "executed"
    assert entry.command == "thermostat"
    assert entry.device_name == device.display_name
    assert entry.reason == "Sollwert unterschritten"
    assert entry.error is None


@pytest.mark.anyio
async def test_an_armed_thermostat_actuator_with_system_mode_switches_off_that_way(
    session: Session,
) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(session, "thermoauszone")
    _decision(session, zone, heating=False)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {"system_mode": "off"}


@pytest.mark.anyio
async def test_an_armed_thermostat_without_system_mode_heats_by_setpoint_alone(
    session: Session,
) -> None:
    """A device without `system_mode` (a Bosch BTH-RA, see `docs/offene-entscheidungen.md`)
    only ever gets `occupied_heating_setpoint` -- never a key it would silently reject."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(
        session, "thermoohnemoduszone", has_system_mode=False
    )
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {
        "occupied_heating_setpoint": float(FROST_FALLBACK_SETPOINT_C)
    }


@pytest.mark.anyio
async def test_an_armed_thermostat_without_system_mode_switches_off_by_its_minimum_setpoint(
    session: Session,
) -> None:
    """The real difference `Zigbee2MqttThermostat` documents: off through
    `system_mode` closes the valve; a device without one is switched off the way it
    is switched off by hand, its lowest accepted setpoint."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(
        session, "thermoohnemodusauszone", has_system_mode=False
    )
    _decision(session, zone, heating=False)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert topic.endswith(f"/{device.external_id}/set")
    assert json.loads(payload) == {"occupied_heating_setpoint": 5.0}


@pytest.mark.anyio
async def test_the_same_thermostat_decision_sent_twice_writes_only_one_command(
    session: Session,
) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _thermostat_actuator_zone(session, "thermowiederholungszone")
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
async def test_a_device_with_both_switch_and_thermostat_capability_only_gets_a_switch_command(
    session: Session,
) -> None:
    """`thermostat_commands()` deliberately leaves a device alone when it also
    carries the `switch` capability -- `switch_commands()` already claims it, and a
    device is not meant to get two conflicting kinds of command in the same cycle."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(
        session, "beideszone", also_switch_capable=True
    )
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert entry.command == "switch"
    assert entry.device_name == device.display_name
    topic, payload = next((t, p) for t, p in client.messages if t.endswith("/set"))
    assert json.loads(payload) == {"state": "ON"}


def test_command_descriptions_are_immutable(session: Session) -> None:
    """The selected physical target must not change after domain evaluation."""
    switch_zone, switch_device = _actuator_zone(session, "immutable-switch")
    thermostat_zone, thermostat_device = _thermostat_actuator_zone(
        session, "immutable-thermostat"
    )
    switch_command = switch_commands(session, switch_zone)[0]
    thermostat_command = thermostat_commands(session, thermostat_zone)[0]

    with pytest.raises(FrozenInstanceError):
        switch_command.integration_code = "other"
    with pytest.raises(FrozenInstanceError):
        thermostat_command.has_system_mode = False
    assert isinstance(switch_command, SwitchCommand)
    assert isinstance(thermostat_command, ThermostatCommand)


def test_capability_joins_do_not_admit_devices_with_an_unrelated_capability(
    session: Session,
) -> None:
    """A capability on some device is not evidence that this actuator supports it."""
    switch_zone, switch_device = _actuator_zone(session, "join-switch")
    switch_impostor = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="join-switch-impostor",
        display_name="join-switch-impostor",
    )
    session.add(switch_impostor)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=switch_impostor.id,
            capability_id=_capability(session, "temperature", "Temperature").id,
        )
    )
    session.add(
        ZoneDevice(
            zone_id=switch_zone.id,
            device_id=switch_impostor.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )

    thermostat_zone, thermostat_device = _thermostat_actuator_zone(
        session, "join-thermostat"
    )
    session.add(
        DeviceCapabilityLink(
            device_id=thermostat_device.id,
            capability_id=_capability(session, "humidity", "Humidity").id,
        )
    )
    session.flush()

    assert [command.device.id for command in switch_commands(session, switch_zone)] == [
        switch_device.id
    ]
    assert [command.device.id for command in thermostat_commands(session, thermostat_zone)] == [
        thermostat_device.id
    ]


def test_a_dual_capability_thermostat_does_not_hide_a_later_plain_thermostat(
    session: Session,
) -> None:
    """A conflicting first assignment must not suppress a later valid radiator."""
    zone, dual = _thermostat_actuator_zone(
        session, "dual-first", also_switch_capable=True
    )
    plain = Device(
        integration_id=integration(session, "zigbee2mqtt").id,
        external_id="plain-second",
        display_name="plain-second",
    )
    session.add(plain)
    session.flush()
    session.add(
        DeviceCapabilityLink(
            device_id=plain.id,
            capability_id=_capability(session, "thermostat", "Thermostatausgang").id,
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=plain.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
            sort_order=1,
        )
    )
    session.flush()

    assert [command.device.id for command in thermostat_commands(session, zone)] == [plain.id]
    assert dual.id != plain.id


@pytest.mark.anyio
async def test_a_thermostat_actuator_at_a_non_wired_integration_is_reported_not_sent(
    session: Session,
) -> None:
    """Not observed on real hardware (only Zigbee2MQTT reports the `thermostat`
    capability), guarded anyway -- the gap this replaces (a Zigbee2MQTT TRV without
    `self_regulating` got no command *and* no log entry) must never repeat itself
    silently for any other integration either."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, device = _thermostat_actuator_zone(
        session, "thermonichtverdrahtetzone", integration_code="meross"
    )
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
    assert entry.command == "thermostat"
    assert entry.device_name == device.display_name
    assert entry.error is not None
    assert "nicht verdrahtet" in entry.error


@pytest.mark.anyio
async def test_a_non_wired_thermostat_integration_is_logged_only_once_across_cycles(
    session: Session,
) -> None:
    """The thermostat twin of the same dedup property already checked for an
    ordinary switch above."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _thermostat_actuator_zone(
        session, "thermowiederholteanbindungzone", integration_code="meross"
    )
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    state = PublicationState()
    await cycle(session, Mitschrift(), state, "thermoctl", NOW)
    await cycle(session, Mitschrift(), state, "thermoctl", NOW)

    assert len(_command_log(session)) == 1


@pytest.mark.anyio
async def test_a_failed_thermostat_command_is_retried_every_cycle_but_logged_once(
    session: Session,
) -> None:
    """Finding A's fix, for the thermostat command kind -- the third of the three
    places `PublicationState.switch_commands` gates a send in
    `_send_actuator_switches` (plain switch, thermostat-kind, and the two
    'not wired' branches share the same cache)."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone, _device = _thermostat_actuator_zone(session, "thermofehlerwiederholzone")
    _decision(session, zone, heating=True)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    failing_client = FailingClient()
    state = PublicationState()
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)

    assert failing_client.switch_attempts == 3
    assert [code for _entry, code in _command_log(session)] == ["failed"]

    recovering_client = Mitschrift()
    await cycle(session, recovering_client, state, "thermoctl", NOW)

    assert [code for _entry, code in _command_log(session)] == ["failed", "executed"]
