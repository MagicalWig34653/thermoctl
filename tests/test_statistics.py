"""Heating times from the shadow log.

The number on the statistics page is only as good as the calculation behind
it. Heating that shows eight hours in the statistics because the service was
down for eight hours would be worse than no statistics at all.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from tests.helpers import create_zone
from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.statistics import as_duration, heating_periods

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
    assert as_duration(59) == "1m"
    assert as_duration(35 * 60) == "35m"
    assert as_duration(4 * 3600 + 5 * 60) == "4h 05m"
