from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.fault import (
    NO_SOURCE,
    OK,
    STUCK_READING_SPAN_C,
    VERALTET,
    sensor_state,
    state_row,
    stuck_reading,
)

NOW = datetime(2026, 8, 29, 12, 0)


def test_sensor_state_without_a_measurement_has_no_source() -> None:
    assert sensor_state(None, NOW, 300) == NO_SOURCE


@pytest.mark.parametrize(
    ("age_s", "expected"),
    [(299, OK), (300, OK), (301, VERALTET)],
)
def test_sensor_state_timeout_boundary_belongs_to_the_valid_range(
    age_s: int, expected: str
) -> None:
    measurement = NOW - timedelta(seconds=age_s)

    assert sensor_state(measurement, NOW, 300) == expected


def test_sensor_state_measurement_from_the_future_is_ok() -> None:
    measurement = NOW + timedelta(seconds=10)

    assert sensor_state(measurement, NOW, 300) == OK


@pytest.mark.parametrize(
    ("age_s", "expected"),
    [(0, OK), (1, VERALTET)],
)
def test_sensor_state_timeout_zero_keeps_only_the_current_reading(
    age_s: int, expected: str
) -> None:
    measurement = NOW - timedelta(seconds=age_s)

    assert sensor_state(measurement, NOW, 0) == expected


def test_state_row_contains_a_concrete_duration_and_assessment() -> None:
    measurement = NOW - timedelta(hours=3, minutes=12)

    row = state_row(VERALTET, measurement, NOW)

    assert "3 Stunden 12 Minuten" in row
    assert "ausgefallen" in row


def test_state_row_names_a_missing_measurement() -> None:
    assert "Noch nie" in state_row(NO_SOURCE, None, NOW)


def test_state_row_describes_a_future_measurement() -> None:
    measurement = NOW + timedelta(seconds=10)

    assert "in 10 Sekunden" in state_row(OK, measurement, NOW)


def test_state_row_rejects_an_unknown_state() -> None:
    with pytest.raises(ValueError, match="Unbekannter Sensorzustand"):
        state_row("unbekannt", NOW, NOW)


def test_state_row_requires_a_measurement_time_for_a_known_state() -> None:
    with pytest.raises(ValueError, match="erfordert einen Messzeitpunkt"):
        state_row(OK, None, NOW)


# --- stuck_reading -----------------------------------------------------------


def test_a_value_that_never_moves_at_all_is_stuck() -> None:
    values = [Decimal("21.30")] * 5

    assert stuck_reading(values, history_covers_duration=True) is True


def test_a_sensor_oscillating_between_two_resolution_steps_is_not_stuck() -> None:
    """22.7 / 22.8 °C is exactly the motivating case in the task: a sensor with 0.1 K
    resolution that legitimately toggles between two adjacent raw values while the
    room itself holds almost perfectly still must not be flagged."""
    values = [Decimal("22.7"), Decimal("22.8"), Decimal("22.7"), Decimal("22.8")]

    assert stuck_reading(values, history_covers_duration=True) is False


def test_the_span_boundary_belongs_to_the_stuck_side() -> None:
    low = Decimal("20.00")
    at_threshold = [low, low + STUCK_READING_SPAN_C]
    just_over = [low, low + STUCK_READING_SPAN_C + Decimal("0.01")]

    assert stuck_reading(at_threshold, history_covers_duration=True) is True
    assert stuck_reading(just_over, history_covers_duration=True) is False


def test_insufficient_history_is_never_stuck_even_if_every_sample_is_equal() -> None:
    """A zone whose source has only just been assigned has not 'held still for
    twelve hours' -- it has simply never been asked the question yet."""
    values = [Decimal("21.30")] * 5

    assert stuck_reading(values, history_covers_duration=False) is False


@pytest.mark.parametrize("values", [[], [Decimal("21.30")]])
def test_fewer_than_two_samples_is_never_stuck(values: list[Decimal]) -> None:
    assert stuck_reading(values, history_covers_duration=True) is False
