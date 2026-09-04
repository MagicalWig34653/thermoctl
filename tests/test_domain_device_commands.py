"""`domain.device_commands.list_commands` -- the read side REST and MCP share."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from tests.helpers import command_outcome, create_device, create_device_command, create_zone, source
from thermoctl.db.models.state import DeviceCommand
from thermoctl.domain.device_commands import MAX_LIMIT, list_commands, naive_utc


def test_lists_newest_first(session: Session) -> None:
    zone = create_zone(session, "protokollzone")
    geraet = create_device(session, "protokollgerät")
    create_device_command(session, zone, geraet, at=datetime(2026, 8, 15, 12, 0))
    create_device_command(session, zone, geraet, at=datetime(2026, 8, 15, 13, 0))

    result = list_commands(session)

    assert [entry.sent_at for entry in result] == [
        datetime(2026, 8, 15, 13, 0),
        datetime(2026, 8, 15, 12, 0),
    ]


def test_zone_filter_matches_the_name_snapshot_of_a_deleted_zone(session: Session) -> None:
    """`zone_id` is `None`, as it is after the referenced zone was deleted --
    the row must still be findable by the name recorded at the time."""
    entry = DeviceCommand(
        sent_at=datetime(2026, 8, 15, 12, 0),
        source_id=source(session, "system").id,
        zone_id=None,
        zone_name="gelöschte-zone",
        device_id=None,
        device_name="verwaistes-geraet",
        command="setpoint",
        payload="{}",
        outcome_id=command_outcome(session, "executed").id,
    )
    session.add(entry)
    session.flush()

    result = list_commands(session, zone_name="gelöschte-zone")

    assert len(result) == 1
    assert result[0].zone_name == "gelöschte-zone"


def test_outcome_filter(session: Session) -> None:
    zone = create_zone(session, "ergebniszone")
    geraet = create_device(session, "ergebnisgerät")
    create_device_command(session, zone, geraet, outcome_code="executed")
    create_device_command(session, zone, geraet, outcome_code="failed")

    result = list_commands(session, outcome="failed")

    assert [entry.outcome for entry in result] == ["failed"]


def test_date_range_filter_is_inclusive_on_both_ends(session: Session) -> None:
    zone = create_zone(session, "zeitraumzone")
    geraet = create_device(session, "zeitraumgeraet")
    create_device_command(session, zone, geraet, at=datetime(2026, 8, 1, 0, 0))
    create_device_command(session, zone, geraet, at=datetime(2026, 8, 15, 0, 0))
    create_device_command(session, zone, geraet, at=datetime(2026, 9, 1, 0, 0))

    result = list_commands(
        session, from_at=datetime(2026, 8, 1, 0, 0), to_at=datetime(2026, 8, 15, 0, 0)
    )

    assert len(result) == 2


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_nonsensical_limit_is_refused(session: Session, limit: int) -> None:
    with pytest.raises(ValueError):
        list_commands(session, limit=limit)


def test_the_maximum_limit_is_accepted(session: Session) -> None:
    zone = create_zone(session, "obergrenzenzone")
    geraet = create_device(session, "obergrenzengerät")
    create_device_command(session, zone, geraet)

    assert len(list_commands(session, limit=MAX_LIMIT)) == 1


def test_naive_utc_passes_a_naive_value_through_unchanged() -> None:
    moment = datetime(2026, 8, 29, 8, 0)
    assert naive_utc(moment) == moment


def test_naive_utc_converts_an_aware_value() -> None:
    moment = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    assert naive_utc(moment) == datetime(2026, 8, 29, 10, 0)


def test_naive_utc_passes_none_through() -> None:
    assert naive_utc(None) is None
