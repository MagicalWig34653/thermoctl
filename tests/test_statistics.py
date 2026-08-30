"""Heating times from the shadow log.

The number on the statistics page is only as good as the calculation behind
it. Heating that shows eight hours in the statistics because the service was
down for eight hours would be worse than no statistics at all.
"""

from datetime import datetime, timedelta

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


def test_duration_in_words() -> None:
    assert as_duration(0) == "–"
    assert as_duration(59) == "1m"
    assert as_duration(35 * 60) == "35m"
    assert as_duration(4 * 3600 + 5 * 60) == "4h 05m"
