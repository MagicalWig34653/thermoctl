"""Tests for the pure PI and window functions (`thermoctl.domain.pi_control`).

Specification: `docs/superpowers/specs/2026-09-02-pi-regelung-spezifikation.md`,
sections 1-4 and 9. This module is **not** wired to `control_loop.decide()` yet
(build order step 3, section 11) -- these tests only prove the pure functions
themselves, grouped exactly as section 9 asks for:

1. table tests for the PI arithmetic (`pi_arithmetic`, `pi_dt`),
2. sequence and property tests for the window modulator (`window_modulate`),
3. a precedence table for section 4's seven anti-windup rows, each with its own
   multi-cycle windup story,
4. eligibility tests (`pi_eligible`),
5. integration tests for the two orchestrating functions (`pi_cycle`,
   `reset_pi_state`) that step 4 will eventually call from `shadow_run.py`.

`random.Random` with a fixed seed stands in for a property-testing library here --
the project has none installed, and a fixed seed keeps failures reproducible.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.pi_control import (
    INTEGRATOR_CONTINUE,
    INTEGRATOR_HOLD,
    INTEGRATOR_RESET,
    MAX_CONTROL_CYCLE_SECONDS,
    MODULATOR_REASON_HELD,
    MODULATOR_REASON_REGULAR,
    MODULATOR_REASON_TASTGRAD_VORRANG,
    NEUTRAL_MODULATOR_STATE,
    NEUTRAL_PI_STATE,
    REMAINDER_LIMIT_S,
    RESET_REASON_ARMING,
    RESET_REASON_CONTEXT_CHANGE,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_TIME_GAP,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
    WINDOW_SECONDS,
    ActuatorProfile,
    ModulatorResult,
    ModulatorState,
    PiCycleInput,
    PiState,
    pi_arithmetic,
    pi_cycle,
    pi_dt,
    pi_eligible,
    reset_pi_state,
    window_modulate,
    window_start_for,
)


def _at(hour: int = 0, minute: int = 0, second: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 1. PI arithmetic -- table tests.
# --------------------------------------------------------------------------- #


class TestPiDt:
    def test_missing_previous_state_locks_out_integration(self) -> None:
        assert pi_dt(previous_evaluated_at=None, now=_at(), expected_cycle_seconds=60) is None

    def test_zero_or_negative_dt_locks_out_integration(self) -> None:
        now = _at(minute=1)
        assert (
            pi_dt(previous_evaluated_at=now, now=now, expected_cycle_seconds=60) is None
        )
        earlier = now - timedelta(seconds=5)
        assert pi_dt(previous_evaluated_at=now, now=earlier, expected_cycle_seconds=60) is None

    def test_a_gap_of_exactly_two_cycles_is_still_integrated(self) -> None:
        previous = _at()
        now = previous + timedelta(seconds=120)
        assert pi_dt(previous_evaluated_at=previous, now=now, expected_cycle_seconds=60) == 120

    def test_a_gap_beyond_two_cycles_locks_out_integration(self) -> None:
        previous = _at()
        now = previous + timedelta(seconds=121)
        assert pi_dt(previous_evaluated_at=previous, now=now, expected_cycle_seconds=60) is None

    def test_regular_single_cycle_gap(self) -> None:
        previous = _at()
        now = previous + timedelta(seconds=60)
        assert pi_dt(previous_evaluated_at=previous, now=now, expected_cycle_seconds=60) == 60


class TestPiArithmetic:
    KP = Decimal("0.25")
    TI = Decimal(180)

    def test_normal_step_matches_the_formula(self) -> None:
        u, integral = pi_arithmetic(
            integral=Decimal("0.2"),
            error_k=Decimal("0.5"),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        increment = (self.KP / (self.TI * 60)) * Decimal("0.5") * Decimal(60)
        expected_integral = Decimal("0.2") + increment
        assert integral == expected_integral
        assert u == self.KP * Decimal("0.5") + expected_integral

    def test_negative_error_integrates_downward(self) -> None:
        _, integral = pi_arithmetic(
            integral=Decimal("0.5"),
            error_k=Decimal("-1"),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert integral < Decimal("0.5")

    def test_output_never_leaves_zero_to_one_high_side(self) -> None:
        u, integral = pi_arithmetic(
            integral=Decimal(1),
            error_k=Decimal(10),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert u == 1
        assert integral == 1

    def test_output_never_leaves_zero_to_one_low_side(self) -> None:
        u, integral = pi_arithmetic(
            integral=Decimal(0),
            error_k=Decimal(-10),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert u == 0
        assert integral == 0

    def test_windup_guard_freezes_integral_at_high_saturation_with_positive_error(self) -> None:
        """Anti-windup bullet 1 (section 2): stuck at u=1 while error still positive."""
        _, integral = pi_arithmetic(
            integral=Decimal(1),
            error_k=Decimal(2),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert integral == 1

    def test_windup_guard_freezes_integral_at_low_saturation_with_negative_error(self) -> None:
        _, integral = pi_arithmetic(
            integral=Decimal(0),
            error_k=Decimal(-2),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert integral == 0

    def test_windup_guard_allows_unwinding_from_high_saturation(self) -> None:
        """u=1, but the error has flipped negative -- I must be free to fall again."""
        _, integral = pi_arithmetic(
            integral=Decimal(1),
            error_k=Decimal("-0.1"),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert integral < 1

    def test_windup_guard_allows_unwinding_from_low_saturation(self) -> None:
        _, integral = pi_arithmetic(
            integral=Decimal(0),
            error_k=Decimal("0.1"),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert integral > 0

    def test_exactly_on_the_saturation_boundary_with_zero_error_still_integrates(self) -> None:
        """u == 1 but error == 0 is neither 'positive error' nor 'negative error' --
        the guard must not misfire and freeze a legitimate zero-error step."""
        u, integral = pi_arithmetic(
            integral=Decimal(1),
            error_k=Decimal(0),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert u == 1
        assert integral == 1

    def test_decimal_rounding_does_not_raise_and_stays_bounded(self) -> None:
        u, integral = pi_arithmetic(
            integral=Decimal("0.333333333333333333"),
            error_k=Decimal(1) / Decimal(3),
            dt_seconds=Decimal(60),
            gain_per_k=self.KP,
            integral_time_minutes=self.TI,
        )
        assert Decimal(0) <= u <= Decimal(1)
        assert Decimal(0) <= integral <= Decimal(1)

    def test_zero_gain_never_moves_the_output(self) -> None:
        u, integral = pi_arithmetic(
            integral=Decimal("0.5"),
            error_k=Decimal(5),
            dt_seconds=Decimal(60),
            gain_per_k=Decimal(0),
            integral_time_minutes=self.TI,
        )
        assert u == Decimal("0.5")
        assert integral == Decimal("0.5")


# --------------------------------------------------------------------------- #
# 2. Window modulator -- sequence and property tests.
# --------------------------------------------------------------------------- #


def _run_window(
    duty: str,
    *,
    pi_min_on: int,
    pi_min_off: int,
    cycles: int,
    dt: int = 60,
    start: datetime | None = None,
) -> list[ModulatorResult]:
    state = NEUTRAL_MODULATOR_STATE
    now = start or _at()
    results: list[ModulatorResult] = []
    for _ in range(cycles):
        result = window_modulate(
            state,
            now=now,
            u_raw=Decimal(duty),
            dt_seconds=Decimal(dt),
            pi_min_on_seconds=pi_min_on,
            pi_min_off_seconds=pi_min_off,
        )
        results.append(result)
        state = result.state
        now += timedelta(seconds=dt)
    return results


class TestWindowModulateValidation:
    def test_rejects_duty_outside_zero_to_one(self) -> None:
        with pytest.raises(ValueError):
            window_modulate(
                NEUTRAL_MODULATOR_STATE,
                now=_at(),
                u_raw=Decimal("1.5"),
                dt_seconds=Decimal(60),
                pi_min_on_seconds=60,
                pi_min_off_seconds=60,
            )

    def test_rejects_non_positive_dt(self) -> None:
        with pytest.raises(ValueError):
            window_modulate(
                NEUTRAL_MODULATOR_STATE,
                now=_at(),
                u_raw=Decimal("0.5"),
                dt_seconds=Decimal(0),
                pi_min_on_seconds=60,
                pi_min_off_seconds=60,
            )

    def test_window_start_for_requires_timezone_aware_datetime(self) -> None:
        with pytest.raises(ValueError):
            window_start_for(datetime(2026, 9, 1, 0, 0))

    def test_window_start_for_floors_to_the_utc_quarter_hour(self) -> None:
        assert window_start_for(_at(hour=3, minute=37, second=42)) == _at(hour=3, minute=30)
        assert window_start_for(_at(hour=3, minute=45)) == _at(hour=3, minute=45)

    def test_window_start_is_utc_regardless_of_a_local_dst_transition(self) -> None:
        """The window is fixed to UTC quarter hours (section 3) -- a local
        Europe/Berlin summer-time jump must not shift it, because nothing here reads
        a local calendar at all."""
        before = datetime(2026, 3, 29, 0, 37, tzinfo=UTC)
        after = datetime(2026, 3, 29, 1, 37, tzinfo=UTC)
        assert window_start_for(before) == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
        assert window_start_for(after) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


class TestWindowModulateEdgeDuties:
    def test_duty_zero_stays_off_forever(self) -> None:
        results = _run_window("0", pi_min_on=60, pi_min_off=60, cycles=20)
        assert all(not r.on for r in results)
        assert all(r.state.remainder_s == 0 for r in results)

    def test_duty_one_stays_on_forever(self) -> None:
        results = _run_window("1", pi_min_on=60, pi_min_off=60, cycles=20)
        assert all(r.on for r in results)
        assert all(r.state.remainder_s == 0 for r in results)

    def test_duty_one_does_not_flip_off_once_remainder_settles_at_zero(self) -> None:
        """Regression: a naive 'remainder <= 0 wins off' tie-break would flip a
        fully-on zone back off the moment its remainder first reaches exactly 0,
        even though the frozen duty is still 1."""
        results = _run_window("1", pi_min_on=60, pi_min_off=60, cycles=3)
        assert [r.on for r in results] == [True, True, True]

    def test_duty_shorter_than_pi_min_on_still_produces_a_single_minimum_pulse(self) -> None:
        """A duty far below what pi_min_on_seconds alone would suggest (u=0.01, so
        the 'natural' on-time would be under a second) still switches on for at
        least one full pi_min_on_seconds pulse -- the modulator holds until the
        minimum is met, not until the naive on-time is met."""
        results = _run_window("0.01", pi_min_on=60, pi_min_off=60, cycles=200, dt=60)
        on_runs = _runs(results)
        on_lengths = [length for on, length in on_runs if on]
        assert on_lengths, "duty > 0 must eventually switch on"
        assert all(length >= 1 for length in on_lengths)

    def test_off_portion_shorter_than_pi_min_off_still_respects_the_minimum(self) -> None:
        """u=0.99 -- the naive off-time would be under a second; the modulator must
        still hold off for at least one pi_min_off_seconds pulse before switching
        back on."""
        results = _run_window("0.99", pi_min_on=60, pi_min_off=60, cycles=200, dt=60)
        off_runs = [length for on, length in _runs(results) if not on]
        assert off_runs, "duty < 1 must eventually switch off"
        assert all(length >= 1 for length in off_runs)


def _runs(results: list[ModulatorResult]) -> list[tuple[bool, int]]:
    """Collapses a sequence of per-cycle `on` values into (state, cycle-count) runs."""
    runs: list[tuple[bool, int]] = []
    for result in results:
        if runs and runs[-1][0] == result.on:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((result.on, 1))
    return runs


class TestWindowModulateSteadyDuty:
    @pytest.mark.parametrize("duty", ["0.1", "0.25", "0.5", "0.75", "0.9"])
    def test_long_run_average_converges_to_the_frozen_duty(self, duty: str) -> None:
        cycles = 2000
        results = _run_window(duty, pi_min_on=60, pi_min_off=60, cycles=cycles, dt=60)
        # Skip the first few cycles -- the modulator needs to leave its initial
        # (fresh, off) state before the long-run average is meaningful.
        settled = results[200:]
        share_on = sum(1 for r in settled if r.on) / len(settled)
        assert abs(share_on - float(duty)) < Decimal("0.02")

    def test_every_regular_pulse_respects_the_pi_minimum_durations(self) -> None:
        results = _run_window("0.3", pi_min_on=120, pi_min_off=180, cycles=500, dt=60)
        runs = _runs(results)
        # Reconstruct, for each run, whether any cycle within it carried the
        # tastgrad-vorrang tag -- an underrun is only ever acceptable tagged that way.
        index = 0
        for on, length in runs:
            duration = length * 60
            tags = {results[i].reason_code for i in range(index, index + length)}
            required = 120 if on else 180
            if duration < required:
                assert MODULATOR_REASON_TASTGRAD_VORRANG in tags
            index += length

    def test_never_more_than_sixty_regular_switches_per_hour_at_a_sixty_second_cycle(
        self,
    ) -> None:
        """Invariant from section 9.3, with the discarded ones explicitly not
        tested: not 'never too short', not 'at most two switches per window'."""
        results = _run_window("0.5", pi_min_on=60, pi_min_off=60, cycles=60, dt=60)
        regular_switches = sum(
            1 for r in results if r.switched and r.reason_code == MODULATOR_REASON_REGULAR
        )
        assert regular_switches <= 60

    def test_tie_break_at_zero_remainder_favours_off(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=100,
            remainder_s=Decimal(0),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.on is False


class TestWindowModulateRemainder:
    def test_remainder_stays_within_its_bound_for_many_random_cycles(self) -> None:
        rng = random.Random(20260902)  # noqa: S311 -- reproducible test fixture, not crypto
        state = NEUTRAL_MODULATOR_STATE
        now = _at()
        for _ in range(3000):
            duty = Decimal(rng.randint(0, 100)) / Decimal(100)
            dt = Decimal(rng.choice([30, 60, 60, 60, 120]))
            pi_min_on = rng.choice([60, 90, 120, 300])
            pi_min_off = rng.choice([60, 90, 120, 300])
            result = window_modulate(
                state,
                now=now,
                u_raw=duty,
                dt_seconds=dt,
                pi_min_on_seconds=pi_min_on,
                pi_min_off_seconds=pi_min_off,
            )
            assert -REMAINDER_LIMIT_S <= result.state.remainder_s <= REMAINDER_LIMIT_S
            state = result.state
            now += timedelta(seconds=int(dt))

    def test_a_positive_remainder_favours_the_next_period_switching_on(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=100,
            remainder_s=Decimal(5),
            window_start=_at(),
            frozen_duty=Decimal("0.4"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.4"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.on is True

    def test_a_negative_remainder_favours_the_next_period_staying_off(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=100,
            remainder_s=Decimal(-5),
            window_start=_at(),
            frozen_duty=Decimal("0.4"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.4"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.on is False

    def test_missed_cycles_are_reflected_in_a_larger_dt(self) -> None:
        """A cycle that ran late (e.g. after a restart) still only counts its
        actually-decided time -- section 3: 'die tatsächlich entschiedene Ein-Zeit
        zählt'. A single 300s-late decision behaves like one big dt, not five."""
        state = ModulatorState(
            on=False,
            held_for_s=600,
            remainder_s=Decimal(0),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=5),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(300),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.state.remainder_s == Decimal(150)

    def test_restart_with_unknown_held_duration_is_not_blocked(self) -> None:
        """held_for_s=None (freshly restarted, no history) must not be treated as
        'zero seconds held, therefore blocked' -- mirrors control_loop.py's rule 5
        convention for the same situation."""
        state = ModulatorState(
            on=False,
            held_for_s=None,
            remainder_s=Decimal(50),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.on is True
        assert result.reason_code == MODULATOR_REASON_REGULAR

    def test_a_window_boundary_duty_change_can_force_an_early_switch(self) -> None:
        """section 3: a duty change at a window boundary can collide with a still
        running PI minimum duration -- the duty wins, tagged pi_tastgrad_vorrang."""
        state = ModulatorState(
            on=True,
            held_for_s=10,
            remainder_s=Decimal(0),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=15),
            u_raw=Decimal("0"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.on is False
        assert result.switched is True
        assert result.reason_code == MODULATOR_REASON_TASTGRAD_VORRANG
        assert result.integrator_action == INTEGRATOR_CONTINUE

    def test_rounding_that_would_overflow_the_remainder_bound_forces_a_switch(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=10,
            remainder_s=Decimal(897),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.9"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.on is True
        assert result.reason_code == MODULATOR_REASON_TASTGRAD_VORRANG
        assert result.state.remainder_s <= REMAINDER_LIMIT_S

    def test_held_below_minimum_without_a_collision_simply_holds(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=10,
            remainder_s=Decimal(5),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.on is False
        assert result.switched is False
        assert result.reason_code == MODULATOR_REASON_HELD
        assert result.integrator_action == INTEGRATOR_HOLD


class TestPiEligible:
    SWITCH_ONLY = ActuatorProfile(
        self_regulating=False, has_switch_capability=True, has_thermostat_capability=False
    )

    def test_no_actuators_is_not_eligible(self) -> None:
        result = pi_eligible(
            [], control_cycle_seconds=60, pi_min_on_seconds=60, pi_min_off_seconds=60
        )
        assert result.eligible is False

    def test_a_self_regulating_actuator_is_not_eligible(self) -> None:
        actuator = ActuatorProfile(
            self_regulating=True, has_switch_capability=True, has_thermostat_capability=False
        )
        result = pi_eligible(
            [self.SWITCH_ONLY, actuator],
            control_cycle_seconds=60,
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.eligible is False

    def test_a_thermostat_capable_actuator_is_not_eligible_even_if_not_self_regulating(
        self,
    ) -> None:
        actuator = ActuatorProfile(
            self_regulating=False, has_switch_capability=True, has_thermostat_capability=True
        )
        result = pi_eligible(
            [actuator], control_cycle_seconds=60, pi_min_on_seconds=60, pi_min_off_seconds=60
        )
        assert result.eligible is False

    def test_an_actuator_without_switch_capability_is_not_eligible(self) -> None:
        actuator = ActuatorProfile(
            self_regulating=False, has_switch_capability=False, has_thermostat_capability=False
        )
        result = pi_eligible(
            [actuator], control_cycle_seconds=60, pi_min_on_seconds=60, pi_min_off_seconds=60
        )
        assert result.eligible is False

    def test_non_positive_control_cycle_is_not_eligible(self) -> None:
        result = pi_eligible(
            [self.SWITCH_ONLY], control_cycle_seconds=0, pi_min_on_seconds=60, pi_min_off_seconds=60
        )
        assert result.eligible is False

    def test_control_cycle_over_sixty_seconds_is_not_eligible(self) -> None:
        result = pi_eligible(
            [self.SWITCH_ONLY],
            control_cycle_seconds=MAX_CONTROL_CYCLE_SECONDS + 1,
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.eligible is False

    def test_control_cycle_longer_than_pi_min_on_is_not_eligible(self) -> None:
        result = pi_eligible(
            [self.SWITCH_ONLY],
            control_cycle_seconds=60,
            pi_min_on_seconds=30,
            pi_min_off_seconds=300,
        )
        assert result.eligible is False

    def test_control_cycle_longer_than_pi_min_off_is_not_eligible(self) -> None:
        result = pi_eligible(
            [self.SWITCH_ONLY],
            control_cycle_seconds=60,
            pi_min_on_seconds=300,
            pi_min_off_seconds=30,
        )
        assert result.eligible is False

    def test_a_pure_switch_only_zone_within_bounds_is_eligible(self) -> None:
        result = pi_eligible(
            [self.SWITCH_ONLY, self.SWITCH_ONLY],
            control_cycle_seconds=60,
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.eligible is True


# --------------------------------------------------------------------------- #
# 3. Section 4's anti-windup table -- one windup story per row.
# --------------------------------------------------------------------------- #


def _cycle(
    now: datetime,
    *,
    error_k: str = "1",
    context: str = "schedule:default",
    cycle_seconds: int = 60,
    gain: str = "0.25",
    ti_minutes: int = 180,
    pi_min_on: int = 60,
    pi_min_off: int = 60,
) -> PiCycleInput:
    return PiCycleInput(
        now=now,
        error_k=Decimal(error_k),
        setpoint_context_key=context,
        expected_cycle_seconds=cycle_seconds,
        gain_per_k=Decimal(gain),
        integral_time_minutes=Decimal(ti_minutes),
        pi_min_on_seconds=pi_min_on,
        pi_min_off_seconds=pi_min_off,
    )


class TestWindupWindowOpen:
    def test_two_hours_of_a_reset_open_window_never_accumulates_integral(self) -> None:
        now = _at()
        state = NEUTRAL_PI_STATE
        for _ in range(120):  # 2h at 60s cycles
            state = reset_pi_state(RESET_REASON_WINDOW_OPEN, now=now)
            assert state.integral == 0
            assert state.modulator.remainder_s == 0
            now += timedelta(seconds=60)

    def test_the_first_cycle_after_the_window_closes_starts_from_a_fresh_window(self) -> None:
        # A moderate error that does not saturate on the P term alone -- a windup
        # test needs the integral itself to be observable, not swallowed by the
        # (correct, separate) saturation guard from `pi_arithmetic`.
        now = _at()
        state = reset_pi_state(RESET_REASON_WINDOW_OPEN, now=now)
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, error_k="0.5"))
        assert result.pi_available is True
        assert result.state.integral > 0
        # A single cycle's worth of integration, not an accumulated two-hour debt:
        # bounded by one step's own increment.
        one_cycle_increment = Decimal("0.25") / Decimal(180 * 60) * Decimal("0.5") * Decimal(60)
        assert result.state.integral == one_cycle_increment


class TestWindupFrost:
    def test_frost_protection_resets_every_cycle_it_is_active(self) -> None:
        now = _at()
        state = PiState(
            integral=Decimal("0.9"),
            last_evaluated_at=now,
            setpoint_context_key="schedule:default",
            modulator=NEUTRAL_MODULATOR_STATE,
            awaiting_boundary_until=None,
            last_reset_reason=None,
        )
        for _ in range(30):
            state = reset_pi_state(RESET_REASON_FROST, now=now)
            assert state.integral == 0
            now += timedelta(seconds=60)
        assert state.last_reset_reason == RESET_REASON_FROST


class TestWindupSensorFailure:
    def test_sensor_failure_resets_and_does_not_resume_stale(self) -> None:
        state = reset_pi_state(RESET_REASON_SENSOR_FAILURE, now=_at())
        assert state.integral == 0
        assert state.modulator.frozen_duty is None


class TestWindupValveProtection:
    def test_a_valve_protection_run_holds_at_zero_for_its_whole_duration(self) -> None:
        now = _at()
        state = PiState(
            integral=Decimal("0.8"),
            last_evaluated_at=now,
            setpoint_context_key="schedule:default",
            modulator=NEUTRAL_MODULATOR_STATE,
            awaiting_boundary_until=None,
            last_reset_reason=None,
        )
        for _ in range(10):  # 10 minutes of a valve-protection run
            state = reset_pi_state(RESET_REASON_VALVE_PROTECTION, now=now)
            assert state.integral == 0
            now += timedelta(seconds=60)

    def test_after_the_run_pi_resumes_without_the_old_integral(self) -> None:
        now = _at()
        state = reset_pi_state(RESET_REASON_VALVE_PROTECTION, now=now)
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, error_k="0"))
        assert result.state.integral == 0


class TestWindupOverrideAndBoost:
    """Section 4's override/boost rows have no separate code path -- see the
    `pi_cycle` docstring. These tests prove the *effect* the table describes: reset
    at the start and end, unchanged in between."""

    def test_starting_an_override_resets_the_integral_once(self) -> None:
        now = _at()
        state = NEUTRAL_PI_STATE
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="3"))
        now += timedelta(seconds=60)
        # Override begins: context key changes.
        result = pi_cycle(result.state, _cycle(now, context="override:12", error_k="3"))
        assert result.reason_code != MODULATOR_REASON_TASTGRAD_VORRANG  # sanity, unrelated axis
        one_cycle_increment = Decimal("0.25") * Decimal(3) * Decimal(60) / Decimal(180 * 60)
        assert result.state.integral <= one_cycle_increment + Decimal("0.001")

    def test_the_override_continues_running_between_its_start_and_end(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now, context="override:12")).state
        first_integral = state.integral
        for _ in range(5):
            now += timedelta(seconds=60)
            state = pi_cycle(state, _cycle(now, context="override:12")).state
        assert state.integral >= first_integral

    def test_ending_an_override_resets_the_integral_again(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now, context="override:12", error_k="3")).state
        for _ in range(5):
            now += timedelta(seconds=60)
            state = pi_cycle(state, _cycle(now, context="override:12", error_k="3")).state
        assert state.integral > 0
        now += timedelta(seconds=60)
        # Override ends: context key returns to the schedule.
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="3"))
        assert result.state.last_reset_reason == RESET_REASON_CONTEXT_CHANGE
        # Exactly one cycle's worth of integral -- not the accumulated override run.
        one_cycle_increment = Decimal("0.25") * Decimal(3) * Decimal(60) / Decimal(180 * 60)
        assert result.state.integral <= one_cycle_increment + Decimal("0.001")

    def test_boost_uses_the_same_context_key_mechanism_as_override(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now, context="boost", error_k="3")).state
        for _ in range(3):
            now += timedelta(seconds=60)
            state = pi_cycle(state, _cycle(now, context="boost", error_k="3")).state
        integral_during_boost = state.integral
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="3"))
        assert result.state.integral < integral_during_boost


