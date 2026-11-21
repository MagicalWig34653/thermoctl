"""`command/temporary_setpoint` -- das Sollwertrad einer Brücke (Matter) -- und die
Rückmeldung nach einem abgelehnten Befehl.

Home Assistant bleibt bei `command/setpoint` (Modus-Temperatur, dauerhaft); das Rad einer
Brücke setzt dagegen eine befristete Übersteuerung. Beides muss nebeneinander gelten.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, operating_mode, source
from tests.test_publishing import Mitschrift
from thermoctl import app as app_module
from thermoctl.app import _execute_command, _process_mqtt_message
from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.integrations.mqtt.commands import CommandError, split_topic
from thermoctl.integrations.mqtt.publication import (
    alle_topics,
    command_topics,
    discovery_payload,
    zone_discovery,
)

ENVIRONMENT = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)


# -- Parsing ------------------------------------------------------------------------------


def test_the_temporary_setpoint_is_read_like_the_setpoint() -> None:
    command = split_topic("thermoctl/zones/7/command/temporary_setpoint", b"21.5", "thermoctl")
    assert (command.zone_id, command.kind, command.temperature) == (
        7, "temporary_setpoint", Decimal("21.5"),
    )


def test_the_temporary_setpoint_accepts_a_decimal_comma() -> None:
    command = split_topic("thermoctl/zones/1/command/temporary_setpoint", b"20,5", "thermoctl")
    assert command.temperature == Decimal("20.5")


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("thermoctl/zones/1/command/temporary_setpoint", b"warm bitte"),
        ("thermoctl/zones/1/command/temporary_setpoint", b""),
        # No subkey: a second, unchecked path to the same target otherwise.
        ("thermoctl/zones/1/command/temporary_setpoint/17", b"21"),
        ("thermoctl/zones/0/command/temporary_setpoint", b"21"),
    ],
)
def test_an_unusable_temporary_setpoint_is_refused_by_the_parser(
    topic: str, payload: bytes
) -> None:
    with pytest.raises(CommandError):
        split_topic(topic, payload, "thermoctl")


# -- Topic contract -----------------------------------------------------------------------


def test_the_topic_belongs_to_the_command_tree_and_the_contract() -> None:
    topic = command_topics(17, "haus_nord").temporary_setpoint
    assert topic == "haus_nord/zones/17/command/temporary_setpoint"
    assert topic in alle_topics(17, "haus_nord")


def test_home_assistant_discovery_keeps_using_the_setpoint_topic() -> None:
    """Not in the discovery: HA's dial means the mode, and the discovery must not
    quietly turn it into a temporary override."""
    topics = command_topics(17, "haus_nord")
    payload = discovery_payload(17, "Zone", temp_step=Decimal("0.5"), prefix="haus_nord")
    assert topics.temporary_setpoint not in payload
    assert topics.setpoint in payload
    message = zone_discovery(17, "Zone", temp_step=Decimal("0.5"), prefix="haus_nord")
    assert topics.temporary_setpoint not in message.payload


def test_a_command_with_the_new_topic_is_covered_by_the_subscriptions() -> None:
    from aiomqtt import Topic

    from thermoctl.integrations.mqtt.commands import command_subscriptions, ist_command

    topic = command_topics(3, "thermoctl").temporary_setpoint
    assert ist_command(topic, "thermoctl")
    assert any(Topic(topic).matches(pattern) for pattern in command_subscriptions("thermoctl"))


# -- Execution ----------------------------------------------------------------------------


def _zone_with_schedule_all_day(session: Session) -> Zone:
    """A schedule point at 23:59 on every weekday, so a 'next switch' always exists
    within a day whatever the real clock says -- `_execute_command` uses the clock."""
    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "radzone")
    day = create_mode(session, "tag")
    night = create_mode(session, "nacht")
    session.add_all(
        [ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
         ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0"))]
    )
    for weekday in range(1, 8):
        session.add(
            SchedulePoint(zone_id=zone.id, weekday=weekday, minute_of_day=0,
                          setpoint_mode_id=day.id)
        )
        session.add(
            SchedulePoint(zone_id=zone.id, weekday=weekday, minute_of_day=1439,
                          setpoint_mode_id=night.id)
        )
    session.flush()
    return zone


def _overrides(session: Session, zone: Zone) -> list[ZoneOverride]:
    return list(
        session.scalars(
            select(ZoneOverride).where(ZoneOverride.zone_id == zone.id).order_by(ZoneOverride.id)
        )
    )


def test_the_temporary_setpoint_command_creates_one_override_and_leaves_the_modes(
    session: Session,
) -> None:
    zone = _zone_with_schedule_all_day(session)
    modes_before = {
        row.setpoint_mode_id: row.temperature_c
        for row in session.scalars(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id))
    }

    result = _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"22,5", ENVIRONMENT
    )

    assert result is zone
    (row,) = _overrides(session, zone)
    assert row.temperature_c == Decimal("22.5")
    assert row.ends_at is not None and row.ends_at > row.starts_at
    assert row.ends_at - row.starts_at <= timedelta(days=1)
    assert {
        r.setpoint_mode_id: r.temperature_c
        for r in session.scalars(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id))
    } == modes_before


def test_two_turns_over_mqtt_leave_exactly_one_running_override(session: Session) -> None:
    zone = _zone_with_schedule_all_day(session)
    topic = f"thermoctl/zones/{zone.id}/command/temporary_setpoint"

    _execute_command(session, topic, b"22.0", ENVIRONMENT)
    _execute_command(session, topic, b"24.0", ENVIRONMENT)

    first, second = _overrides(session, zone)
    assert first.cancelled_at is not None and second.cancelled_at is None
    assert second.temperature_c == Decimal("24.0")


def test_the_setpoint_command_behaves_exactly_as_before(session: Session) -> None:
    """Regression: Home Assistant's dial still changes the mode's temperature for good
    and creates no override."""
    zone = _zone_with_schedule_all_day(session)
    now = utcnow()
    assert isinstance(now, datetime)

    _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/setpoint", b"22.5", ENVIRONMENT
    )

    assert _overrides(session, zone) == []
    temperatures = {
        row.temperature_c
        for row in session.scalars(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id))
    }
    assert Decimal("22.5") in temperatures


def test_a_refused_temporary_setpoint_is_logged_and_creates_nothing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    zone = _zone_with_schedule_all_day(session)
    zone.operating_mode = operating_mode(session, "off")
    session.flush()

    with caplog.at_level(logging.WARNING):
        _execute_command(
            session, f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"22", ENVIRONMENT
        )

    assert _overrides(session, zone) == []
    assert "abgelehnt" in caplog.text.lower() and "ausgeschaltet" in caplog.text.lower()


def test_garbage_for_the_temporary_setpoint_is_discarded(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    zone = _zone_with_schedule_all_day(session)

    with caplog.at_level(logging.WARNING):
        result = _execute_command(
            session, f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"warm", ENVIRONMENT
        )

    assert result is None
    assert _overrides(session, zone) == []
    assert "unbrauchbar" in caplog.text.lower()


# -- Answer after a refusal ---------------------------------------------------------------


class _Sessions:
    """Always the same session -- `session_scope` must not close it (see
    `test_publishing.py::test_a_command_is_answered_immediately`)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self) -> Session:
        self._session.close = lambda: None  # type: ignore[method-assign]
        return self._session


