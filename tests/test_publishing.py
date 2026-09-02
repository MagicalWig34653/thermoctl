"""The caller that sends its own state and registers the zones with Home Assistant.

The payloads themselves are tested in `test_veroeffentlichung.py`. Here it is
about the questions alongside that: **when** something is sent, **how** the
operating state stays visible while doing so, and when something is
deregistered.
"""

import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_all_command_outcomes,
    create_settings,
    create_zone,
    operating_mode,
    source,
)
from thermoctl.db.models.lookup import CommandOutcome
from thermoctl.db.models.state import DeviceCommand
from thermoctl.domain.control import arm
from thermoctl.domain.fault_notice import FaultNotice
from thermoctl.services.publishing import PublicationState, cycle, send_fault_notice

NOW = datetime(2026, 8, 31, 7, 0)


class Mitschrift:
    """A publisher that only records.

    It always sends -- the dry-run bolt sits in the real client and applies
    solely to switching commands. Here what is checked is *what* the service
    wants to send.
    """

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.switched: list[str] = []
        self.fluechtig: list[str] = []

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        self.messages.append((topic, payload))
        if switches:
            self.switched.append(topic)
        if not retained:
            self.fluechtig.append(topic)
        return True

    def topics(self) -> list[str]:
        return [t for t, _ in self.messages]


async def _run(session: Session, state: PublicationState) -> Mitschrift:
    client = Mitschrift()
    await cycle(session, client, state, "thermoctl", NOW)
    return client


@pytest.mark.anyio
async def test_publishing_happens_in_dry_run(session: Session) -> None:
    """A state notice moves nothing. An integration that can only be tried out
    after arming is exactly the one that can no longer be checked safely once an
    error would still be without consequence."""
    create_settings(session)
    zone = create_zone(session, "probezone")

    client = await _run(session, PublicationState())

    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in client.topics()
    assert (
        f"homeassistant/binary_sensor/thermoctl_zone_{zone.id}_sensorstoerung/config"
        in client.topics()
    )
    assert f"thermoctl/zones/{zone.id}/state/setpoint" in client.topics()


@pytest.mark.anyio
async def test_none_of_these_messages_switches(session: Session) -> None:
    """Counter-check to the line above: something is published, nothing is
    switched. Without it, the test above it would also be satisfied by a
    version that moves valves during a dry run."""
    create_settings(session)
    create_zone(session, "harmlos")
    client = await _run(session, PublicationState())
    assert client.switched == []


@pytest.mark.anyio
async def test_fault_and_all_clear_reach_home_assistant_even_in_dry_run() -> None:
    client = Mitschrift()
    fault = FaultNotice(
        "sensor:7",
        "stoerung",
        "Sensorstörung in Flur",
        "Die Zone regelt gegen 16.0 °C.",
    )
    all_clear = FaultNotice(
        "sensor:7",
        "entwarnung",
        "Sensor in Flur wieder in Ordnung",
        "Die Zone regelt wieder normal.",
    )

    await send_fault_notice(client, fault, "thermoctl")
    await send_fault_notice(client, all_clear, "thermoctl")

    base = "thermoctl/zones/7/state/sensor_fault"
    states = [payload for topic, payload in client.messages if topic == base]
    attributes = [
        json.loads(payload)
        for topic, payload in client.messages
        if topic == f"{base}/attributes"
    ]
    assert states == ["ON", "OFF"]
    assert [item["schwere"] for item in attributes] == ["stoerung", "entwarnung"]
    assert client.switched == []
    assert client.fluechtig == []


@pytest.mark.anyio
async def test_a_home_assistant_notice_failure_does_not_escape() -> None:
    class BrokenPublisher:
        async def publishing(
            self,
            topic: str,
            payload: str,
            *,
            switches: bool,
            retained: bool = False,
        ) -> bool:
            raise OSError("Broker nicht erreichbar")

    await send_fault_notice(
        BrokenPublisher(),
        FaultNotice("sensor:7", "stoerung", "Sensorstörung", "Text"),
        "thermoctl",
    )


@pytest.mark.anyio
async def test_dry_run_no_longer_appears_in_the_name(session: Session) -> None:
    """It used to be there because it was visible -- and exactly that made it wrong.

    Home Assistant derives the entity identifier from the name the first time it
    appears. A zone that first showed up during a dry run was afterwards forever
    called `climate.thermoctl_zone_1_trockenlauf`, even once armed.
    """
    create_settings(session)
    zone = create_zone(session, "namenszone")

    client = await _run(session, PublicationState())
    login = dict(client.messages)[
        f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    ]
    assert "rockenlauf" not in login


