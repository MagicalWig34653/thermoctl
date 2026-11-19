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


# --------------------------------------------------------------------------- #
# B. Integral separation: far below the setpoint the integral is frozen.
# --------------------------------------------------------------------------- #

K = Decimal("0.5")


class TestIntegralSeparation:
    def test_error_just_above_k_freezes_the_integral(self) -> None:
        u, integral = _arith("0.3", "0.51", integral_separation_k=K)
        assert integral == Decimal("0.3")
        assert u == KP * Decimal("0.51") + Decimal("0.3")

    def test_error_exactly_k_still_integrates(self) -> None:
        _, integral = _arith("0.3", "0.5", integral_separation_k=K)
        assert integral == Decimal("0.3") + _increment("0.5")

    def test_error_just_below_k_integrates(self) -> None:
        _, integral = _arith("0.3", "0.49", integral_separation_k=K)
        assert integral == Decimal("0.3") + _increment("0.49")

    def test_zero_switches_the_separation_off(self) -> None:
        _, integral = _arith("0.3", "0.9", integral_separation_k=Decimal(0))
        assert integral == Decimal("0.3") + _increment("0.9")

    def test_a_negative_error_is_never_separated(self) -> None:
        # The sign matters: e > k compares the signed error, so a large surplus is not
        # "far below the setpoint" and unwinds exactly as without the rule.
        _, integral = _arith("0.5", "-3", integral_separation_k=K)
        assert integral == _arith("0.5", "-3")[1]
        _, small = _arith("0.5", "-0.4", integral_separation_k=K)
        assert small == Decimal("0.5") + _increment("-0.4")

    def test_frozen_means_no_unwinding_either(self) -> None:
        # Integral at 1 with a large positive error is frozen by the saturation lock
        # anyway; with a high integral but u < 1 the separation holds it as well.
        _, integral = _arith("0.9", "0.6", integral_separation_k=K)
        assert integral == Decimal("0.9")

    def test_through_pi_cycle(self) -> None:
        now = _at(12)
        held = pi_cycle(_state(now, "0.3"), _input(now, "0.8", integral_separation_k=K))
        free = pi_cycle(_state(now, "0.3"), _input(now, "0.8"))
        assert held.state.integral == Decimal("0.3")
        assert free.state.integral == Decimal("0.3") + _increment("0.8")


# --------------------------------------------------------------------------- #
# C. Feed-forward duty: additive share, anti-windup sees it.
# --------------------------------------------------------------------------- #


class TestFeedforwardDuty:
    def test_it_is_added_to_the_output_not_to_the_integral(self) -> None:
        u, integral = _arith("0.2", "0.1", feedforward_duty=Decimal("0.3"))
        assert integral == Decimal("0.2") + _increment("0.1")
        assert u == KP * Decimal("0.1") + integral + Decimal("0.3")

    def test_output_is_clamped_to_one(self) -> None:
        u, _ = _arith("0.2", "0.1", feedforward_duty=Decimal("0.95"))
        assert u == 1

    def test_output_is_clamped_to_zero(self) -> None:
        u, _ = _arith("0.2", "-1", feedforward_duty=Decimal("0.1"))
        assert u == 0

    def test_high_lock_sees_the_feedforward(self) -> None:
        # Without it u = 0.15 + 0.2 = 0.35 and I rises; with 0.9 u is saturated at 1.
        _, free = _arith("0.2", "0.3")
        assert free > Decimal("0.2")
        u, integral = _arith("0.2", "0.3", feedforward_duty=Decimal("0.9"))
        assert u == 1
        assert integral == Decimal("0.2")

    def test_low_lock_sees_the_feedforward(self) -> None:
        # Without it u = clamp(-0.05) = 0 and I is locked; with 0.2 u = 0.15 > 0 and I
        # may unwind again.
        _, locked = _arith("0", "-0.1")
        assert locked == 0
        _, integral = _arith("0.1", "-0.1", feedforward_duty=Decimal("0.2"))
        assert integral == Decimal("0.1") + _increment("-0.1")
        u_locked, kept = _arith("0.05", "-0.5", feedforward_duty=Decimal("0.1"))
        assert (u_locked, kept) == (Decimal(0), Decimal("0.05"))

    def test_zero_is_todays_behaviour(self) -> None:
        assert _arith("0.2", "0.3", feedforward_duty=Decimal(0)) == _arith("0.2", "0.3")

    def test_through_pi_cycle_the_raw_duty_includes_it(self) -> None:
        now = _at(12)
        out = pi_cycle(_state(now, "0.2"), _input(now, "0.1", feedforward_duty=Decimal("0.3")))
        assert out.feedforward_duty == Decimal("0.3")
        assert out.duty_raw == KP * Decimal("0.1") + out.state.integral + Decimal("0.3")
        plain = pi_cycle(_state(now, "0.2"), _input(now, "0.1"))
        assert plain.feedforward_duty == Decimal(0)

    def test_the_output_reports_zero_where_no_duty_was_computed(self) -> None:
        now = _at(12)
        gap = pi_cycle(
            _state(now - timedelta(hours=1), "0.2"),
            _input(now, "0.1", feedforward_duty=Decimal("0.3")),
        )
        assert gap.duty_raw is None
        assert gap.feedforward_duty == Decimal(0)
        awaiting = PiState(
            integral=Decimal("0.2"),
            last_evaluated_at=now - timedelta(seconds=30),
            setpoint_context_key="plan",
            modulator=NEUTRAL_MODULATOR_STATE,
            awaiting_boundary_until=now + timedelta(minutes=5),
            last_reset_reason="scharfschaltung",
        )
        waiting = pi_cycle(awaiting, _input(now, "0.1", feedforward_duty=Decimal("0.3")))
        assert waiting.duty_raw is None
        assert waiting.feedforward_duty == Decimal(0)
