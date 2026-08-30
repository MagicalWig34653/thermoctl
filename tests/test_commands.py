"""Commands arriving from outside on our own topics.

Home Assistant gets one thermostat per zone. Turning it there ends up here --
and may do exactly as much as a click in the interface, not one bit more.
"""

from decimal import Decimal

import pytest

from thermoctl.integrations.mqtt.commands import (
    CommandError,
    commands_abonnements,
    ist_command,
    zerlegen,
)


def test_the_setpoint_is_read() -> None:
    command = zerlegen("thermoctl/zones/7/command/setpoint", b"21.5", "thermoctl")
    assert command.zone_id == 7
    assert command.temperature == Decimal("21.5")


def test_the_comma_is_accepted() -> None:
    """Not every sender sends a period -- and a rejected command here would
    mean a dial that turns and does nothing."""
    command = zerlegen("thermoctl/zones/1/command/setpoint", b"20,5", "thermoctl")
    assert command.temperature == Decimal("20.5")


def test_the_operating_mode_is_read() -> None:
    command = zerlegen("thermoctl/zones/2/command/operating_mode", b"off", "thermoctl")
    assert command.operating_mode == "off"


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("thermoctl/zones/1/command/setpoint", b"warm bitte"),
        ("thermoctl/zones/1/command/operating_mode", b"gemuetlich"),
        ("thermoctl/zones/1/command/farbe", b"blau"),
        ("thermoctl/zones/0/command/setpoint", b"21"),
    ],
)
def test_unusable_input_falls_through(topic: str, payload: bytes) -> None:
    with pytest.raises(CommandError):
        zerlegen(topic, payload, "thermoctl")


def test_a_foreign_prefix_does_not_belong_to_us() -> None:
    """A broker carries more than our topics. A message from elsewhere must
    not trigger anything here -- not even if it happens to look like a match."""
    topic = "andereanlage/zones/1/command/setpoint"
    assert not ist_command(topic, "thermoctl")
    with pytest.raises(CommandError):
        zerlegen(topic, b"21", "thermoctl")


def test_state_topics_are_not_commands() -> None:
    """Otherwise our own publication would trigger our own command -- a
    feedback loop that keeps itself alive."""
    assert not ist_command("thermoctl/zones/1/state/setpoint", "thermoctl")


def test_the_subscriptions_also_cover_commands_with_a_subkey() -> None:
    """`+` in MQTT matches **exactly one** level, never zero and never two.

    With only `.../befehl/+`, `befehl/modus/3` would never arrive -- the
    per-mode dials in Home Assistant would silently have done nothing.
    """
    pattern = commands_abonnements("thermoctl")
    assert pattern == ["thermoctl/zones/+/command/+", "thermoctl/zones/+/command/+/+"]
    assert ist_command("thermoctl/zones/3/command/mode/7", "thermoctl")
    assert ist_command("thermoctl/zones/3/command/parameter/hysteresis_k", "thermoctl")


def test_the_new_command_kinds_are_read() -> None:
    boost = zerlegen("thermoctl/zones/4/command/boost", b"boost", "thermoctl")
    assert (boost.kind, boost.zone_id) == ("boost", 4)

    mode = zerlegen("thermoctl/zones/4/command/mode/9", b"19.5", "thermoctl")
    assert (mode.kind, mode.mode_id, mode.temperature) == ("mode", 9, Decimal("19.5"))

    parameter = zerlegen(
        "thermoctl/zones/4/command/parameter/hysteresis_k", b"0.4", "thermoctl"
    )
    assert (parameter.kind, parameter.parameter, parameter.zahl) == (
        "parameter", "hysteresis_k", Decimal("0.4"),
    )


