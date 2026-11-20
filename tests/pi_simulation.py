"""Closed-loop room simulation around the real `pi_cycle()` -- helper for the tests.

SIMULATION, KEIN MESSNACHWEIS. Everything here is a deliberately simple, synthetic
room model (valve and delay as first-order lags, floor as a first-order lag, heat loss
proportional to the difference to a lagged outdoor temperature, sun as additional heat
input, 0.1 K sensor resolution). It shows how the *rules* behave on a plausible plant;
it says nothing about how a particular building behaves. The numbers it produces are
never evidence of comfort in a real room -- only regression guards for the rules, and
the basis for an honest list of what the rules do not achieve.

The control side is the production code: `pi_cycle()` (including the window modulator
and its minimum durations) and `pi_feedforward`. Nothing of the PI maths is copied.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thermoctl.domain.pi_control import (
    NEUTRAL_MODULATOR_STATE,
    PiCycleInput,
    PiState,
    pi_cycle,
)
from thermoctl.domain.pi_feedforward import feedforward_duty, smoothed_outdoor

CYCLE_SECONDS = 30
STEPS_PER_HOUR = 3600 // CYCLE_SECONDS
START = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
# The outdoor sensor reports every few minutes, not every cycle.
SENSOR_REPORT_EVERY_STEPS = 10  # 5 min


def _d(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


def _sensor(value: float) -> float:
    """0.1 K resolution of the room sensor."""
    return math.floor(value * 10 + 0.5) / 10


def jitter(hour: float) -> float:
    """Outdoor sensor noise: swings of a few tenths of a kelvin between readings."""
    return 0.25 * math.sin(hour * 2 * math.pi / 0.55) + 0.15 * math.sin(
        hour * 2 * math.pi / 0.23 + 1.0
    )


@dataclass(frozen=True)
class Plant:
    """Heating power `heat` (K/h at full duty), loss `loss` (1/h), delay `delay_min`."""

    heat: float
    loss: float
    delay_min: float


PLANT_FAST = Plant(heat=0.40, loss=0.0075, delay_min=25)
PLANT_SLOW = Plant(heat=0.30, loss=0.006, delay_min=35)


@dataclass(frozen=True)
class Scenario:
    setpoint: Callable[[float], float]
    outdoor: Callable[[float], float]
    sun: Callable[[float], float]  # K/h of additional heat input


def sunny_day() -> Scenario:
    """Setpoint 20.5, mild outside, sun adds up to 0.3 K/h between 10 and 16 h."""
    return Scenario(
        setpoint=lambda h: 20.5,
        outdoor=lambda h: 9.5 + jitter(h),
        sun=lambda h: 0.30 * math.sin(math.pi * (h - 10) / 6) ** 2 if 10 <= h <= 16 else 0.0,
    )


def falling_outdoor() -> Scenario:
    """Setpoint 20, outside falls from 7 to 1 degrees between 18 and 26 h."""

    def outdoor(h: float) -> float:
        if h <= 18:
            base = 7.0
        elif h <= 26:
            base = 7.0 - 6.0 * (h - 18) / 8
        else:
            base = 1.0
        return base + jitter(h)

    return Scenario(setpoint=lambda h: 20.0, outdoor=outdoor, sun=lambda h: 0.0)


def setpoint_jump() -> Scenario:
    """Setpoint 20, raised to 21 between 6 and 14 h."""
    return Scenario(
        setpoint=lambda h: 21.0 if 6 <= h < 14 else 20.0,
        outdoor=lambda h: 6.0 + jitter(h),
        sun=lambda h: 0.0,
    )


def approach_from_below() -> Scenario:
    """Setpoint 19 until 4 h, then 20: the room has to be brought up from below."""
    return Scenario(
        setpoint=lambda h: 19.0 if h < 4 else 20.0,
        outdoor=lambda h: 6.0 + jitter(h),
        sun=lambda h: 0.0,
    )


@dataclass(frozen=True)
class Config:
    """The controller side of a run. Defaults = the rules switched off."""

    kp: Decimal = Decimal("0.5")
    ti_minutes: Decimal = Decimal(90)
    surplus_decay_factor: Decimal = Decimal(1)
    separation_k: Decimal = Decimal(0)
    feedforward_gain: Decimal | None = None
    smoothing_hours: int = 3
    pi_min_on_seconds: int = 180
    pi_min_off_seconds: int = 60


@dataclass(frozen=True)
class Sample:
    hour: float
    measured: float
    setpoint: float
    heating: bool
    integral: float
    true_temperature: float
    # Steady-state duty the room needs minus the feed-forward share: what the
    # integral should converge to.
    needed_integral: float


def simulate(
    scenario: Scenario,
    plant: Plant,
    config: Config,
    *,
    hours: float = 48,
    start_temperature: float | None = None,
    steady: bool = True,
) -> list[Sample]:
    """Runs `hours` of closed loop. `steady`: start in equilibrium with the right integral."""
    setpoint_at, outdoor_at, sun_at = scenario.setpoint, scenario.outdoor, scenario.sun
    set0, out0 = setpoint_at(0.0), outdoor_at(0.0)
    needed0 = max(0.0, min(1.0, plant.loss * (set0 - out0) / plant.heat))

    # --- plant state ---
    valve = floor = needed0
    delay = deque([needed0] * int(plant.delay_min * 60 / CYCLE_SECONDS))
    true_temperature = set0 if start_temperature is None else start_temperature
    outdoor_effective = out0

    # --- controller state ---
    feedforward_on = config.feedforward_gain is not None
    outdoor_samples: deque[tuple[datetime, Decimal | None]] = deque(maxlen=200)
    start_integral = Decimal(0)
    if steady:
        start_integral = _d(needed0)
        if feedforward_on:
            share = feedforward_duty(
                enabled=True,
                gain=config.feedforward_gain or Decimal(0),
                setpoint_c=_d(set0),
                outdoor_smoothed_c=_d(out0),
            )
            start_integral = max(Decimal(0), _d(needed0) - share)
    state = PiState(
        integral=start_integral,
        last_evaluated_at=START - timedelta(seconds=CYCLE_SECONDS),
        setpoint_context_key="plan",
        modulator=NEUTRAL_MODULATOR_STATE,
        awaiting_boundary_until=None,
        last_reset_reason=None,
    )
    if steady and feedforward_on:
        # The smoothing window starts filled with the equilibrium outdoor value.
        for back in range(1, 40):
            outdoor_samples.append((START - timedelta(minutes=5 * (40 - back)), _d(out0)))
    heating = False
    share_now = Decimal(0)

    history: list[Sample] = []
    for step in range(int(hours * STEPS_PER_HOUR)):
        hour = step / STEPS_PER_HOUR
        now = START + timedelta(seconds=step * CYCLE_SECONDS)
        setpoint = setpoint_at(hour)
        measured = _sensor(true_temperature)
        outdoor = outdoor_at(hour)

        if feedforward_on:
            if step % SENSOR_REPORT_EVERY_STEPS == 0:
                outdoor_samples.append((now, _d(outdoor)))
            share_now = feedforward_duty(
                enabled=True,
                gain=config.feedforward_gain or Decimal(0),
                setpoint_c=_d(setpoint),
                outdoor_smoothed_c=smoothed_outdoor(
                    list(outdoor_samples), now, config.smoothing_hours
                ),
            )
        result = pi_cycle(
            state,
            PiCycleInput(
                now=now,
                error_k=_d(setpoint) - _d(measured),
                setpoint_context_key="plan",
                expected_cycle_seconds=CYCLE_SECONDS,
                gain_per_k=config.kp,
                integral_time_minutes=config.ti_minutes,
                pi_min_on_seconds=config.pi_min_on_seconds,
                pi_min_off_seconds=config.pi_min_off_seconds,
                surplus_decay_factor=config.surplus_decay_factor,
                integral_separation_k=config.separation_k,
                feedforward_duty=share_now,
            ),
        )
        state = result.state
        if result.heating is not None:
            heating = result.heating

        # --- plant, one cycle ---
        valve += ((1.0 if heating else 0.0) - valve) * CYCLE_SECONDS / 180.0
        delay.append(valve)
        floor += (delay.popleft() - floor) * CYCLE_SECONDS / 900.0
        outdoor_effective += (outdoor - outdoor_effective) * CYCLE_SECONDS / (3 * 3600.0)
        dT = plant.heat * floor - plant.loss * (true_temperature - outdoor_effective) + sun_at(hour)
        true_temperature += dT * CYCLE_SECONDS / 3600.0
        needed = plant.loss * (setpoint - outdoor_effective) / plant.heat
        history.append(
            Sample(
                hour=hour,
                measured=measured,
                setpoint=setpoint,
                heating=heating,
                integral=float(state.integral),
                true_temperature=true_temperature,
                needed_integral=needed - float(share_now),
            )
        )
    return history


# --------------------------------------------------------------------------- #
# Metrics.
# --------------------------------------------------------------------------- #


def _segment(history: list[Sample], start_hour: float, end_hour: float) -> list[Sample]:
    return history[int(start_hour * STEPS_PER_HOUR) : int(end_hour * STEPS_PER_HOUR)]


def band_share(
    history: list[Sample], start_hour: float, end_hour: float, tol: float = 0.2
) -> float:
    """Share of time the *measured* value is within +-tol of the setpoint."""
    segment = _segment(history, start_hour, end_hour)
    inside = sum(1 for s in segment if abs(s.measured - s.setpoint) <= tol + 1e-9)
    return inside / len(segment)


def max_deviation(history: list[Sample], start_hour: float, end_hour: float) -> float:
    """Largest measured value above the setpoint (K)."""
    return max(s.measured - s.setpoint for s in _segment(history, start_hour, end_hour))


def min_deviation(history: list[Sample], start_hour: float, end_hour: float) -> float:
    """Lowest measured value relative to the setpoint (K, negative = below)."""
    return min(s.measured - s.setpoint for s in _segment(history, start_hour, end_hour))


def integral_deficit_hours(
    history: list[Sample], start_hour: float, end_hour: float
) -> tuple[float | None, float]:
    """Time from the first integral shortfall above 5 points to the last above 3 points.

    Returns `(hours, largest shortfall)`; `(None, 0.0)` if there never was one. The
    shortfall is the needed integral minus the actual one.
    """
    first = last = None
    peak = 0.0
    for index in range(int(start_hour * STEPS_PER_HOUR), int(end_hour * STEPS_PER_HOUR)):
        gap = history[index].needed_integral - history[index].integral
        if first is None and gap > 0.05:
            first = index
        if first is not None:
            peak = max(peak, gap)
            if gap > 0.03:
                last = index
    if first is None or last is None:
        return None, 0.0
    return (last - first) / STEPS_PER_HOUR, peak
