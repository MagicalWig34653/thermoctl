"""Tests for `thermoctl.domain.pi_feedforward` (outdoor smoothing and duty share)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.pi_feedforward import (
    FEEDFORWARD_GAIN_MAX,
    FEEDFORWARD_GAIN_MIN,
    FEEDFORWARD_OUTDOOR_TIMEOUT,
    feedforward_duty,
    smoothed_outdoor,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


def _d(value: str) -> Decimal:
    return Decimal(value)


class TestConstants:
    def test_values_follow_the_task(self) -> None:
        assert FEEDFORWARD_OUTDOOR_TIMEOUT == timedelta(hours=3)
        assert FEEDFORWARD_GAIN_MIN == Decimal("0.005")
        assert FEEDFORWARD_GAIN_MAX == Decimal("0.05")


class TestSmoothedOutdoor:
    def test_time_weighted_not_count_weighted(self) -> None:
        # 4 degrees for three hours, then 8 degrees for one hour: (4*3 + 8*1) / 4 = 5.
        # Counted by samples the mean would be 6.
        samples = [(_ago(hours=4), _d("4")), (_ago(hours=1), _d("8"))]
        assert smoothed_outdoor(samples, NOW, 4) == _d("5")

    def test_unsorted_input_gives_the_same_result(self) -> None:
        samples = [(_ago(hours=1), _d("8")), (_ago(hours=4), _d("4"))]
        assert smoothed_outdoor(samples, NOW, 4) == _d("5")

    def test_last_value_holds_until_now(self) -> None:
        assert smoothed_outdoor([(_ago(minutes=30), _d("7"))], NOW, 2) == _d("7")

    def test_value_before_the_window_carries_into_it(self) -> None:
        # 10 degrees from long ago until 1 h ago, 6 degrees since: window of 2 h
        # -> (10*1 + 6*1) / 2 = 8.
        samples = [(_ago(hours=5), _d("10")), (_ago(hours=1), _d("6"))]
        assert smoothed_outdoor(samples, NOW, 2) == _d("8")

    def test_only_the_newest_sample_before_the_window_carries_in(self) -> None:
        samples = [
            (_ago(hours=9), _d("0")),
            (_ago(hours=5), _d("10")),
            (_ago(hours=1), _d("6")),
        ]
        assert smoothed_outdoor(samples, NOW, 2) == _d("8")

    def test_invalid_values_are_skipped_and_the_previous_value_keeps_holding(self) -> None:
        samples = [
            (_ago(hours=2), _d("4")),
            (_ago(hours=1, minutes=30), None),
            (_ago(hours=1), _d("8")),
        ]
        # 4 for one hour, 8 for one hour.
        assert smoothed_outdoor(samples, NOW, 2) == _d("6")

    def test_a_trailing_invalid_value_falls_back_to_the_last_valid_one(self) -> None:
        samples = [(_ago(hours=1), _d("5")), (_ago(minutes=1), None)]
        assert smoothed_outdoor(samples, NOW, 2) == _d("5")

    def test_samples_in_the_future_are_ignored(self) -> None:
        samples = [(_ago(hours=1), _d("5")), (NOW + timedelta(minutes=5), _d("30"))]
        assert smoothed_outdoor(samples, NOW, 2) == _d("5")

    def test_sample_exactly_at_window_start_is_inside(self) -> None:
        samples = [(_ago(hours=2), _d("4")), (_ago(hours=1), _d("8"))]
        assert smoothed_outdoor(samples, NOW, 2) == _d("6")

    def test_sample_exactly_now_without_duration_is_returned_when_alone(self) -> None:
        assert smoothed_outdoor([(NOW, _d("3"))], NOW, 2) == _d("3")

    def test_zero_length_last_segment_adds_no_weight(self) -> None:
        samples = [(_ago(hours=2), _d("4")), (NOW, _d("30"))]
        assert smoothed_outdoor(samples, NOW, 2) == _d("4")

    def test_empty_window_uses_the_last_valid_value_within_the_timeout(self) -> None:
        # Window 1 h, last sample 2 h ago: nothing inside, but only 2 h old.
        assert smoothed_outdoor([(_ago(hours=2), _d("5"))], NOW, 1) == _d("5")

    def test_the_timeout_boundary_itself_still_counts(self) -> None:
        assert smoothed_outdoor([(_ago(hours=3), _d("5"))], NOW, 1) == _d("5")

    def test_older_than_the_timeout_is_none(self) -> None:
        samples = [(_ago(hours=3, seconds=1), _d("5"))]
        assert smoothed_outdoor(samples, NOW, 1) is None

    def test_empty_inputs_are_none(self) -> None:
        assert smoothed_outdoor([], NOW, 3) is None
        assert smoothed_outdoor([(_ago(minutes=5), None)], NOW, 3) is None

    def test_only_future_samples_are_none(self) -> None:
        assert smoothed_outdoor([(NOW + timedelta(minutes=1), _d("5"))], NOW, 3) is None

    def test_a_smoothing_window_that_is_not_positive_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="smoothing_hours"):
            smoothed_outdoor([(_ago(minutes=5), _d("5"))], NOW, 0)


def _duty(
    *,
    enabled: bool = True,
    gain: str = "0.02",
    setpoint: str = "20",
    outdoor: str | None = "5",
) -> Decimal:
    return feedforward_duty(
        enabled=enabled,
        gain=_d(gain),
        setpoint_c=_d(setpoint),
        outdoor_smoothed_c=None if outdoor is None else _d(outdoor),
    )


class TestFeedforwardDuty:
    def test_formula(self) -> None:
        assert _duty() == _d("0.02") * 15

    def test_disabled_is_zero(self) -> None:
        assert _duty(enabled=False) == 0

    def test_unknown_outdoor_is_zero(self) -> None:
        assert _duty(outdoor=None) == 0

    def test_warmer_outside_than_setpoint_is_zero_not_negative(self) -> None:
        assert _duty(outdoor="25") == 0

    def test_equal_is_zero(self) -> None:
        assert _duty(outdoor="20") == 0

    def test_clamped_to_one(self) -> None:
        assert _duty(outdoor="-40") == 1

    def test_exactly_one_is_kept(self) -> None:
        assert _duty(gain="0.05", setpoint="20", outdoor="0") == 1