@pytest.mark.anyio
async def test_the_identifier_stays_the_same_across_arming(
    session: Session,
) -> None:
    """The counter-check to the line above, and the actual guarantee.

    What is compared is the whole registration, not just the name: if anything
    in it depended on the operating state, this test would find it -- and the
    entity in Home Assistant would have changed upon arming.
    """
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "kennungszone")
    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"

    trocken = dict((await _run(session, PublicationState())).messages)[config]
    arm(session, True, reason="Test", user_id=None)
    geschaerft = dict((await _run(session, PublicationState())).messages)[config]

    assert trocken == geschaerft
    assert '"unique_id":"thermoctl_zone_' in trocken
    assert '"object_id":"thermoctl_zone_' in trocken


@pytest.mark.anyio
async def test_the_operating_state_lives_in_its_own_entity(session: Session) -> None:
    """It has to stay visible -- just not in the name of a different entity."""
    create_settings(session)
    source(session, "web")
    create_zone(session, "zustandszone")

    trocken = dict((await _run(session, PublicationState())).messages)
    assert "homeassistant/binary_sensor/thermoctl_scharf/config" in trocken
    armed_config = json.loads(trocken["homeassistant/binary_sensor/thermoctl_scharf/config"])
    assert armed_config["name"] == "Regelung scharf"
    assert trocken["thermoctl/state/armed"] == "false"

    arm(session, True, reason="Test", user_id=None)
    geschaerft = dict((await _run(session, PublicationState())).messages)
    assert geschaerft["thermoctl/state/armed"] == "true"


@pytest.mark.anyio
async def test_discoveries_and_state_go_out_retained(session: Session) -> None:
    """Without retain, Home Assistant shows an empty card after every restart.

    A whole control cycle passes before the service sends again -- and when
    switching a mode, it looked as if the command had been swallowed.
    """
    create_settings(session)
    create_zone(session, "behaltene-zone")
    client = await _run(session, PublicationState())
    assert client.messages
    assert client.fluechtig == []


@pytest.mark.anyio
async def test_boost_timestamps_modes_and_parameters_are_offered_per_zone(
    session: Session,
) -> None:
    """Whatever should be operable per zone in Home Assistant must also be registered."""
    create_settings(session)
    zone = create_zone(session, "vollausstattung")
    client = await _run(session, PublicationState())
    topics = set(client.topics())
    identifier = f"thermoctl_zone_{zone.id}"

    assert f"homeassistant/button/{identifier}_boost/config" in topics
    assert f"homeassistant/sensor/{identifier}_last_switch/config" in topics
    assert f"homeassistant/sensor/{identifier}_next_switch/config" in topics
    # One dial per control parameter, and its state.
    for name in ("hysteresis_k", "min_on_seconds", "temperature_offset_k"):
        assert f"homeassistant/number/{identifier}_parameter_{name}/config" in topics
        assert f"thermoctl/zones/{zone.id}/state/parameter/{name}" in topics
    # One dial per mode. Which modes exist is decided by the plant.
    modes = [t for t in topics if t.startswith(f"homeassistant/number/{identifier}_modus_")]
    assert modes, "no mode registered"


@pytest.mark.anyio
async def test_without_a_change_nothing_is_registered_again(session: Session) -> None:
    """Otherwise a discovery message would go out per zone and minute -- a lot of
    traffic for a statement that has not changed."""
    create_settings(session)
    zone = create_zone(session, "einmal-zone")
    state = PublicationState()
    await _run(session, state)

    zweiter = await _run(session, state)
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" not in zweiter.topics()
    assert f"thermoctl/zones/{zone.id}/state/setpoint" in zweiter.topics()


@pytest.mark.anyio
async def test_dry_run_does_not_deregister(session: Session) -> None:
    """Deregistering and re-registering on every switch would make the entity
    briefly disappear in Home Assistant -- history data and automations there
    would run into a void."""
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, "bleibende-zone")
    arm(session, True, reason="Test", user_id=None)
    state = PublicationState()
    await _run(session, state)

    arm(session, False, reason="", user_id=None)
    client = await _run(session, state)

    config = f"homeassistant/climate/thermoctl_zone_{zone.id}/config"
    assert (config, "") not in client.messages
    assert zone.id in state.registered


