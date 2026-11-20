"""Timeout der Außentemperatur-Vorsteuerung und Grenzfälle der Mittelung.

Der NEUESTE gültige Wert entscheidet über das Timeout (3 h), unabhängig von der
Glättungsdauer: Ist er älter, gibt es keinen Außenwert, auch wenn ältere Werte noch im
Glättungsfenster liegen würden.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.pi_feedforward import FEEDFORWARD_OUTDOOR_TIMEOUT, smoothed_outdoor

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
SECOND = timedelta(seconds=1)


def _d(value: str | int) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize("hours", [3, 6])
class TestNewestValueDecidesTheTimeout:
    def test_three_hours_minus_a_second_is_valid(self, hours: int) -> None:
        samples = [(NOW - FEEDFORWARD_OUTDOOR_TIMEOUT + SECOND, _d(5))]
        assert smoothed_outdoor(samples, NOW, hours) == _d(5)

    def test_exactly_three_hours_is_valid(self, hours: int) -> None:
        assert smoothed_outdoor([(NOW - FEEDFORWARD_OUTDOOR_TIMEOUT, _d(5))], NOW, hours) == _d(5)

    def test_three_hours_plus_a_second_is_none(self, hours: int) -> None:
        samples = [(NOW - FEEDFORWARD_OUTDOOR_TIMEOUT - SECOND, _d(5))]
        assert smoothed_outdoor(samples, NOW, hours) is None

    def test_a_single_value_four_hours_old_is_none(self, hours: int) -> None:
        assert smoothed_outdoor([(NOW - 4 * HOUR, _d(5))], NOW, hours) is None

    def test_four_hours_old_followed_only_by_invalid_readings_is_none(self, hours: int) -> None:
        samples = [(NOW - 4 * HOUR, _d(5)), (NOW - 1 * HOUR, None), (NOW - 10 * SECOND, None)]
        assert smoothed_outdoor(samples, NOW, hours) is None

    def test_older_values_inside_the_window_do_not_revive_a_stale_newest_one(
        self, hours: int
    ) -> None:
        # Fünf und vier Stunden alt liegen bei sechs Stunden Glättung im Fenster, der
        # neueste ist aber älter als das Timeout: kein Außenwert.
        samples = [(NOW - 5 * HOUR, _d(0)), (NOW - 4 * HOUR, _d(6))]
        assert smoothed_outdoor(samples, NOW, hours) is None


def test_a_fresh_newest_value_keeps_the_average_over_the_older_ones() -> None:
    # 0 Grad von vor 5 h bis vor 2 h (3 h), 6 Grad seit vor 2 h (2 h): (0*3 + 6*2) / 5 = 2,4.
    samples = [(NOW - 5 * HOUR, _d(0)), (NOW - 2 * HOUR, _d(6))]
    assert smoothed_outdoor(samples, NOW, 6) == _d("2.4")


class TestAveragingBoundaries:
    def test_input_is_sorted_by_time_not_by_value(self) -> None:
        # Zeitlich: 9 Grad (1 h), 1 Grad (2 h), 5 Grad (1 h) -> 16 / 4 = 4.
        # Nach Wert sortiert käme eine andere Reihenfolge und ein anderes Mittel heraus.
        samples = [
            (NOW - 1 * HOUR, _d(5)),
            (NOW - 4 * HOUR, _d(9)),
            (NOW - 3 * HOUR, _d(1)),
        ]
        assert smoothed_outdoor(samples, NOW, 4) == _d(4)

    def test_only_the_newest_of_several_old_values_carries_into_the_window(self) -> None:
        # Fenster 2 h; davor 0, 10 und 20 Grad (zuletzt 3 h her): 20 gilt ab Fensterbeginn.
        samples = [
            (NOW - 9 * HOUR, _d(0)),
            (NOW - 5 * HOUR, _d(10)),
            (NOW - 3 * HOUR, _d(20)),
            (NOW - 1 * HOUR, _d(6)),
        ]
        assert smoothed_outdoor(samples, NOW, 2) == _d(13)  # (20*1 + 6*1) / 2

    def test_three_values_inside_the_window_are_weighted_by_their_own_duration(self) -> None:
        # 2 Grad 2 h, 4 Grad 2 h, 10 Grad 1 h: (4 + 8 + 10) / 5 = 4,4.
        samples = [
            (NOW - 5 * HOUR, _d(2)),
            (NOW - 3 * HOUR, _d(4)),
            (NOW - 1 * HOUR, _d(10)),
        ]
        assert smoothed_outdoor(samples, NOW, 6) == _d("4.4")

    def test_the_mean_is_exact_and_not_an_integer_division(self) -> None:
        # 1 Grad 1 h, 2 Grad 2 h: 5 / 3.
        samples = [(NOW - 3 * HOUR, _d(1)), (NOW - 2 * HOUR, _d(2))]
        assert smoothed_outdoor(samples, NOW, 3) == _d(5) / _d(3)

    def test_two_samples_exactly_at_now_use_the_newest_one(self) -> None:
        samples = [(NOW, _d(3)), (NOW, _d(7))]
        assert smoothed_outdoor(samples, NOW, 3) == _d(7)

    def test_a_single_sample_exactly_at_now_is_that_value(self) -> None:
        assert smoothed_outdoor([(NOW, _d(3))], NOW, 3) == _d(3)

    def test_a_sample_at_now_after_an_older_one_has_no_weight(self) -> None:
        samples = [(NOW - 1 * HOUR, _d(5)), (NOW, _d(9))]
        assert smoothed_outdoor(samples, NOW, 1) == _d(5)
