"""Pure logic: when does a forgotten open window plus cold outdoor air count as
an alarm."""

from datetime import datetime, timedelta
from decimal import Decimal

from thermoctl.domain.fault import NO_SOURCE, OK, VERALTET
from thermoctl.domain.window_alarm import window_alarm_state

NOW = datetime(2026, 9, 6, 12, 0, 0)
OPEN_AFTER_MINUTES = 30
THRESHOLD_C = Decimal("5.0")


def _state(*, opened_minutes_ago: int, outdoor_c: Decimal, status: str = OK) -> bool | None:
    return window_alarm_state(
        window_open_since=NOW - timedelta(minutes=opened_minutes_ago),
        now=NOW,
        open_after_minutes=OPEN_AFTER_MINUTES,
        outdoor_status=status,
        outdoor_temperature_c=outdoor_c,
        threshold_c=THRESHOLD_C,
    )


def test_no_window_open_is_no_alarm_even_when_cold() -> None:
    assert (
        window_alarm_state(
            window_open_since=None,
            now=NOW,
            open_after_minutes=OPEN_AFTER_MINUTES,
            outdoor_status=OK,
            outdoor_temperature_c=Decimal("-5.0"),
            threshold_c=THRESHOLD_C,
        )
        is False
    )


def test_both_conditions_must_hold_at_once() -> None:
    # Window open long enough, but not cold enough.
    assert _state(opened_minutes_ago=45, outdoor_c=Decimal("10.0")) is False
    # Cold enough, but window not open long enough.
    assert _state(opened_minutes_ago=5, outdoor_c=Decimal("-5.0")) is False
    # Both hold.
    assert _state(opened_minutes_ago=45, outdoor_c=Decimal("-5.0")) is True


def test_the_duration_threshold_is_strictly_more_than_not_at_least() -> None:
    """"seit mehr als" -- exactly at the threshold does not yet count."""
    assert _state(opened_minutes_ago=OPEN_AFTER_MINUTES, outdoor_c=Decimal("0.0")) is False
    assert _state(opened_minutes_ago=OPEN_AFTER_MINUTES + 1, outdoor_c=Decimal("0.0")) is True


def test_the_temperature_threshold_is_strictly_under_not_at_most() -> None:
    """"unter" -- exactly at the threshold does not yet count."""
    assert _state(opened_minutes_ago=OPEN_AFTER_MINUTES + 1, outdoor_c=THRESHOLD_C) is False
    assert (
        _state(opened_minutes_ago=OPEN_AFTER_MINUTES + 1, outdoor_c=THRESHOLD_C - Decimal("0.1"))
        is True
    )


def test_an_untrustworthy_outdoor_reading_is_unknown_not_no_alarm() -> None:
    """`None` is a distinct, third answer -- never collapsed into `False`."""
    for status in (NO_SOURCE, VERALTET):
        assert _state(opened_minutes_ago=60, outdoor_c=Decimal("-5.0"), status=status) is None


def test_a_missing_temperature_value_is_also_unknown() -> None:
    assert (
        window_alarm_state(
            window_open_since=NOW - timedelta(minutes=60),
            now=NOW,
            open_after_minutes=OPEN_AFTER_MINUTES,
            outdoor_status=OK,
            outdoor_temperature_c=None,
            threshold_c=THRESHOLD_C,
        )
        is None
    )