class TestWindupMinimumDurationBlock:
    """The eighth row is the modulator's own hold -- proven directly on
    `window_modulate` above (`TestWindowModulateRemainder`
    `test_held_below_minimum_without_a_collision_simply_holds` /
    `test_a_window_boundary_duty_change_can_force_an_early_switch`), which is where
    the distinction between 'angehalten' and 'pi_tastgrad_vorrang, dann blockiert der
    Timer gerade nicht' actually lives. This test drives it through `pi_cycle` so the
    row is also verified with a full orchestrated cycle, not just the modulator call."""

    def test_a_blocked_switch_holds_the_integrator_regular_cycles_continue_it(self) -> None:
        now = _at()
        state = PiState(
            integral=Decimal("0.5"),
            last_evaluated_at=now,
            setpoint_context_key="schedule:default",
            modulator=ModulatorState(
                on=False,
                held_for_s=10,
                remainder_s=Decimal(5),
                window_start=window_start_for(now),
                frozen_duty=Decimal("0.5"),
            ),
            awaiting_boundary_until=None,
            last_reset_reason=None,
        )
        now += timedelta(seconds=60)
        result = pi_cycle(
            state,
            _cycle(now, context="schedule:default", error_k="0", pi_min_on=300, pi_min_off=300),
        )
        assert result.integrator_action == INTEGRATOR_HOLD
        assert result.state.integral == Decimal("0.5")


