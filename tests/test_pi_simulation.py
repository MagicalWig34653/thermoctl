"""Closed-loop scenarios for the optional PI rules -- SIMULATION, KEIN MESSNACHWEIS.

The room here is a synthetic model (`tests/pi_simulation.py`), the controller is the real
`pi_cycle()` with the production window modulator and minimum durations. What these
tests establish is how the *rules* behave on two plausible plants (a faster and a slower
one), as regression guards:

- surplus decay (A): after a sunny day the integral is not gone and the night holds the
  setpoint,
- integral separation: approaching from far below overshoots less,
- feed-forward: a falling outdoor temperature no longer drags the room down first.

They are not evidence for any real room. Where the acceptance criteria of the task
(night minimum >= setpoint - 0.2 K, overshoot after sun <= +0.3 K, 80 % of the time in
the +-0.2 K band) are *not* reached, the corresponding test is a strict `xfail` with the
reason and the measured figure -- so the gap stays visible and a later improvement shows
up as a failure to remove the marker, instead of a claim nobody checks.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pi_simulation import (
    PLANT_FAST,
    PLANT_SLOW,
    Config,
    Plant,
    Sample,
    Scenario,
    approach_from_below,
    band_share,
    falling_outdoor,
    integral_deficit_hours,
    max_deviation,
    min_deviation,
    setpoint_jump,
    simulate,
    sunny_day,
)

PLANTS = [
    pytest.param(PLANT_FAST, id="schnelle-strecke"),
    pytest.param(PLANT_SLOW, id="traege-strecke"),
]

TODAY = Config()  # Kp 0.5, Ti 90, every optional rule off
KP1 = Config(kp=Decimal(1), ti_minutes=Decimal(180))
KP1_A = Config(kp=Decimal(1), ti_minutes=Decimal(180), surplus_decay_factor=Decimal("0.2"))
KP1_A_FF = Config(
    kp=Decimal(1),
    ti_minutes=Decimal(180),
    surplus_decay_factor=Decimal("0.2"),
    feedforward_gain=Decimal("0.02"),
)

TOLERANCE = 1e-9


def _run(scenario: Scenario, plant: Plant, config: Config, hours: float = 48) -> list[Sample]:
    return simulate(scenario, plant, config, hours=hours)


# --------------------------------------------------------------------------- #
# Sunny day: the integral must survive the surplus.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plant", PLANTS)
class TestSunnyDay:
    def test_today_the_integral_stays_short_for_more_than_a_day(self, plant: Plant) -> None:
        """The problem the rule solves: unwinding at a surplus leaves a long deficit."""
        hours, _ = integral_deficit_hours(_run(sunny_day(), plant, TODAY), 10, 44)
        assert hours is not None
        assert hours > 24

    def test_with_the_decay_factor_there_is_no_integral_deficit(self, plant: Plant) -> None:
        history = _run(sunny_day(), plant, KP1_A)
        assert integral_deficit_hours(history, 10, 44) == (None, 0.0)

    def test_night_minimum_is_within_two_tenths_of_the_setpoint(self, plant: Plant) -> None:
        history = _run(sunny_day(), plant, KP1_A)
        assert min_deviation(history, 20, 28) >= -0.2 - TOLERANCE

    def test_the_night_is_not_colder_than_without_the_rule(self, plant: Plant) -> None:
        without = min_deviation(_run(sunny_day(), plant, KP1), 20, 28)
        with_rule = min_deviation(_run(sunny_day(), plant, KP1_A), 20, 28)
        assert with_rule >= without - TOLERANCE

    def test_overshoot_is_not_made_worse_by_the_rule(self, plant: Plant) -> None:
        without = max_deviation(_run(sunny_day(), plant, KP1), 10, 20)
        with_rule = max_deviation(_run(sunny_day(), plant, KP1_A), 10, 20)
        assert with_rule <= without + TOLERANCE

    @pytest.mark.xfail(
        strict=True,
        reason="Nicht erreicht (Simulation): die Sonne heizt den Raum um +0,6 bis +0,7 K "
        "ueber den Sollwert; abgesenkt wird das nur ueber die Sonnenabsenkung des "
        "Sollwerts, die hier nicht modelliert ist. Zielwert laut Aufgabe: <= +0,3 K.",
    )
    def test_acceptance_overshoot_after_sun_at_most_three_tenths(self, plant: Plant) -> None:
        history = _run(sunny_day(), plant, KP1_A)
        assert max_deviation(history, 10, 20) <= 0.3 + TOLERANCE


class TestSunnyDayBandShare:
    def test_fast_plant_reaches_eighty_percent_in_the_band(self) -> None:
        assert band_share(_run(sunny_day(), PLANT_FAST, KP1_A), 4, 44) >= 0.8

    @pytest.mark.xfail(
        strict=True,
        reason="Nicht erreicht (Simulation, traege Strecke): rund 76 % statt 80 %, weil "
        "der Sonneneintrag den Raum ueber das Band hebt (siehe Ueberschwingen).",
    )
    def test_acceptance_slow_plant_reaches_eighty_percent_in_the_band(self) -> None:
        assert band_share(_run(sunny_day(), PLANT_SLOW, KP1_A), 4, 44) >= 0.8


# --------------------------------------------------------------------------- #
# Falling outdoor temperature: the feed-forward knows the demand beforehand.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plant", PLANTS)
class TestFallingOutdoorTemperature:
    def test_without_feedforward_the_integral_needs_hours(self, plant: Plant) -> None:
        hours, _ = integral_deficit_hours(_run(falling_outdoor(), plant, KP1_A), 18, 44)
        assert hours is not None
        assert hours > 1.5

    def test_with_feedforward_the_integral_stays_near_the_needed_value(self, plant: Plant) -> None:
        history = _run(falling_outdoor(), plant, KP1_A_FF)
        assert integral_deficit_hours(history, 18, 44) == (None, 0.0)

    def test_with_feedforward_the_room_holds_the_setpoint_through_the_cold_night(
        self, plant: Plant
    ) -> None:
        history = _run(falling_outdoor(), plant, KP1_A_FF)
        assert min_deviation(history, 18, 44) >= -0.2 - TOLERANCE
        assert band_share(history, 4, 44) >= 0.8

    def test_the_feedforward_does_not_push_the_room_above_the_band(self, plant: Plant) -> None:
        history = _run(falling_outdoor(), plant, KP1_A_FF)
        assert max_deviation(history, 4, 44) <= 0.2 + TOLERANCE

    def test_the_night_is_not_colder_than_without_the_feedforward(self, plant: Plant) -> None:
        without = min_deviation(_run(falling_outdoor(), plant, KP1_A), 18, 44)
        with_ff = min_deviation(_run(falling_outdoor(), plant, KP1_A_FF), 18, 44)
        assert with_ff >= without - TOLERANCE


# --------------------------------------------------------------------------- #
# Setpoint jump and approach from below: integral separation.
# --------------------------------------------------------------------------- #

SEPARATED = Config(separation_k=Decimal("0.5"))
SEPARATED_KP1 = Config(kp=Decimal(1), ti_minutes=Decimal(180), separation_k=Decimal("0.5"))


@pytest.mark.parametrize("plant", PLANTS)
class TestApproachFromBelow:
    def test_separation_reduces_the_overshoot_when_approaching(self, plant: Plant) -> None:
        without = max_deviation(_run(approach_from_below(), plant, TODAY, 40), 4, 16)
        separated = max_deviation(_run(approach_from_below(), plant, SEPARATED, 40), 4, 16)
        assert without >= 0.5
        assert separated <= without - 0.3 + TOLERANCE

    def test_separation_reduces_the_overshoot_after_a_setpoint_jump(self, plant: Plant) -> None:
        without = max_deviation(_run(setpoint_jump(), plant, TODAY, 40), 6, 14)
        separated = max_deviation(_run(setpoint_jump(), plant, SEPARATED, 40), 6, 14)
        assert separated < without

    def test_separation_with_kp_one_does_not_worsen_the_overshoot(self, plant: Plant) -> None:
        without = max_deviation(_run(approach_from_below(), plant, KP1, 40), 4, 16)
        separated = max_deviation(_run(approach_from_below(), plant, SEPARATED_KP1, 40), 4, 16)
        assert separated <= without + TOLERANCE


class TestSeparationCosts:
    def test_separation_with_the_old_gain_is_slower_and_dips_lower(self) -> None:
        """The price, stated honestly: with Kp 0.5 the frozen integral lets the room
        settle later and lower after the approach (the proportional share alone is weak)."""
        without = _run(approach_from_below(), PLANT_FAST, TODAY, 40)
        separated = _run(approach_from_below(), PLANT_FAST, SEPARATED, 40)
        assert min_deviation(separated, 8, 16) < min_deviation(without, 8, 16)


# --------------------------------------------------------------------------- #
# Defaults: the three rules off are exactly the old closed loop.
# --------------------------------------------------------------------------- #


class TestRulesOffIsTheOldLoop:
    def test_explicit_neutral_config_gives_the_identical_run(self) -> None:
        explicit = Config(
            surplus_decay_factor=Decimal(1),
            separation_k=Decimal(0),
            feedforward_gain=None,
        )
        assert _run(sunny_day(), PLANT_FAST, explicit, 24) == _run(
            sunny_day(), PLANT_FAST, TODAY, 24
        )