@pytest.mark.anyio
async def test_only_a_deleted_zone_is_deregistered(session: Session) -> None:
    """The only reason for a deregistration. Without it, a thermostat that no one
    operates anymore would be left standing in Home Assistant."""
    create_settings(session)
    zone = create_zone(session, "verschwindende-zone")
    state = PublicationState()
    await _run(session, state)

    session.delete(zone)
    session.flush()
    client = await _run(session, state)

    # Every entity of the zone, not just the thermostat: otherwise the boost
    # button and dials of a deleted zone would be left standing in Home
    # Assistant.
    abgemeldet = {topic for topic, payload in client.messages if payload == ""}
    assert f"homeassistant/climate/thermoctl_zone_{zone.id}/config" in abgemeldet
    assert f"homeassistant/button/thermoctl_zone_{zone.id}_boost/config" in abgemeldet
    assert state.registered == {}


@pytest.mark.anyio
async def test_a_missing_reading_is_not_sent_as_an_empty_payload(
    session: Session,
) -> None:
    """In MQTT, an empty payload deletes a retained message. 'No reading yet' is
    something different from 'this value no longer exists'."""
    create_settings(session)
    zone = create_zone(session, "messwertlose-zone")

    client = await _run(session, PublicationState())
    assert f"thermoctl/zones/{zone.id}/state/current_temperature" not in client.topics()


@pytest.mark.anyio
async def test_the_setpoint_is_sent_with_a_decimal_point(session: Session) -> None:
    """MQTT is not a user interface: Home Assistant expects a number, not a German
    comma."""
    create_settings(session)
    zone = create_zone(session, "punktzone")
    client = await _run(session, PublicationState())
    setpoint = dict(client.messages)[f"thermoctl/zones/{zone.id}/state/setpoint"]
    assert "," not in setpoint
    assert Decimal(setpoint) > 0


