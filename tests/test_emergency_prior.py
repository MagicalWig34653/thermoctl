"""`services/emergency_prior.py`: what is really known about a relay at first entry
into emergency operation (concept 3.4)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_settings, create_zone, seed_switch_command
from thermoctl.domain.emergency_cycle import PriorPhaseHint
from thermoctl.services.emergency_prior import commanded_state, entry_prior_hint

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"state": "ON"}', True),
        ('{"state": "OFF"}', False),
        ('{"togglex": {"channel": 0, "onoff": 1}}', True),
        ('{"togglex": {"channel": 0, "onoff": 0}}', False),
        ('{"state": "TOGGLE"}', None),
        ('{"togglex": {"channel": 0, "onoff": 2}}', None),
        ('{"togglex": 1}', None),
        ("[1]", None),
        ("kein json", None),
        ("{}", None),
    ],
)
def test_commanded_state_reads_both_payload_shapes(payload: str, expected: bool | None) -> None:
    assert commanded_state(payload) is expected


def _fixture(session: Session):  # type: ignore[no-untyped-def]
    create_settings(session)
    zone = create_zone(session, "prior")
    device = create_device(session, "prior-relais")
    return zone, device


def _hint(session: Session, device_id: int, *, min_on: int = 300) -> PriorPhaseHint | None:
    return entry_prior_hint(session, device_id, now=NOW, min_on_seconds=min_on)


def test_no_command_is_unknown(session: Session) -> None:
    _zone, device = _fixture(session)
    assert _hint(session, device.id) is None


def test_on_command_credits_elapsed_time_below_minimum_on(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=30), on=True)
    assert _hint(session, device.id) == PriorPhaseHint(on=True, elapsed_seconds=30)


def test_on_for_exactly_the_minimum_has_nothing_left_to_honour(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=300), on=True)
    assert _hint(session, device.id) is None
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=299), on=True)
    assert _hint(session, device.id) == PriorPhaseHint(on=True, elapsed_seconds=299)


def test_off_command_credits_elapsed_time_however_long(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=9000), on=False)
    assert _hint(session, device.id) == PriorPhaseHint(on=False, elapsed_seconds=9000)


def test_meross_payload_is_understood(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(
        session,
        zone,
        device,
        sent_at=NOW - timedelta(seconds=40),
        on=True,
        payload='{"togglex": {"channel": 0, "onoff": 1}}',
    )
    assert _hint(session, device.id) == PriorPhaseHint(on=True, elapsed_seconds=40)


def test_the_newest_executed_command_wins_regardless_of_insertion_order(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=10), on=False)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=50), on=True)
    assert _hint(session, device.id) == PriorPhaseHint(on=False, elapsed_seconds=10)


def test_failed_and_suppressed_commands_prove_nothing(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW - timedelta(seconds=60), on=True)
    seed_switch_command(
        session, zone, device, sent_at=NOW - timedelta(seconds=5), on=False, outcome="failed"
    )
    seed_switch_command(
        session, zone, device, sent_at=NOW - timedelta(seconds=4), on=False, outcome="suppressed"
    )
    assert _hint(session, device.id) == PriorPhaseHint(on=True, elapsed_seconds=60)


def test_other_commands_and_other_devices_do_not_count(session: Session) -> None:
    zone, device = _fixture(session)
    other = create_device(session, "prior-andere")
    seed_switch_command(session, zone, other, sent_at=NOW - timedelta(seconds=10), on=True)
    entry = seed_switch_command(
        session, zone, device, sent_at=NOW - timedelta(seconds=10), on=True
    )
    entry.command = "handover"
    session.flush()
    assert _hint(session, device.id) is None


def test_unreadable_payload_is_unknown(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(
        session, zone, device, sent_at=NOW - timedelta(seconds=10), on=True, payload="kaputt"
    )
    assert _hint(session, device.id) is None


def test_a_send_time_in_the_future_is_unknown(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW + timedelta(seconds=1), on=False)
    assert _hint(session, device.id) is None

def test_a_command_sent_this_very_second_credits_zero(session: Session) -> None:
    zone, device = _fixture(session)
    seed_switch_command(session, zone, device, sent_at=NOW, on=False)
    assert _hint(session, device.id) == PriorPhaseHint(on=False, elapsed_seconds=0)
