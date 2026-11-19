"""Tests for the three optional PI rules added on top of section 2's arithmetic:
surplus decay, integral separation and the outdoor feed-forward share.

All three arrive with defaults that reproduce the earlier behaviour bit for bit; the
"defaults" tests below prove that against a verbatim copy of the previous formula.
Whatever happens around the arithmetic (restart, time gap, mode switch, setpoint
jump) is decided by `pi_cycle()`, not by these rules -- those tests prove that the
new parameters change nothing there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thermoctl.domain.pi_control import (
    NEUTRAL_MODULATOR_STATE,
    PiCycleInput,
    PiState,
    pi_arithmetic,
    pi_cycle,
)

KP = Decimal("0.5")
TI = Decimal(90)
DT = Decimal(30)


def _at(hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, 1, hour, minute, second, tzinfo=UTC)


def _arith(
    integral: str,
    error: str,
    **extra: Decimal,
) -> tuple[Decimal, Decimal]:
    return pi_arithmetic(
        integral=Decimal(integral),
        error_k=Decimal(error),
        dt_seconds=DT,
        gain_per_k=KP,
        integral_time_minutes=TI,
        **extra,
    )


def _state(now: datetime, integral: str, context: str = "plan") -> PiState:
    """A running state whose last evaluation was one 30 s cycle ago."""
    return PiState(
        integral=Decimal(integral),
        last_evaluated_at=now - timedelta(seconds=30),
        setpoint_context_key=context,
        modulator=NEUTRAL_MODULATOR_STATE,
        awaiting_boundary_until=None,
        last_reset_reason=None,
    )


def _input(now: datetime, error: str, context: str = "plan", **extra: Decimal) -> PiCycleInput:
    return PiCycleInput(
        now=now,
        error_k=Decimal(error),
        setpoint_context_key=context,
        expected_cycle_seconds=30,
        gain_per_k=KP,
        integral_time_minutes=TI,
        pi_min_on_seconds=180,
        pi_min_off_seconds=60,
        **extra,
    )


def _increment(error: str) -> Decimal:
    return (KP / (TI * 60)) * Decimal(error) * DT


# --------------------------------------------------------------------------- #
# A. Surplus decay: factor on negative integral increments.
# --------------------------------------------------------------------------- #


class TestSurplusDecayFactor:
    def test_factor_one_is_todays_full_unwinding(self) -> None:
        _, integral = _arith("0.5", "-0.4", surplus_decay_factor=Decimal(1))
        assert integral == Decimal("0.5") + _increment("-0.4")

    def test_factor_zero_never_unwinds_at_surplus(self) -> None:
        _, integral = _arith("0.5", "-0.4", surplus_decay_factor=Decimal(0))
        assert integral == Decimal("0.5")

    def test_factor_scales_the_negative_increment(self) -> None:
        _, integral = _arith("0.5", "-0.4", surplus_decay_factor=Decimal("0.2"))
        assert integral == Decimal("0.5") + _increment("-0.4") * Decimal("0.2")

    def test_output_uses_the_scaled_integral(self) -> None:
        u, integral = _arith("0.5", "-0.4", surplus_decay_factor=Decimal("0.2"))
        assert u == KP * Decimal("-0.4") + integral

    def test_factor_does_not_touch_a_positive_error(self) -> None:
        _, integral = _arith("0.5", "0.4", surplus_decay_factor=Decimal(0))
        assert integral == Decimal("0.5") + _increment("0.4")

    def test_factor_does_not_touch_a_zero_error(self) -> None:
        _, integral = _arith("0.5", "0", surplus_decay_factor=Decimal(0))
        assert integral == Decimal("0.5")

    def test_the_low_saturation_lock_at_u_zero_stays_unchanged(self) -> None:
        # u = clamp(0.5 * -2 + 0.4) = 0 and e < 0: I is frozen, whatever the factor.
        for factor in (Decimal(0), Decimal("0.2"), Decimal(1)):
            u, integral = _arith("0.4", "-2", surplus_decay_factor=factor)
            assert (u, integral) == (Decimal(0), Decimal("0.4"))

    def test_a_factor_outside_zero_to_one_is_clamped(self) -> None:
        # A negative factor would turn unwinding into winding up: never allowed.
        _, negative = _arith("0.5", "-0.4", surplus_decay_factor=Decimal(-3))
        assert negative == Decimal("0.5")
        _, large = _arith("0.5", "-0.4", surplus_decay_factor=Decimal(7))
        assert large == Decimal("0.5") + _increment("-0.4")


class TestSurplusDecayThroughPiCycle:
    def test_cycle_input_hands_the_factor_to_the_arithmetic(self) -> None:
        now = _at(12)
        low = pi_cycle(_state(now, "0.5"), _input(now, "-0.4", surplus_decay_factor=Decimal(0)))
        full = pi_cycle(_state(now, "0.5"), _input(now, "-0.4"))
        assert low.state.integral == Decimal("0.5")
        assert full.state.integral == Decimal("0.5") + _increment("-0.4")