@pytest.mark.anyio
async def test_a_command_is_answered_immediately(session: Session) -> None:
    """The climate card in Home Assistant is not optimistic.

    It waits for the state and shows the old one until then. If it only arrived
    on the next control cycle, the operating mode just chosen would jump back
    for a minute -- and to the user it looked as if it could not be changed.
    """
    from types import SimpleNamespace

    from thermoctl.app import _process_mqtt_message
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "antwortzone")
    operating_mode(session, "off")
    client = Mitschrift()

    class _Sessions:
        """Always returns the same session -- `session_scope` must not close it.

        The fixture keeps the transaction open and cleans up itself afterwards;
        a `close()` in the middle would detach every already-loaded object from
        it.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(publisher=client, session_factory=_Sessions())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _process_mqtt_message(
        app, umgebung, f"thermoctl/zones/{zone.id}/command/operating_mode", b"off"
    )

    # The new value, not the old one: whoever only rewrites the foreign key
    # leaves an already-loaded `zone.operating_mode` in place -- and used to
    # report "auto" here.
    assert (f"thermoctl/zones/{zone.id}/state/operating_mode", "off") in client.messages
    assert zone.operating_mode.code == "off"


@pytest.mark.anyio
async def test_a_discarded_command_triggers_no_message(session: Session) -> None:
    """Counter-check: otherwise the service would also respond to nonsense and to foreign topics."""
    from types import SimpleNamespace

    from thermoctl.app import _process_mqtt_message
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "stillezone")
    client = Mitschrift()

    class _Sessions:
        """Always returns the same session -- `session_scope` must not close it.

        The fixture keeps the transaction open and cleans up itself afterwards;
        a `close()` in the middle would detach every already-loaded object from
        it.
        """

        def __call__(self) -> Session:
            session.close = lambda: None  # type: ignore[method-assign]
            return session

    app = SimpleNamespace(
        state=SimpleNamespace(publisher=client, session_factory=_Sessions())
    )
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    await _process_mqtt_message(
        app, umgebung, f"thermoctl/zones/{zone.id}/command/operating_mode", b"gemuetlich"
    )

    assert client.messages == []


@pytest.mark.anyio
async def test_state_switch_times_and_sensor_situation_go_along(session: Session) -> None:
    """Whatever Home Assistant should display per zone must also be sent.

    "Last switch" here is not the last control cycle, but the last *change*:
    otherwise it would always say "a minute ago".
    """
    from tests.helpers import create_zone_state, sensor_status_of
    from thermoctl.db.models.state import ShadowDecision

    create_settings(session)
    zone = create_zone(session, "zustandsreiche-zone")
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("20.5")
    state.sensor_status_id = sensor_status_of(session, "veraltet").id
    session.add_all(
        [
            ShadowDecision(
                decided_at=datetime(2026, 8, 31, 5, 0), zone_id=zone.id,
                setpoint_reason="Plan", would_heat=True, previous_would_heat=False,
                outcome_code="wuerde_heizen", reason="kalt",
            ),
            ShadowDecision(
                decided_at=datetime(2026, 8, 31, 6, 30), zone_id=zone.id,
                setpoint_reason="Plan", would_heat=True, previous_would_heat=True,
                outcome_code="wuerde_heizen", reason="weiter",
            ),
        ]
    )
    session.flush()

    messages = dict((await _run(session, PublicationState())).messages)
    base = f"thermoctl/zones/{zone.id}/state"

    assert messages[f"{base}/current_temperature"] == "20.5"
    assert messages[f"{base}/sensor_state"] == "veraltet"
    assert messages[f"{base}/would_heat"] == "true"
    # 05:00, not 06:30: at 06:30 only what already held was confirmed.
    # With a time zone, because `device_class: timestamp` requires one.
    assert messages[f"{base}/last_switch"] == "2026-08-31T05:00:00+00:00"


def _zone_with_self_regulating_valve(  # type: ignore[no-untyped-def]
    session: Session, name: str, *, external_temperature: bool = False
):
    """A zone whose valve regulates itself, with a setpoint of 21 degrees."""
    from decimal import Decimal as _Decimal

    from tests.helpers import create_device, create_mode, role
    from thermoctl.db.models.device import (
        DeviceCapabilityLink,
        DeviceProperty,
        ZoneDevice,
    )
    from thermoctl.db.models.lookup import DeviceCapability
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    zone = create_zone(session, name)
    mode = create_mode(session, f"tag-{name}")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=_Decimal("21.0"))
    )
    # Ohne Schaltpunkt faellt der Sollwert auf den Frostschutz zurueck -- der Test
    # pruefte dann die Ausweichregel statt des Zeitplans.
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=NOW.isoweekday(), minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    valve = create_device(session, f"{name}-ventil")
    capability = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    if capability is None:
        capability = DeviceCapability(code="thermostat", label="Thermostatventil")
        session.add(capability)
        session.flush()
    session.add(DeviceCapabilityLink(device_id=valve.id, capability_id=capability.id))
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=valve.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=True,
        )
    )
    session.add(
        DeviceProperty(
            device_id=valve.id,
            name="occupied_heating_setpoint",
            value_type="numeric",
            unit="°C",
            min_value=_Decimal("5"),
            max_value=_Decimal("30"),
            is_readable=True,
            is_writable=True,
        )
    )
    if external_temperature:
        session.add(
            DeviceProperty(
                device_id=valve.id,
                name="external_temperature_input",
                value_type="numeric",
                unit="°C",
                min_value=_Decimal("-40"),
                max_value=_Decimal("125"),
                is_readable=True,
                is_writable=True,
            )
        )
    session.flush()
    return zone


@pytest.mark.anyio
async def test_a_self_regulating_valve_is_not_written_to_in_the_dry_run(
    session: Session,
) -> None:
    """The whole point of the two bolts, on the newest path to a valve.

    A setpoint written to a thermostatic valve moves a valve motor. It is not a
    display value, and treating it as one would be a way around the dry run -- so it
    travels as a switching message and nothing at all leaves during the dry run.
    """
    create_settings(session)
    source(session, "web")
    _zone_with_self_regulating_valve(session, "trockenlaufzone")

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert [topic for topic in client.topics() if topic.endswith("/set")] == []
    assert client.switched == []


@pytest.mark.anyio
async def test_an_armed_plant_tells_the_valve_its_setpoint_once(session: Session) -> None:
    """Armed it goes out -- and only when something changed.

    The setpoint stands still for hours; a battery-powered valve should not get the
    same number every cycle.
    """
    create_settings(session)
    source(session, "web")
    _zone_with_self_regulating_valve(session, "scharfzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client, state = Mitschrift(), PublicationState()
    await cycle(session, client, state, "thermoctl", NOW)

    commands = [(t, p) for t, p in client.messages if t.endswith("/set")]
    assert len(commands) == 1
    topic, payload = commands[0]
    assert topic.endswith("/scharfzone-ventil/set")
    assert json.loads(payload)["occupied_heating_setpoint"] == 21.0
    # It moves a valve, so it is a switching message.
    assert topic in client.switched

    await cycle(session, client, state, "thermoctl", NOW)
    assert len([t for t, _ in client.messages if t.endswith("/set")]) == 1


@pytest.mark.anyio
async def test_the_measured_room_temperature_goes_out_with_the_setpoint(
    session: Session,
) -> None:
    """Both in one message, and that is deliberate.

    The valve is meant to regulate against the room, not against the radiator it is
    screwed to. Sent separately the two could arrive in either order, and a valve that
    briefly has the new setpoint and the old temperature would act on a combination
    that was never intended.
    """
    from tests.helpers import create_zone_state

    create_settings(session)
    source(session, "web")
    zone = _zone_with_self_regulating_valve(
        session, "aussenfuehlerzone", external_temperature=True
    )
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("19.5")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    payload = next(p for t, p in client.messages if t.endswith("/set"))
    assert json.loads(payload) == {
        "occupied_heating_setpoint": 21.0,
        "external_temperature_input": 19.5,
    }


class FailingClient:
    """A publisher whose switching commands always raise -- the broker is gone."""

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            raise ConnectionError("Broker nicht erreichbar")
        return True


class CountingFailingClient:
    """Like `FailingClient`, but counts every switching attempt made through it --
    needed to prove a retry actually happens (finding A), not just that the log
    entry looks right."""

    def __init__(self) -> None:
        self.switch_attempts = 0

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        if switches:
            self.switch_attempts += 1
            raise ConnectionError("Broker nicht erreichbar")
        return True


class RejectingClient:
    """A publisher whose switching commands come back `False` -- no exception, no
    confirmation. Distinct from `FailingClient`: this is the ordinary dry-run-bolt
    rejection inside the real MQTT client (see `integrations/mqtt/client.py`), not
    a broken connection."""

    async def publishing(
        self, topic: str, payload: str, *, switches: bool, retained: bool = False
    ) -> bool:
        return not switches


def _command_log(session: Session) -> list[tuple[DeviceCommand, str]]:
    rows = session.execute(
        select(DeviceCommand, CommandOutcome.code)
        .join(CommandOutcome, CommandOutcome.id == DeviceCommand.outcome_id)
        .order_by(DeviceCommand.id)
    ).all()
    return [(entry, code) for entry, code in rows]


@pytest.mark.anyio
async def test_a_sent_setpoint_writes_exactly_one_executed_log_entry(
    session: Session,
) -> None:
    """The requirement, stated plainly: one entry per command, with the right outcome."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    zone = _zone_with_self_regulating_valve(session, "protokollzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    await cycle(session, Mitschrift(), PublicationState(), "thermoctl", NOW)

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "executed"
    assert entry.zone_id == zone.id
    assert entry.zone_name == zone.display_name
    assert entry.device_name == "protokollzone-ventil"
    assert entry.command == "setpoint"
    assert json.loads(entry.payload)["occupied_heating_setpoint"] == 21.0
    assert entry.error is None
    assert entry.reason


