"""Heating times from the shadow log.

The number on the statistics page is only as good as the calculation behind
it. Heating that shows eight hours in the statistics because the service was
down for eight hours would be worse than no statistics at all.
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tests.helpers import command_outcome, create_zone, source
from thermoctl.db.models.state import DeviceCommand, ShadowDecision
from thermoctl.domain.statistics import (
    DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS,
    DayValue,
    RelayDayValue,
    RelayDeviceStatistics,
    ZoneStatistics,
    as_duration,
    heating_periods,
    relay_operations,
)

START = datetime(2026, 8, 24, 6, 0)


def _log(session: Session, zone_id: int, pattern: list[tuple[int, bool]]) -> None:
    """`pattern` is a sequence of (minutes since START, would heat)."""
    from decimal import Decimal

    for offset, heats in pattern:
        session.add(
            ShadowDecision(
                decided_at=START + timedelta(minutes=offset),
                zone_id=zone_id,
                temperature_c=Decimal("20.0"),
                setpoint_c=Decimal("21.0"),
                setpoint_reason="Zeitplan",
                would_heat=heats,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            )
        )
    session.flush()


def test_duration_comes_from_the_intervals(session: Session) -> None:
    """Three measurement points a minute apart, the first two heating: two minutes."""
    zone = create_zone(session, "statistikzone")
    _log(session, zone.id, [(0, True), (1, True), (2, False)])

    result = heating_periods(
        session, [zone.id], START, START + timedelta(hours=1), cycle_seconds=60
    )
    assert result[zone.id].seconds_total == 120


def test_the_last_measurement_point_does_not_count_indefinitely(session: Session) -> None:
    """After the last point, nobody knows what happened next -- it must not
    contribute anything."""
    zone = create_zone(session, "letzterpunkt")
    _log(session, zone.id, [(0, True)])
    result = heating_periods(
        session, [zone.id], START, START + timedelta(hours=1), cycle_seconds=60
    )
    assert result[zone.id].seconds_total == 0


def test_a_gap_is_capped(session: Session) -> None:
    """The service was down for eight hours. Counting that time as heating time
    would be pure fiction -- the installation reported nothing."""
    zone = create_zone(session, "luecke")
    _log(session, zone.id, [(0, True), (480, True), (481, False)])
    result = heating_periods(
        session, [zone.id], START, START + timedelta(days=1), cycle_seconds=60
    )
    # 3 minutes capped for the gap, plus the one real minute after it.
    assert result[zone.id].seconds_total == 180 + 60


def test_without_capping_it_would_be_a_full_workday(session: Session) -> None:
    """Counter-check for the capping: it is the difference between four minutes
    and eight hours. Without it, the test above would also be satisfied by a
    version that simply adds up every interval."""
    zone = create_zone(session, "ohnekappung")
    _log(session, zone.id, [(0, True), (480, True), (481, False)])
    result = heating_periods(
        session, [zone.id], START, START + timedelta(days=1), cycle_seconds=100000
    )
    assert result[zone.id].seconds_total == 480 * 60 + 60


def test_a_slower_cycle_counts_correctly(session: Session) -> None:
    """The cycle duration is configurable. A counter of "rows times cycle" would
    be wrong as soon as it had ever been different -- the intervals are correct
    regardless."""
    zone = create_zone(session, "langsam")
    _log(session, zone.id, [(0, True), (5, True), (10, False)])
    result = heating_periods(
        session, [zone.id], START, START + timedelta(hours=1), cycle_seconds=300
    )
    assert result[zone.id].seconds_total == 600


def test_days_are_kept_separate(session: Session) -> None:
    zone = create_zone(session, "tagesgrenze")
    _log(
        session,
        zone.id,
        [(0, True), (1, False), (60 * 24, True), (60 * 24 + 1, False)],
    )
    result = heating_periods(
        session, [zone.id], START, START + timedelta(days=2), cycle_seconds=60
    )
    by_day = {t.day: t.seconds for t in result[zone.id].days}
    assert by_day[START.date()] == 60
    assert by_day[(START + timedelta(days=1)).date()] == 60


def test_a_zone_without_a_log_still_appears_with_zeros(session: Session) -> None:
    """Otherwise a zone would drop out of the list as soon as it had never
    heated -- and it would look deleted instead of merely cold."""
    zone = create_zone(session, "stille-zone")
    result = heating_periods(
        session, [zone.id], START, START + timedelta(days=2), cycle_seconds=60
    )
    assert zone.id in result
    assert len(result[zone.id].days) == 3
    assert result[zone.id].seconds_total == 0


def test_statistics_values_are_immutable() -> None:
    """The view first derives one shared bar scale and then renders these values.

    Mutable fields could break the result's zone identity or make totals, bars, and
    labels describe different snapshots within the same response.
    """
    day = DayValue(date(2026, 8, 24), 60)
    zone = ZoneStatistics(17, [day])

    with pytest.raises(FrozenInstanceError):
        day.seconds = 120
    with pytest.raises(FrozenInstanceError):
        zone.zone_id = 18


@pytest.mark.parametrize("cycle_seconds", [0, 1])
def test_non_positive_and_one_second_cycles_share_the_one_second_floor(
    session: Session, cycle_seconds: int
) -> None:
    zone = create_zone(session, f"cycle-floor-{cycle_seconds}")
    session.add_all(
        [
            ShadowDecision(
                decided_at=START,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Test",
                would_heat=True,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
            ShadowDecision(
                decided_at=START + timedelta(seconds=5),
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Test",
                would_heat=False,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
        ]
    )
    session.flush()

    result = heating_periods(
        session,
        [zone.id],
        START,
        START + timedelta(minutes=1),
        cycle_seconds=cycle_seconds,
    )

    assert result[zone.id].seconds_total == 3


def test_a_two_day_inclusive_range_contains_both_days(session: Session) -> None:
    zone = create_zone(session, "two-day-range")

    result = heating_periods(
        session, [zone.id], START, START + timedelta(days=1), cycle_seconds=60
    )

    assert [value.day for value in result[zone.id].days] == [
        START.date(),
        (START + timedelta(days=1)).date(),
    ]


def test_a_day_boundary_follows_the_configured_timezone_not_utc(session: Session) -> None:
    """Grouping used to cut every day at UTC midnight -- 01:00 or 02:00 local time in

    `Europe/Berlin`, depending on daylight saving. The counter-check: a heating
    segment entirely inside the last hour of a UTC day, but already past local
    midnight, must be attributed to the *next* UTC date's local day, not the one UTC
    thinks it is in.
    """
    zone = create_zone(session, "zeitzone")
    # 2026-08-24 23:00 UTC is 2026-08-25 01:00 CEST (summer time, UTC+2): already the
    # next local day, still the same UTC day.
    late_utc = datetime(2026, 8, 24, 23, 0)
    session.add_all(
        [
            ShadowDecision(
                decided_at=late_utc,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=True,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
            ShadowDecision(
                decided_at=late_utc + timedelta(minutes=30),
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=False,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
        ]
    )
    session.flush()

    result = heating_periods(
        session,
        [zone.id],
        late_utc - timedelta(hours=1),
        late_utc + timedelta(hours=1),
        cycle_seconds=1800,
        timezone_name="Europe/Berlin",
    )
    by_day = {t.day: t.seconds for t in result[zone.id].days}
    # Attributed to 2026-08-25 (the local day), not 2026-08-24 (the UTC day the raw
    # timestamp carries).
    assert by_day.get(date(2026, 8, 25)) == 1800
    assert by_day.get(date(2026, 8, 24), 0) == 0


def test_a_spring_forward_day_is_23_hours_and_still_counted_whole(session: Session) -> None:
    """2026-03-29 is the day Europe/Berlin's clocks jump from 02:00 CET to 03:00 CEST

    -- a 23-hour local day. Bucketing by a fixed 24-hour span, or by UTC date, would
    split this day's heating across two buckets or miscount its length; bucketing by
    local calendar date does not, because the local day is the unit, not a duration.
    """
    zone = create_zone(session, "sommerzeit-beginn")
    # Local midnight 2026-03-29 00:00 CET == 2026-03-28 23:00 UTC.
    local_midnight_start = datetime(2026, 3, 28, 23, 0)
    # The following local midnight, 2026-03-30 00:00 CEST == 2026-03-29 22:00 UTC --
    # only 23 hours later in UTC, because the clocks skipped an hour in between.
    local_midnight_end = datetime(2026, 3, 29, 22, 0)
    session.add_all(
        [
            ShadowDecision(
                decided_at=local_midnight_start,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=True,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
            ShadowDecision(
                decided_at=local_midnight_end,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=False,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
        ]
    )
    session.flush()

    result = heating_periods(
        session,
        [zone.id],
        local_midnight_start,
        local_midnight_end,
        cycle_seconds=40000,
        timezone_name="Europe/Berlin",
    )
    by_day = {t.day: t.seconds for t in result[zone.id].days}
    # The whole 23-hour span lands on the local day it belongs to, not split across
    # 2026-03-28 and 2026-03-29 the way a UTC-date grouping would have done.
    assert by_day.get(date(2026, 3, 29)) == 23 * 3600
    assert by_day.get(date(2026, 3, 28), 0) == 0


def test_a_fall_back_day_is_25_hours_and_still_counted_whole(session: Session) -> None:
    """The mirror case: 2026-10-25 has 25 wall-clock hours in Europe/Berlin. A gap

    capped or grouped by a fixed 24-hour assumption would either lose the extra hour
    or attribute it to the wrong day.
    """
    zone = create_zone(session, "sommerzeit-ende")
    # Local midnight 2026-10-25 00:00 CEST == 2026-10-24 22:00 UTC.
    local_midnight_start = datetime(2026, 10, 24, 22, 0)
    # The following local midnight, 2026-10-26 00:00 CET == 2026-10-25 23:00 UTC --
    # 25 hours later in UTC, because the clocks were set back an hour in between.
    local_midnight_end = datetime(2026, 10, 25, 23, 0)
    session.add_all(
        [
            ShadowDecision(
                decided_at=local_midnight_start,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=True,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
            ShadowDecision(
                decided_at=local_midnight_end,
                zone_id=zone.id,
                temperature_c=None,
                setpoint_c=None,
                setpoint_reason="Zeitplan",
                would_heat=False,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            ),
        ]
    )
    session.flush()

    result = heating_periods(
        session,
        [zone.id],
        local_midnight_start,
        local_midnight_end,
        cycle_seconds=40000,
        timezone_name="Europe/Berlin",
    )
    by_day = {t.day: t.seconds for t in result[zone.id].days}
    assert by_day.get(date(2026, 10, 25)) == 25 * 3600
    assert by_day.get(date(2026, 10, 24), 0) == 0


def test_duration_in_words() -> None:
    assert as_duration(0) == "–"
    assert as_duration(1) == "0m"
    assert as_duration(59) == "1m"
    assert as_duration(35 * 60) == "35m"
    assert as_duration(59 * 60) == "59m"
    assert as_duration(60 * 60) == "1h 00m"
    assert as_duration(119 * 60) == "1h 59m"
    assert as_duration(120 * 60) == "2h 00m"
    assert as_duration(4 * 3600 + 5 * 60) == "4h 05m"


def _relay_command(
    session: Session,
    zone_id: int,
    device_name: str,
    at: datetime,
    payload: str,
    *,
    outcome: str = "executed",
    command: str = "switch",
) -> None:
    session.add(
        DeviceCommand(
            sent_at=at,
            source_id=source(session, "system").id,
            zone_id=zone_id,
            zone_name=f"Zone {zone_id}",
            device_id=None,
            device_name=device_name,
            command=command,
            payload=payload,
            outcome_id=command_outcome(session, outcome).id,
            error="nicht gesendet" if outcome == "failed" else None,
            reason="Test",
        )
    )
    session.flush()


def test_relay_operations_count_only_confirmed_changes_and_support_both_payloads(
    session: Session,
) -> None:
    zone = create_zone(session, "relais-zone")
    other_zone = create_zone(session, "andere-relais-zone")
    start = datetime(2026, 8, 23, 22, 0)  # 24.08. 00:00 Europe/Berlin
    end = datetime(2026, 8, 25, 10, 0)

    # The older successful ON is the baseline. The same ON after a restart is not a
    # state change; neither dry-run/failed attempts nor valve commands establish one.
    _relay_command(session, zone.id, "Steckdose", start - timedelta(minutes=1), '{"state":"ON"}')
    _relay_command(session, zone.id, "Steckdose", start, '{"state":"ON"}')
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(minutes=1), '{"state":"OFF"}',
        outcome="suppressed",
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(minutes=2), '{"state":"OFF"}',
        outcome="failed",
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(minutes=3), '{"state":"OFF"}',
        command="setpoint",
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(minutes=4), '{"heating":false}',
        command="thermostat",
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(minutes=5),
        '{"togglex":{"channel":0,"onoff":0}}',
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(days=1, minutes=5),
        '{"togglex":{"channel":0,"onoff":1}}',
    )
    _relay_command(
        session, zone.id, "Steckdose", start + timedelta(days=1, minutes=6),
        '{"state":"OFF"}',
    )
    # Same copied name in another zone must remain a separate relay history.
    _relay_command(session, other_zone.id, "Steckdose", start, '{"state":"OFF"}')
    _relay_command(
        session, other_zone.id, "Steckdose", start + timedelta(minutes=1), '{"state":"ON"}'
    )

    values = relay_operations(
        session,
        [zone.id, other_zone.id],
        start,
        end,
        timezone_name="Europe/Berlin",
    )

    by_zone = {value.zone_id: value for value in values}
    assert [(day.day, day.operations) for day in by_zone[zone.id].days] == [
        (date(2026, 8, 24), 1),
        (date(2026, 8, 25), 2),
    ]
    assert by_zone[zone.id].operations_total == 3
    assert by_zone[other_zone.id].operations_total == 1


@pytest.mark.parametrize(
    "payload",
    [
        "kein-json",
        "[]",
        '{"state":"UNKNOWN"}',
        '{"togglex":[]}',
        '{"togglex":{"onoff":2}}',
    ],
)
def test_unknown_switch_payloads_do_not_invent_relay_operations(
    session: Session, payload: str
) -> None:
    zone = create_zone(session, f"payload-{payload}")
    start = datetime(2026, 8, 24)
    _relay_command(session, zone.id, "Unbekannter Schalter", start, payload)

    values = relay_operations(session, [zone.id], start, start + timedelta(days=1))

    assert len(values) == 1
    assert values[0].operations_total == 0


def test_relay_operations_passes_the_installations_assumption_through(
    session: Session,
) -> None:
    """`relay_operations` reads no setting itself (it is a domain function); the
    caller's value has to land, unmodified, on every returned device's statistics."""
    zone = create_zone(session, "eigene-annahme")
    start = datetime(2026, 8, 24)
    _relay_command(session, zone.id, "Steckdose", start, '{"state":"ON"}')

    default = relay_operations(session, [zone.id], start, start + timedelta(days=1))
    assert default[0].assumed_lifetime_operations == 500_000

    custom = relay_operations(
        session,
        [zone.id],
        start,
        start + timedelta(days=1),
        assumed_lifetime_operations=250_000,
    )
    assert custom[0].assumed_lifetime_operations == 250_000


def test_relay_statistics_handle_empty_ranges_and_explain_the_projection(
    session: Session,
) -> None:
    start = datetime(2026, 8, 24)
    assert relay_operations(session, [], start, start) == []
    assert relay_operations(session, [17], start, start - timedelta(seconds=1)) == []
    assert relay_operations(session, [17], start, start) == []

    # The thresholds themselves are exercised here against an explicit assumption of
    # 100,000 -- independent of whatever the dataclass default happens to be, so this
    # test still says something once that default changes again.
    empty = RelayDeviceStatistics(1, "still", [], assumed_lifetime_operations=100_000)
    normal = RelayDeviceStatistics(
        1, "normal", [RelayDayValue(start.date(), 100)], assumed_lifetime_operations=100_000
    )
    warning = RelayDeviceStatistics(
        1, "warning", [RelayDayValue(start.date(), 137)], assumed_lifetime_operations=100_000
    )
    danger = RelayDeviceStatistics(
        1, "danger", [RelayDayValue(start.date(), 274)], assumed_lifetime_operations=100_000
    )

    assert empty.annual_projection == 0
    assert empty.assumed_lifetime_years is None
    assert empty.wear_level == "normal"
    assert normal.days[0].annual_projection == 36_500
    assert normal.assumed_lifetime_percent_per_year == 36.5
    assert normal.assumed_lifetime_years == pytest.approx(100_000 / 36_500)
    assert normal.wear_level == "normal"
    assert warning.annual_projection == 50_005
    assert warning.wear_level == "warning"
    assert danger.annual_projection == 100_010
    assert danger.wear_level == "danger"


def test_relay_device_statistics_default_to_the_500000_assumption() -> None:
    """The vorgabe the project owner asked for -- and it applies without a caller
    having to know about it, exactly like the setting's own column default."""
    stat = RelayDeviceStatistics(1, "vorgabe", [RelayDayValue(date(2026, 8, 24), 100)])
    assert stat.assumed_lifetime_operations == DEFAULT_ASSUMED_RELAY_LIFETIME_OPERATIONS
    assert stat.assumed_lifetime_operations == 500_000


def test_the_same_switching_pattern_warns_at_100000_and_not_at_500000() -> None:
    """The inhaltlicher Test the project owner asked for: identical switching counts,
    only the assumed lifetime differs, and that alone flips the warning."""
    days = [RelayDayValue(date(2026, 8, 24), 150)]  # annual_projection == 54,750

    strict = RelayDeviceStatistics(1, "gerat", days, assumed_lifetime_operations=100_000)
    lenient = RelayDeviceStatistics(1, "gerat", days, assumed_lifetime_operations=500_000)

    assert strict.wear_level == "warning"
    assert lenient.wear_level == "normal"
    assert strict.assumed_lifetime_percent_per_year > lenient.assumed_lifetime_percent_per_year
