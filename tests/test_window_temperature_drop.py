from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.window_temperature_drop import (
    WINDOW_TEMP_DROP_THRESHOLD_K,
    temperature_detection_cap_exceeded,
    temperature_detection_gap_within_tolerance,
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
    reference (the second-highest of the readings before the current one, once
    there are at least three of them) must reflect the recent plateau, not get
    pulled down by an unrelated, much older, lower reading earlier in the same
    window."""
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
    for that single outlier; with (as here) at least three readings before the
    current one, the second-highest of them must not be fooled by it the way a
    bare maximum would be."""
    values = [
        Decimal("20.90"),
        Decimal("21.00"),
        Decimal("20.90") + WINDOW_TEMP_DROP_THRESHOLD_K + Decimal("1"),  # the outlier
        Decimal("20.95"),
        Decimal("21.00"),  # current reading, unremarkable
    ]

    assert window_open_suspected(values, history_covers_duration=True) is False


def test_the_reviewers_own_two_historical_readings_are_still_detected() -> None:
    """Second cross-review round, Befund 2: a median over the two historical
    readings here -- one old, one at the actual pre-drop peak -- comes out to
    1.45 K against the 1.5 K default threshold and misses the drop entirely,
    right when fast detection matters most (just after a window opens, when
    only a couple of readings could possibly show it yet). With fewer than
    three historical readings there is nothing to safely discard, so the
    reference stays their plain maximum (20.10), exactly reproducing the
    original, pre-median behaviour this reviewer's own numbers checked
    against: a 1.50 K drop, at the default threshold, detected."""
    values = [Decimal("20.00"), Decimal("20.10"), Decimal("18.60")]

    assert window_open_suspected(values, history_covers_duration=True) is True


def test_the_reviewers_own_numbers_are_not_detected_by_a_plain_median() -> None:
    """Pins the regression itself down, not just its fix: this is the exact
    computation a reviewer found by hand against the (now removed) median
    implementation -- 1.45 K, short of the 1.5 K threshold -- so this failing
    directly proves the historical maximum, not the median, is genuinely what
    fires here, rather than merely asserting the right outcome for the wrong
    reason."""
    import statistics

    historical = [Decimal("20.00"), Decimal("20.10")]
    current = Decimal("18.60")

    drop_via_median = statistics.median(historical) - current

    assert drop_via_median < WINDOW_TEMP_DROP_THRESHOLD_K


def test_a_single_outlier_can_still_fool_a_short_history() -> None:
    """The accepted residual gap, held down by a test rather than left only in
    the docstring: with just two readings before the current one, there is
    nothing to out-vote a single spurious high value with, so it can still
    fake a drop here exactly as an unfixed `max()`-only version always could.
    This is the deliberate trade-off documented in the module docstring's
    "residual gap" section, not an oversight -- protecting the two- and
    one-reading case too would mean discarding data these short windows do
    not have to spare, and would cost exactly the sensitivity the previous
    test above depends on."""
    values = [
        Decimal("20.90"),
        Decimal("20.90") + WINDOW_TEMP_DROP_THRESHOLD_K + Decimal("1"),  # the sole outlier
        Decimal("21.00"),  # current reading, unremarkable
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


# --- temperature_detection_gap_within_tolerance --------------------------------


def test_a_recent_detection_is_within_tolerance() -> None:
    last_detected_at = NOW - timedelta(minutes=5)

    assert temperature_detection_gap_within_tolerance(
        last_detected_at, NOW, gap_tolerance_minutes=10
    ) is True


def test_the_tolerance_boundary_belongs_to_the_tolerated_side() -> None:
    """The same convention every other boundary in this module uses -- a gap of
    exactly the tolerance still counts as the same streak continuing."""
    exactly_at = NOW - timedelta(minutes=10)
    just_over = NOW - timedelta(minutes=10) - timedelta(seconds=1)

    assert temperature_detection_gap_within_tolerance(
        exactly_at, NOW, gap_tolerance_minutes=10
    ) is True
    assert temperature_detection_gap_within_tolerance(
        just_over, NOW, gap_tolerance_minutes=10
    ) is False


def test_never_detected_before_is_never_within_tolerance() -> None:
    assert temperature_detection_gap_within_tolerance(None, NOW, gap_tolerance_minutes=10) is False


# --- The cumulative streak, replayed end to end --------------------------------
#
# These two replay the exact composition of `temperature_detection_gap_within_
# tolerance` and `temperature_detection_cap_exceeded` that `services/ingest.py::
# _window_open_from_temperature` performs every cycle -- the level at which the
# second cross-review round's Befund 1 actually lived: not in either function
# alone, but in whether repeatedly composing them across many cycles still lets
# a cumulative streak survive short interruptions. A reviewer replaying 20 runs
# of 420 simulated minutes against the first version of the cap -- which
# measured the streak's age off `window_open_since`, reset on every cycle the
# zone was not currently judged open -- never once saw it fire. These two tests
# pin that regression down where it actually manifests: repeated short gaps
# over hours, not a single isolated call.


def _replay_hold_boundary_flickers(
    *, gap_tolerance_minutes: int, hold_minutes: int = 30, max_suspected_minutes: int = 90
) -> bool:
    """Replays several hours of cycles every 5 minutes, detected every cycle
    except for a single missed one at every hold boundary -- the exact noisy
    re-check the module docstring's threshold section anticipates. Returns
    whether the cap ever fired. `gap_tolerance_minutes` is the one parameter
    the two tests below vary, everything else held fixed between them.
    """
    streak_started_at: datetime | None = None
    last_detected_at: datetime | None = None

    minutes = 0
    while minutes <= 420:
        moment = NOW + timedelta(minutes=minutes)
        # Detected every cycle, except a single flickered miss exactly at
        # every hold boundary.
        detected = minutes == 0 or minutes % hold_minutes != 0
        if detected:
            continues = (
                streak_started_at is not None
                and temperature_detection_gap_within_tolerance(
                    last_detected_at, moment, gap_tolerance_minutes=gap_tolerance_minutes
                )
            )
            streak_started_at = streak_started_at if continues else moment
            last_detected_at = moment
            if temperature_detection_cap_exceeded(
                streak_started_at, moment, max_suspected_minutes=max_suspected_minutes
            ):
                return True
        minutes += 5
    return False


def test_the_cap_survives_repeated_short_interruptions_at_the_hold_boundary() -> None:
    """With the module's own default tolerance (comfortably longer than the
    5-minute step between simulated cycles here), a single missed cycle at
    every hold boundary must not stop the cumulative streak from eventually
    reaching the cap."""
    assert _replay_hold_boundary_flickers(gap_tolerance_minutes=10) is True


def test_the_same_replay_never_caps_without_any_gap_tolerance() -> None:
    """Pins the regression itself down, not just its fix: with zero tolerance
    -- equivalent to the removed, strict 'the span must be perfectly
    unbroken' approach -- the identical flicker pattern above must never let
    the cap fire, even across many simulated hours. If this assertion ever
    starts failing on its own, it is not this test that broke: it means the
    default tolerance was quietly set to zero, or a future change reintroduced
    exactly the defect this module exists to have fixed."""
    assert _replay_hold_boundary_flickers(gap_tolerance_minutes=0) is False
