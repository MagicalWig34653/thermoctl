"""`record_command` on its own -- especially its promise not to block the caller."""

import logging
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import command_outcome, create_device, create_zone, source
from thermoctl.db.models.state import DeviceCommand
from thermoctl.services.device_commands import EXECUTED, record_command

NOW = datetime(2026, 8, 15, 12, 0, 0)


def test_a_valid_call_writes_one_row(session: Session) -> None:
    source(session, "system")
    command_outcome(session, "executed")
    zone = create_zone(session, "einheitzone")
    device = create_device(session, "einheitventil")

    record_command(
        session, now=NOW, source="system", zone=zone, device=device,
        command="setpoint", payload='{"a": 1}', outcome=EXECUTED, reason="Zeitplan",
    )

    entry = session.scalar(select(DeviceCommand))
    assert entry is not None
    assert entry.zone_id == zone.id
    assert entry.device_id == device.id
    assert entry.payload == '{"a": 1}'
    assert entry.reason == "Zeitplan"


def test_an_unknown_source_is_logged_and_swallowed_not_raised(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Grundsatz 7: a broken log write must not stop the caller -- but also must
    not vanish without a trace. Both halves are checked here."""
    command_outcome(session, "executed")
    zone = create_zone(session, "namenloszone")
    device = create_device(session, "namenlosventil")

    with caplog.at_level(logging.ERROR, logger="thermoctl.services.device_commands"):
        # No source "geist" exists -- must not raise.
        record_command(
            session, now=NOW, source="geist", zone=zone, device=device,
            command="setpoint", payload='{"a": 1}', outcome=EXECUTED,
        )

    assert session.scalar(select(DeviceCommand)) is None
    assert "konnte nicht geschrieben werden" in caplog.text


def test_an_unknown_outcome_is_logged_and_swallowed_not_raised(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    source(session, "system")
    zone = create_zone(session, "unbekanntzone")
    device = create_device(session, "unbekanntventil")

    with caplog.at_level(logging.ERROR, logger="thermoctl.services.device_commands"):
        record_command(
            session, now=NOW, source="system", zone=zone, device=device,
            command="setpoint", payload='{"a": 1}', outcome="nirgendwo",
        )

    assert session.scalar(select(DeviceCommand)) is None
    assert "konnte nicht geschrieben werden" in caplog.text


def test_a_failed_write_does_not_poison_the_surrounding_transaction(
    session: Session,
) -> None:
    """The savepoint is the point: a broken entry must not take a good one down
    with it, and both live in the same publication cycle's transaction."""
    source(session, "system")
    command_outcome(session, "executed")
    zone = create_zone(session, "nachbarzone")
    device = create_device(session, "nachbarventil")

    record_command(
        session, now=NOW, source="geist", zone=zone, device=device,
        command="setpoint", payload='{"a": 1}', outcome=EXECUTED,
    )
    record_command(
        session, now=NOW, source="system", zone=zone, device=device,
        command="setpoint", payload='{"a": 2}', outcome=EXECUTED,
    )
    session.flush()

    entry = session.scalar(select(DeviceCommand))
    assert entry is not None
    assert entry.payload == '{"a": 2}'