class TestWindupArmingAndInvalidState:
    def test_arming_from_dry_run_waits_for_the_next_full_window_boundary(self) -> None:
        now = _at(minute=5)
        state = reset_pi_state(RESET_REASON_ARMING, now=now, await_next_boundary=True)
        # Still inside the window that was already partially elapsed at arming time.
        result = pi_cycle(state, _cycle(now + timedelta(seconds=60)))
        assert result.pi_available is False
        assert result.integrator_action == INTEGRATOR_HOLD

    def test_pi_becomes_available_again_at_the_next_window_boundary(self) -> None:
        # Realistic cadence: the caller keeps calling pi_cycle every regular
        # control cycle while waiting, not just once at the boundary -- each such
        # call keeps `last_evaluated_at` current even though pi stays unavailable.
        now = _at(minute=5)
        state = reset_pi_state(RESET_REASON_ARMING, now=now, await_next_boundary=True)
        boundary = window_start_for(now) + timedelta(seconds=WINDOW_SECONDS)
        result = None
        while now < boundary:
            now += timedelta(seconds=60)
            result = pi_cycle(state, _cycle(now))
            assert result.pi_available is (now >= boundary)
            state = result.state
        assert result is not None
        assert result.pi_available is True

    def test_a_missing_or_corrupted_state_gets_the_same_safe_arming_treatment(self) -> None:
        now = _at()
        state = reset_pi_state(RESET_REASON_INVALID_STATE, now=now, await_next_boundary=True)
        assert state.last_reset_reason == RESET_REASON_INVALID_STATE
        assert state.awaiting_boundary_until == window_start_for(now) + timedelta(
            seconds=WINDOW_SECONDS
        )

    def test_await_next_boundary_requires_now(self) -> None:
        with pytest.raises(ValueError):
            reset_pi_state(RESET_REASON_ARMING, await_next_boundary=True)


