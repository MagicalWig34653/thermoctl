from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.window_temperature_drop import (
    WINDOW_TEMP_DROP_THRESHOLD_K,
    temperature_detection_cap_exceeded,
    temperature_detection_still_holding,
    temperature_detection_still_silenced,
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


def test_a_steep_drop_starting_mid_window_is_still_caught() -> None:
    """The room holds at a plateau for a while, then falls steeply -- the
    reference (the median of every reading before the current one) must reflect
    the recent plateau, not get pulled down by an unrelated, much older, lower
    reading earlier in the same window."""
    values = [
        Decimal("19.00"),  # an old, unrelated low reading early in the window
        Decimal("20.10"),
        Decimal("20.10"),
        Decimal("20.10"),
        Decimal("20.10") - WINDOW_TEMP_DROP_THRESHOLD_K,
    ]

    assert window_open_suspected(values, history_covers_duration=True) is True


def test_a_single_noisy_high_outlier_does_not_fake_a_drop() -> None:
    """Cross-review finding: a plain `max()` reference lets one spurious high
    reading -- a Zigbee reporting glitch, a brief radio dropout -- fake a steep
    drop on its own. The room here never really moves outside +/-0.1 K except
    for that single outlier; the median-based reference must not be fooled by
    it the way a bare maximum would be."""
    values = [
        Decimal("20.90"),
        Decimal("21.00"),
        Decimal("20.90") + WINDOW_TEMP_DROP_THRESHOLD_K + Decimal("1"),  # the outlier
        Decimal("20.95"),
        Decimal("21.00"),  # current reading, unremarkable
    ]

    assert window_open_suspected(values, history_covers_duration=True) is False


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


# --- temperature_detection_cap_exceeded ----------------------------------------


def test_a_short_streak_never_exceeds_the_cap() -> None:
    since = NOW - timedelta(minutes=5)

    assert temperature_detection_cap_exceeded(since, NOW, max_suspected_minutes=90) is False


def test_the_cap_boundary_belongs_to_the_exceeded_side() -> None:
    just_under = NOW - timedelta(minutes=90) + timedelta(seconds=1)
    exactly_at = NOW - timedelta(minutes=90)

    assert temperature_detection_cap_exceeded(just_under, NOW, max_suspected_minutes=90) is False
    assert temperature_detection_cap_exceeded(exactly_at, NOW, max_suspected_minutes=90) is True


def test_no_since_never_exceeds_the_cap() -> None:
    assert temperature_detection_cap_exceeded(None, NOW, max_suspected_minutes=90) is False


# --- temperature_detection_still_silenced --------------------------------------


def test_a_fresh_silence_still_applies() -> None:
    silence_until = NOW + timedelta(minutes=59)

    assert temperature_detection_still_silenced(silence_until, NOW) is True


def test_silence_lapses_once_its_deadline_passes() -> None:
    silence_until = NOW - timedelta(seconds=1)

    assert temperature_detection_still_silenced(silence_until, NOW) is False


def test_silence_boundary_belongs_to_the_lapsed_side() -> None:
    assert temperature_detection_still_silenced(NOW, NOW) is False


def test_no_deadline_is_never_silenced() -> None:
    assert temperature_detection_still_silenced(None, NOW) is False


# --- temperature_drop_history_cutoff -------------------------------------------


def test_history_cutoff_subtracts_the_window() -> None:
    assert temperature_drop_history_cutoff(NOW, 15) == NOW - timedelta(minutes=15)