@pytest.mark.anyio
async def test_the_dry_run_writes_one_suppressed_entry_and_sends_nothing(
    session: Session,
) -> None:
    """Without this the requirement to trace a *withheld* command would be untested."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "trockenlaufprotokollzone")
    # Deliberately not armed.

    client = Mitschrift()
    await cycle(session, client, PublicationState(), "thermoctl", NOW)

    assert [t for t in client.topics() if t.endswith("/set")] == []
    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "suppressed"
    assert entry.error is None


@pytest.mark.anyio
async def test_a_failed_send_writes_an_entry_naming_the_reason(session: Session) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "fehlerzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    await cycle(session, FailingClient(), PublicationState(), "thermoctl", NOW)

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "failed"
    assert entry.error is not None
    assert "Broker nicht erreichbar" in entry.error


@pytest.mark.anyio
async def test_a_rejected_send_without_an_exception_also_writes_a_failed_entry(
    session: Session,
) -> None:
    """Distinct from the exception path above: the client can also just say no."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "abgewiesenzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    await cycle(session, RejectingClient(), PublicationState(), "thermoctl", NOW)

    entries = _command_log(session)
    assert len(entries) == 1
    entry, outcome_code = entries[0]
    assert outcome_code == "failed"
    assert entry.error == "MQTT-Client hat die Veroeffentlichung abgewiesen"