# --------------------------------------------------------------------------- #
# 4. `pi_cycle` -- direct, orchestration-level tests not already covered above.
# --------------------------------------------------------------------------- #


class TestPiCycle:
    def test_a_time_gap_resets_and_reports_pi_unavailable(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now)).state
        now += timedelta(seconds=200)  # > 2 * 60s expected cycle
        result = pi_cycle(state, _cycle(now))
        assert result.pi_available is False
        assert result.reason_code == RESET_REASON_TIME_GAP
        assert result.integrator_action == INTEGRATOR_RESET
        assert result.state.integral == 0

    def test_the_very_first_cycle_has_no_previous_evaluation_and_is_itself_a_lockout(
        self,
    ) -> None:
        result = pi_cycle(NEUTRAL_PI_STATE, _cycle(_at()))
        assert result.pi_available is False
        assert result.reason_code == RESET_REASON_TIME_GAP

    def test_a_regular_run_reports_pi_available_with_a_duty_and_heating_decision(
        self,
    ) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now)).state
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, error_k="2"))
        assert result.pi_available is True
        assert result.heating in (True, False)
        assert result.duty_raw is not None
        assert Decimal(0) <= result.duty_raw <= Decimal(1)

    def test_unchanged_context_across_cycles_does_not_reset(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now, context="schedule:default")).state
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="1"))
        assert result.state.last_reset_reason != RESET_REASON_CONTEXT_CHANGE


