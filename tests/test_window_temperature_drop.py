from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.window_temperature_drop import (
    WINDOW_TEMP_DROP_THRESHOLD_K,
    temperature_detection_still_holding,
    temperature_drop_history_cutoff,
    window_open_suspected,
)

NOW = datetime(2026, 9, 6, 18, 0)


# --- window_open_suspected ----------------------------------------------------


def test_a_steep_drop_at_exactly_the_threshold_is_suspected() -> None:
    """The boundary belongs to the suspicious side, the same convention
    `stuck_reading`'s own boundary test in `tests/test_fault.py` uses."""
    values = [Decimal("21.0"), Decimal("21.0") - WINDOW_TEMP_DROP_THRESHOLD_K]

    assert window_open_suspected(values, history_covers_duration=True) is True


def test_a_drop_just_under_the_threshold_is_not_suspected() -> None:
    values = [
        Decimal("21.0"),
        Decimal("21.0") - WINDOW_TEMP_DROP_THRESHOLD_K + Decimal("0.01"),
    ]

    assert window_open_suspected(values, history_covers_duration=True) is False


def test_a_slow_ordinary_cooldown_never_triggers() -> None:
    """The motivating negative case: heating stops, the room drifts down a few
    tenths of a Kelvin across the window -- nowhere near a window being opened."""
    values = [Decimal("21.00"), Decimal("20.95"), Decimal("20.90"), Decimal("20.85")]

    assert window_open_suspected(values, history_covers_duration=True) is False


def test_the_end_of_a_heating_phase_is_not_a_drop() -> None:
    """Temperature rising while heat was on, then levelling off right at the
    setpoint as the heater switches off -- the room never actually falls."""
    values = [Decimal("20.50"), Decimal("20.90"), Decimal("21.00"), Decimal("21.00")]

    assert window_open_suspected(values, history_covers_duration=True) is False


def test_the_steepest_drop_can_start_inside_the_window_not_only_at_its_edge() -> None:
    """A small rise followed by a real, steep drop must not be diluted by
    comparing only the very first and very last sample -- `window_open_suspected`
    uses the window's own maximum, not its earliest value, as the reference."""
    values = [
        Decimal("20.00"),
        Decimal("20.10"),
        Decimal("20.10") - WINDOW_TEMP_DROP_THRESHOLD_K,
    ]

    assert window_open_suspected(values, history_covers_duration=True) is True


def test_insufficient_history_is_never_suspected_even_with_a_steep_drop() -> None:
    values = [Decimal("21.0"), Decimal("21.0") - WINDOW_TEMP_DROP_THRESHOLD_K - Decimal("2")]

    assert window_open_suspected(values, history_covers_duration=False) is False


@pytest.mark.parametrize("values", [[], [Decimal("21.0")]])
def test_fewer_than_two_samples_is_never_suspected(values: list[Decimal]) -> None:
    assert window_open_suspected(values, history_covers_duration=True) is False


def test_a_custom_threshold_is_honoured() -> None:
    values = [Decimal("21.0"), Decimal("20.5")]

    assert window_open_suspected(
        values, history_covers_duration=True, drop_threshold_k=Decimal("0.4")
    ) is True
    assert window_open_suspected(
        values, history_covers_duration=True, drop_threshold_k=Decimal("0.6")
    ) is False


# --- temperature_detection_still_holding --------------------------------------


def test_a_fresh_detection_still_holds() -> None:
    since = NOW - timedelta(minutes=5)

    assert temperature_detection_still_holding(since, NOW, hold_minutes=30) is True


def test_holding_lapses_once_the_hold_duration_elapses() -> None:
    since = NOW - timedelta(minutes=30)

    assert temperature_detection_still_holding(since, NOW, hold_minutes=30) is False


def test_holding_lapses_boundary_belongs_to_the_expired_side() -> None:
    just_inside = NOW - timedelta(minutes=30) + timedelta(seconds=1)
    exactly_at = NOW - timedelta(minutes=30)

    assert temperature_detection_still_holding(just_inside, NOW, hold_minutes=30) is True
    assert temperature_detection_still_holding(exactly_at, NOW, hold_minutes=30) is False


def test_no_since_never_holds() -> None:
    assert temperature_detection_still_holding(None, NOW, hold_minutes=30) is False


# --- temperature_drop_history_cutoff -------------------------------------------


def test_history_cutoff_subtracts_the_window() -> None:
    assert temperature_drop_history_cutoff(NOW, 15) == NOW - timedelta(minutes=15)