@pytest.mark.anyio
async def test_the_same_setpoint_sent_twice_writes_only_one_entry(session: Session) -> None:
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "wiederholungszone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    client, state = Mitschrift(), PublicationState()
    await cycle(session, client, state, "thermoctl", NOW)
    await cycle(session, client, state, "thermoctl", NOW)

    assert len(_command_log(session)) == 1


@pytest.mark.anyio
async def test_a_withheld_command_is_logged_again_once_armed(session: Session) -> None:
    """The same setpoint, unchanged -- but the outcome changed, and that is a new fact."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "uebergangszone")

    client, state = Mitschrift(), PublicationState()
    await cycle(session, client, state, "thermoctl", NOW)
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()
    await cycle(session, client, state, "thermoctl", NOW)

    entries = _command_log(session)
    assert [code for _entry, code in entries] == ["suppressed", "executed"]


@pytest.mark.anyio
async def test_a_failed_setpoint_is_retried_every_cycle_but_logged_only_once(
    session: Session,
) -> None:
    """Cross-review finding A. Before the fix, the cache key was `(payload, armed)`
    -- written on every outcome, including a failed one -- so a second cycle with
    the same (unchanged) setpoint and the same broken client found an identical
    cache entry and skipped the device entirely: no send attempt, no log line. A
    zone that needs heat and cannot reach its actuator would then stay silent
    until its boolean heating decision happened to flip, which for a cold,
    persistently underserved zone is exactly the case that does not happen.

    The fix retries the real send every armed cycle regardless of the last
    outcome, but only *logs* a new entry when the outcome actually changes --
    otherwise a permanently unreachable device would fill the command log with an
    identical line every minute. This proves both halves: the attempt count keeps
    climbing (recovery stays possible) while the log stays at one entry (no
    spam) -- and that recovery, once the device answers again, produces exactly
    one further entry."""
    create_settings(session)
    source(session, "system")
    create_all_command_outcomes(session)
    _zone_with_self_regulating_valve(session, "wiederholzone")
    arm(session, True, reason="vier Tage verglichen", user_id=None)
    session.flush()

    failing_client = CountingFailingClient()
    state = PublicationState()
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)
    await cycle(session, failing_client, state, "thermoctl", NOW)

    # The property this test exists for: three cycles, three real attempts --
    # without the fix this would be 1, because the second and third cycle would
    # skip the device entirely.
    assert failing_client.switch_attempts == 3
    entries = _command_log(session)
    assert [code for _entry, code in entries] == ["failed"]

    # Gegenprobe for the other half: once the device answers again, exactly one
    # further entry appears -- not one per cycle that failed before it.
    recovering_client = Mitschrift()
    await cycle(session, recovering_client, state, "thermoctl", NOW)

    entries = _command_log(session)
    assert [code for _entry, code in entries] == ["failed", "executed"]


def test_deleting_a_zone_and_its_device_keeps_the_command_log_entry(session: Session) -> None:
    """Unlike `shadow_decision` (CASCADE), this table is meant to answer questions
    about a zone and device that no longer exist -- so the row must survive, and
    keep saying what they were called."""
    from tests.helpers import create_device

    create_settings(session)
    zone = create_zone(session, "loeschzone")
    device = create_device(session, "loeschventil")
    from tests.helpers import command_outcome

    outcome_id = command_outcome(session, "executed").id
    entry = DeviceCommand(
        sent_at=NOW,
        source_id=source(session, "system").id,
        zone_id=zone.id,
        zone_name=zone.display_name,
        device_id=device.id,
        device_name=device.display_name,
        command="setpoint",
        payload='{"occupied_heating_setpoint": 21.0}',
        outcome_id=outcome_id,
    )
    session.add(entry)
    session.flush()
    entry_id, zone_name, device_name = entry.id, zone.display_name, device.display_name

    session.delete(zone)
    session.delete(device)
    session.flush()
    # Without expire_all() the query would return the object still held in memory
    # -- with the reference values it had *before* the database applied ON DELETE
    # SET NULL, as in `tests/test_zone.py`.
    session.expire_all()

    survivor = session.get(DeviceCommand, entry_id)
    assert survivor is not None
    assert survivor.zone_id is None
    assert survivor.device_id is None
    assert survivor.zone_name == zone_name
    assert survivor.device_name == device_name