class TestPiCycleSwitched:
    """`PiCycleOutput.switched` -- whether the modulator's on/off output flipped
    this cycle. A caller (`services/shadow_run.py`) needs this to tell a genuine
    flip apart from an ordinary continuing cycle when it persists the timestamp of
    the last switch separately from `held_for_s`."""

    def test_a_time_gap_reset_reports_unswitched(self) -> None:
        now = _at()
        state = pi_cycle(NEUTRAL_PI_STATE, _cycle(now)).state
        now += timedelta(seconds=200)
        result = pi_cycle(state, _cycle(now))
        assert result.switched is False

    def test_waiting_for_a_window_boundary_reports_unswitched(self) -> None:
        now = _at(minute=5)
        state = reset_pi_state(RESET_REASON_ARMING, now=now, await_next_boundary=True)
        result = pi_cycle(state, _cycle(now + timedelta(seconds=60)))
        assert result.switched is False

    def test_a_duty_of_one_switches_a_previously_off_zone_on(self) -> None:
        now = _at()
        state = PiState(
            integral=Decimal("1"),
            last_evaluated_at=now,
            setpoint_context_key="schedule:default",
            modulator=ModulatorState(
                on=False,
                held_for_s=600,
                remainder_s=Decimal(0),
                window_start=window_start_for(now),
                frozen_duty=Decimal("1"),
            ),
            awaiting_boundary_until=None,
            last_reset_reason=None,
        )
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="10"))
        assert result.heating is True
        assert result.switched is True
        assert result.state.modulator.held_for_s == 60

    def test_an_unchanged_on_off_output_reports_unswitched(self) -> None:
        now = _at()
        state = PiState(
            integral=Decimal("1"),
            last_evaluated_at=now,
            setpoint_context_key="schedule:default",
            modulator=ModulatorState(
                on=True,
                held_for_s=600,
                remainder_s=Decimal(0),
                window_start=window_start_for(now),
                frozen_duty=Decimal("1"),
            ),
            awaiting_boundary_until=None,
            last_reset_reason=None,
        )
        now += timedelta(seconds=60)
        result = pi_cycle(state, _cycle(now, context="schedule:default", error_k="10"))
        assert result.heating is True
        assert result.switched is False