def _app(session: Session, client: Mitschrift) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(publisher=client, session_factory=_Sessions(session))
    )


@pytest.mark.anyio
async def test_a_refused_command_still_reports_the_unchanged_state(session: Session) -> None:
    """The matter bridge would otherwise stay on the value it had just sent."""
    zone = _zone_with_schedule_all_day(session)
    zone.operating_mode = operating_mode(session, "off")
    session.flush()
    client = Mitschrift()

    await _process_mqtt_message(
        _app(session, client), ENVIRONMENT,
        f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"22",
    )

    topics = client.topics()
    assert f"thermoctl/zones/{zone.id}/state/operating_mode" in topics
    assert (f"thermoctl/zones/{zone.id}/state/operating_mode", "off") in client.messages
    assert f"thermoctl/zones/{zone.id}/state/setpoint" in topics
    assert _overrides(session, zone) == []


@pytest.mark.anyio
async def test_an_out_of_range_setpoint_still_reports_the_unchanged_state(
    session: Session,
) -> None:
    zone = _zone_with_schedule_all_day(session)
    client = Mitschrift()

    await _process_mqtt_message(
        _app(session, client), ENVIRONMENT,
        f"thermoctl/zones/{zone.id}/command/setpoint", b"99",
    )

    assert f"thermoctl/zones/{zone.id}/state/setpoint" in client.topics()


@pytest.mark.anyio
async def test_a_refusal_is_not_reported_by_a_standby_instance(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = _zone_with_schedule_all_day(session)
    zone.operating_mode = operating_mode(session, "off")
    session.flush()
    monkeypatch.setattr(app_module.cluster, "is_leader", lambda *_a, **_k: False)
    client = Mitschrift()

    await _process_mqtt_message(
        _app(session, client), ENVIRONMENT,
        f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"22",
    )

    assert client.messages == []


@pytest.mark.anyio
async def test_unparsable_and_unknown_zone_commands_still_report_nothing(
    session: Session,
) -> None:
    zone = _zone_with_schedule_all_day(session)
    client = Mitschrift()
    app = _app(session, client)

    await _process_mqtt_message(
        app, ENVIRONMENT, f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"warm"
    )
    await _process_mqtt_message(
        app, ENVIRONMENT, "thermoctl/zones/999999/command/temporary_setpoint", b"22"
    )

    assert client.messages == []


@pytest.mark.anyio
async def test_a_successful_turn_reports_the_new_setpoint(session: Session) -> None:
    zone = _zone_with_schedule_all_day(session)
    client = Mitschrift()

    await _process_mqtt_message(
        _app(session, client), ENVIRONMENT,
        f"thermoctl/zones/{zone.id}/command/temporary_setpoint", b"23.5",
    )

    assert (f"thermoctl/zones/{zone.id}/state/setpoint", "23.5") in client.messages
    assert (f"thermoctl/zones/{zone.id}/state/override_active", "true") in client.messages