@pytest.mark.parametrize(
    "topic",
    [
        # A subkey where none belongs: otherwise `befehl/sollwert/irgendwas`
        # would be a second, unchecked path to the same target.
        "thermoctl/zones/1/command/setpoint/17",
        "thermoctl/zones/1/command/boost/jetzt",
        # And the subkeys themselves must be valid.
        "thermoctl/zones/1/command/mode/0",
        "thermoctl/zones/1/command/mode/tag",
        "thermoctl/zones/1/command/parameter/Hysterese",
        "thermoctl/zones/1/command/mode",
        "thermoctl/zones/1/command/parameter",
    ],
)
def test_subkeys_are_checked(topic: str) -> None:
    with pytest.raises(CommandError):
        zerlegen(topic, b"21", "thermoctl")


# --- Execution in the application -------------------------------------------


def test_the_setpoint_changes_the_active_mode(session) -> None:
    """The thermostat in Home Assistant means the mode, not "the next two hours".

    As an override, the value would be gone again after the next schedule
    point, and the dial would seem to jump back on its own. It therefore
    changes the same row that the thermostat on the home page also changes.
    """
    from sqlalchemy import select

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.zone import ZoneSetpoint

    settings = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "befehlszone")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id,
            setpoint_mode_id=settings.frost_protection_mode_id,
            temperature_c=Decimal("16.0"),
        )
    )
    session.flush()
    environment = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/setpoint", b"22.5", environment
    )

    changed = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == settings.frost_protection_mode_id,
        )
    )
    assert changed == Decimal("22.5")
    # Counter-check: this deliberately does *not* create an override.
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()


def test_a_mode_value_and_a_control_parameter_arrive(session) -> None:
    """The per-mode and per-control-parameter dials."""
    from sqlalchemy import select

    from tests.helpers import create_mode, create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "reglerzone")
    night = create_mode(session, "nacht")
    environment = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/mode/{night.id}", b"17.5", environment
    )
    _execute_command(
        session,
        f"thermoctl/zones/{zone.id}/command/parameter/hysteresis_k",
        b"0.4",
        environment,
    )

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == night.id
        )
    ) == Decimal("17.5")
    assert zone.hysteresis_k == Decimal("0.4")


def test_a_control_parameter_outside_the_limits_is_rejected(session, caplog) -> None:
    """Counter-check: the dial in Home Assistant must not be allowed more
    than the form in the interface."""
    import logging

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "grenzzone")
    environment = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _execute_command(
            session,
            f"thermoctl/zones/{zone.id}/command/parameter/hysteresis_k",
            b"99",
            environment,
        )
    assert zone.hysteresis_k is None
    assert "abgelehnt" in caplog.text.lower()


def test_a_nonsensical_command_changes_nothing(session, caplog) -> None:
    """99 degrees from Home Assistant is no reason for a crash -- the domain
    limit applies, and the reason belongs in the log instead of vanishing
    silently."""
    import logging

    from sqlalchemy import select

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session)
    zone = create_zone(session, "unsinnzone")
    source(session, "system")
    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _execute_command(
            session, f"thermoctl/zones/{zone.id}/command/setpoint", b"99", settings
        )
    assert not session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
    ).all()
    assert "abgelehnt" in caplog.text.lower()


def test_a_command_for_an_unknown_zone_is_discarded(session, caplog) -> None:
    import logging

    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _execute_command(
            session, "thermoctl/zones/999999/command/setpoint", b"21", settings
        )
    assert "unbekannte zone" in caplog.text.lower()


def test_an_unusable_topic_is_discarded(session, caplog) -> None:
    import logging

    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _execute_command(session, "thermoctl/zones/1/command/farbe", b"blau", settings)
    assert "unbrauchbar" in caplog.text.lower()


def test_the_boost_button_brings_forward_the_next_switch_point(session) -> None:
    """The button has no value, only an event -- the payload is irrelevant."""
    from datetime import datetime

    from sqlalchemy import select

    from tests.helpers import create_mode, create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "boostzone")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=int(datetime.now().isoweekday()),
                minute_of_day=1439, setpoint_mode_id=night.id,
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    environment = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(session, f"thermoctl/zones/{zone.id}/command/boost", b"PRESS", environment)

    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert entry.temperature_c == Decimal("18.0")
    # It ends at the switch point it brings forward -- not at some arbitrary time.
    assert entry.ends_at is not None