# --------------------------------------------------------------------------- #
# 6. Mutation-testing follow-up (cosmic-ray-pi-control.toml). Closes the 53
# survivors from the second measurement -- see `cosmic-ray-pi-control-assessment.md`
# for the full accounting, including which mutants are classified as equivalent
# rather than tested here, and why.
# --------------------------------------------------------------------------- #


class TestFixedConstantsAndNeutralDefaults:
    """Direct, literal checks of the module's few fixed numbers (section 3: 'nicht
    ein dritter Tuning-Parameter') and of the neutral defaults a reset produces.
    Each of these is read by a caller as a concrete fact -- not just a value that
    happens to cancel out inside a larger calculation already covered elsewhere --
    so a mutation to the literal itself must fail a test that pins the literal."""

    def test_window_length_is_exactly_fifteen_minutes(self) -> None:
        assert WINDOW_SECONDS == 900

    def test_remainder_bound_matches_the_window_length(self) -> None:
        assert REMAINDER_LIMIT_S == Decimal(900)

    def test_activation_ceiling_is_exactly_sixty_seconds(self) -> None:
        assert MAX_CONTROL_CYCLE_SECONDS == 60

    def test_arming_waits_for_a_boundary_exactly_nine_hundred_seconds_out(self) -> None:
        """Deliberately not built from the `WINDOW_SECONDS` import on the
        expectation side too -- comparing a value against the very constant that
        produced it proves nothing once that constant is the thing being mutated.
        `_at(minute=45)` is a plain, independent literal."""
        now = _at(minute=37)
        state = reset_pi_state(RESET_REASON_ARMING, now=now, await_next_boundary=True)
        assert state.awaiting_boundary_until == _at(minute=45)

    def test_neutral_pi_state_starts_with_no_accumulated_error(self) -> None:
        assert NEUTRAL_PI_STATE.integral == Decimal(0)

    def test_neutral_modulator_state_starts_off(self) -> None:
        """A freshly reset zone must not assume it is already heating."""
        assert NEUTRAL_MODULATOR_STATE.on is False


