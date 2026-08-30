"""The caller that sends its own state and registers the zones with Home Assistant.

The payloads themselves are tested in `test_veroeffentlichung.py`. Here it is
about the questions alongside that: **when** something is sent, **how** the
operating state stays visible while doing so, and when something is
deregistered.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, operating_mode, source
from thermoctl.domain.control import arm
from thermoctl.services.publishing import PublicationState, cycle

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