class TestPiDtOneSecondBoundary:
    def test_a_one_second_gap_is_still_integrated(self) -> None:
        previous = _at()
        now = previous + timedelta(seconds=1)
        assert pi_dt(previous_evaluated_at=previous, now=now, expected_cycle_seconds=60) == 1


class TestPiArithmeticWindupBoundaries:
    """The anti-windup freeze (section 2) must hold the integral at its *true*
    prior value -- not merely land on the same clamped output that a non-frozen
    step would also produce once the output is capped at 0 or 1 anyway. Every
    existing windup test starts the integral already at the 0/1 ceiling, where
    freezing and not-freezing are indistinguishable because the clamp masks the
    difference; these start strictly inside (0, 1) so the two paths actually
    diverge."""

    def test_freeze_preserves_the_true_integral_with_a_large_saturating_error(self) -> None:
        _, integral = pi_arithmetic(
            integral=Decimal("0.9"),
            error_k=Decimal("2"),
            dt_seconds=Decimal(60),
            gain_per_k=Decimal("0.25"),
            integral_time_minutes=Decimal(180),
        )
        assert integral == Decimal("0.9")

    def test_freeze_triggers_right_at_the_saturation_edge_not_only_far_past_it(self) -> None:
        """error_k=0.5 alone would not saturate u -- only combined with the
        existing integral does u_before reach exactly 1. A freeze condition that
        only engages for much larger errors would miss this."""
        _, integral = pi_arithmetic(
            integral=Decimal("0.9"),
            error_k=Decimal("0.5"),
            dt_seconds=Decimal(60),
            gain_per_k=Decimal("0.25"),
            integral_time_minutes=Decimal(180),
        )
        assert integral == Decimal("0.9")

    def test_freeze_preserves_the_true_integral_at_low_saturation(self) -> None:
        _, integral = pi_arithmetic(
            integral=Decimal("0.1"),
            error_k=Decimal("-0.5"),
            dt_seconds=Decimal(60),
            gain_per_k=Decimal("0.25"),
            integral_time_minutes=Decimal(180),
        )
        assert integral == Decimal("0.1")


class TestWindowModulateValidationBoundaries:
    def test_a_negative_u_raw_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            window_modulate(
                NEUTRAL_MODULATOR_STATE,
                now=_at(),
                u_raw=Decimal("-0.5"),
                dt_seconds=Decimal(60),
                pi_min_on_seconds=60,
                pi_min_off_seconds=60,
            )

    def test_a_one_second_dt_is_accepted_and_integrated_exactly(self) -> None:
        result = window_modulate(
            NEUTRAL_MODULATOR_STATE,
            now=_at(),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(1),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.state.remainder_s == Decimal("0.5")


class TestWindowModulateCorruptedFrozenDuty:
    def test_a_missing_frozen_duty_within_the_current_window_is_refrozen_not_left_none(
        self,
    ) -> None:
        """A `window_start` that already matches the current boundary but a
        missing `frozen_duty` describes a corrupted persisted row -- the two
        fields are supposed to be set together every window. The modulator must
        recover by freezing a fresh duty this cycle instead of operating on (and
        eventually asserting against) a `None` duty."""
        now = _at()
        state = ModulatorState(
            on=False,
            held_for_s=None,
            remainder_s=Decimal(0),
            window_start=window_start_for(now),
            frozen_duty=None,
        )
        result = window_modulate(
            state,
            now=now,
            u_raw=Decimal("1"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.on is True
        assert result.state.frozen_duty == Decimal("1")


class TestWindowModulateMinimumDurationBoundary:
    def test_held_for_exactly_the_minimum_duration_is_no_longer_blocked(self) -> None:
        """`held_for_s == pi_min_off_seconds` has already satisfied the minimum --
        the modulator must decide by the remainder from here on, not stay held."""
        state = ModulatorState(
            on=False,
            held_for_s=300,
            remainder_s=Decimal(5),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.reason_code == MODULATOR_REASON_REGULAR
        assert result.on is True  # remainder_s=5 > 0 favours on


class TestWindowModulateRegularTieBreakBoundary:
    def test_a_small_positive_remainder_between_zero_and_one_still_favours_on(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=1000,
            remainder_s=Decimal("0.5"),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.on is True
        assert result.reason_code == MODULATOR_REASON_REGULAR


class TestWindowModulateRemainderThresholdBoundary:
    """Section 3's remainder bound is exactly ±900 seconds (`REMAINDER_LIMIT_S`).
    A below-minimum collision must hold exactly at the bound and only override
    once it would be *exceeded* -- these four cases pin both the bound's own
    value and the strict (not inclusive) comparison, on both signs. `duty=0.9` is
    deliberately not 0 or 1, so these land in the ordinary below-minimum branch
    rather than the separate 'duty is absolute' short-circuit above it."""

    def test_a_projected_remainder_of_exactly_nine_hundred_still_holds(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=0,
            remainder_s=Decimal(846),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.9"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.switched is False
        assert result.reason_code == MODULATOR_REASON_HELD

    def test_a_projected_remainder_of_nine_hundred_and_one_forces_the_switch(self) -> None:
        state = ModulatorState(
            on=False,
            held_for_s=0,
            remainder_s=Decimal(847),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.9"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.switched is True
        assert result.on is True
        assert result.reason_code == MODULATOR_REASON_TASTGRAD_VORRANG

    def test_a_projected_remainder_of_exactly_minus_nine_hundred_still_holds(self) -> None:
        state = ModulatorState(
            on=True,
            held_for_s=0,
            remainder_s=Decimal(-894),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.9"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.switched is False
        assert result.reason_code == MODULATOR_REASON_HELD

    def test_a_projected_remainder_of_minus_nine_hundred_and_one_forces_the_switch(self) -> None:
        state = ModulatorState(
            on=True,
            held_for_s=0,
            remainder_s=Decimal(-895),
            window_start=_at(),
            frozen_duty=Decimal("0.9"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.9"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.switched is True
        assert result.on is False
        assert result.reason_code == MODULATOR_REASON_TASTGRAD_VORRANG


class TestHeldForAccumulation:
    def test_held_duration_accumulates_the_elapsed_seconds_across_cycles(self) -> None:
        """`held_for_s` is the running count the PI minimum-duration check
        compares against `pi_min_on_seconds` / `pi_min_off_seconds` -- it must add
        each cycle's `dt` to the running total, not replace it or combine it with
        a floor-division, shift, or bitwise operator."""
        state = ModulatorState(
            on=False,
            held_for_s=7,
            remainder_s=Decimal(0),
            window_start=_at(),
            frozen_duty=Decimal("0.5"),
        )
        result = window_modulate(
            state,
            now=_at(minute=1),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(3),
            pi_min_on_seconds=1000,
            pi_min_off_seconds=1000,
        )
        assert result.switched is False
        assert result.state.held_for_s == 10


class TestPiEligibleControlCycleBoundaries:
    _SWITCH_ONLY = ActuatorProfile(
        self_regulating=False, has_switch_capability=True, has_thermostat_capability=False
    )

    def test_a_one_second_control_cycle_is_not_rejected_as_non_positive(self) -> None:
        result = pi_eligible(
            [self._SWITCH_ONLY],
            control_cycle_seconds=1,
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        assert result.eligible is True

    def test_sixty_one_seconds_is_over_the_activation_ceiling(self) -> None:
        """A literal 61, not `MAX_CONTROL_CYCLE_SECONDS + 1` -- reusing the
        mutated constant on both sides would prove nothing."""
        result = pi_eligible(
            [self._SWITCH_ONLY],
            control_cycle_seconds=61,
            pi_min_on_seconds=300,
            pi_min_off_seconds=300,
        )
        assert result.eligible is False


class TestDataclassesStayFrozen:
    """The module docstring's purity guarantee -- 'no mutation of the arguments
    (every dataclass is frozen)' -- only holds if a caller literally cannot
    mutate a state or result object handed back to it, e.g. to keep a previous
    cycle's `PiCycleOutput` around for a log line while `pi_cycle()` builds an
    unrelated new one via `replace()` for the next one."""

    def test_modulator_state_rejects_attribute_assignment(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            NEUTRAL_MODULATOR_STATE.on = True  # type: ignore[misc]

    def test_modulator_result_rejects_attribute_assignment(self) -> None:
        result = window_modulate(
            NEUTRAL_MODULATOR_STATE,
            now=_at(),
            u_raw=Decimal("0.5"),
            dt_seconds=Decimal(60),
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.on = False  # type: ignore[misc]

    def test_pi_state_rejects_attribute_assignment(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            NEUTRAL_PI_STATE.integral = Decimal(1)  # type: ignore[misc]

    def test_pi_cycle_input_rejects_attribute_assignment(self) -> None:
        cycle = _cycle(_at())
        with pytest.raises(dataclasses.FrozenInstanceError):
            cycle.error_k = Decimal(5)  # type: ignore[misc]

    def test_pi_cycle_output_rejects_attribute_assignment(self) -> None:
        result = pi_cycle(NEUTRAL_PI_STATE, _cycle(_at()))
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.pi_available = True  # type: ignore[misc]

    def test_actuator_profile_rejects_attribute_assignment(self) -> None:
        profile = ActuatorProfile(
            self_regulating=False, has_switch_capability=True, has_thermostat_capability=False
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.self_regulating = True  # type: ignore[misc]

    def test_pi_eligibility_rejects_attribute_assignment(self) -> None:
        result = pi_eligible(
            [
                ActuatorProfile(
                    self_regulating=False,
                    has_switch_capability=True,
                    has_thermostat_capability=False,
                )
            ],
            control_cycle_seconds=60,
            pi_min_on_seconds=60,
            pi_min_off_seconds=60,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.eligible = False  # type: ignore[misc]
