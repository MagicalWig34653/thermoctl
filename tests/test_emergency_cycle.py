"""Tests for the pure emergency-cycle domain logic (`thermoctl.domain.emergency_cycle`).

Covers Auftrag 6 of `lokal/plaene/0.11.0-notbetrieb.md`: the fixed-cycle ("Festtakt")
and outdoor-curve ("Außenkennlinie") phase computation for actuators taktet under
sensor-failure emergency operation. Nothing here is wired to the control loop or the
database yet -- a later task (Auftrag 7) supplies real profiles/state from the DB
models another task is building in parallel.

Layout:

1. hand-derived timeline for the fixed 600s/1200s cycle across several pairs,
2. exact phase-boundary tests (deadline - 1s / deadline / deadline + 1s),
3. late cycle (no catch-up) and long-pause / clock-regression (disturbed restart),
4. curve interpolation: endpoints, out-of-range clamping, the -5 C midpoint example,
   and a repeating-fraction case that pins down the exact Decimal rounding,
5. cycle-source switching only at a pair boundary,
6. warm-restart hysteresis band,
7. minimum-duration clamping, including the "Ein=0 bleibt 0" rule,
8. first-entry credit for an already-on/already-off relay.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.emergency_cycle import (
    PHASE_OFF,
    PHASE_ON,
    REASON_CONTINUE,
    REASON_DISTURBED,
    REASON_ENTRY,
    REASON_TRANSITION,
    REASON_WARM_LOCK,
    SOURCE_CURVE,
    SOURCE_FIXED,
    CurvePoint,
    CycleInput,
    CycleProfile,
    CycleState,
    OutdoorSample,
    PriorPhaseHint,
    advance,
)

T0 = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)


def _fixed_profile(
    on_seconds: int = 600, off_seconds: int = 1200, hysteresis_k: str = "1"
) -> CycleProfile:
    return CycleProfile(
        fixed_on_seconds=on_seconds,
        fixed_off_seconds=off_seconds,
        warm_restart_hysteresis_k=Decimal(hysteresis_k),
        curve_points=(),
    )


def _curve_profile(hysteresis_k: str = "1") -> CycleProfile:
    """The plan's example curve: -10 C -> 1200/600, 0 C -> 600/1200, 15 C -> 0/1800."""
    return CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal(hysteresis_k),
        curve_points=(
            CurvePoint(Decimal("-10"), 1200, 600),
            CurvePoint(Decimal("0"), 600, 1200),
            CurvePoint(Decimal("15"), 0, 1800),
        ),
    )


def _no_source() -> OutdoorSample:
    return OutdoorSample(value_c=None, measured_at=None, usable=False)


def _usable(value_c: str, measured_at: datetime = T0) -> OutdoorSample:
    return OutdoorSample(value_c=Decimal(value_c), measured_at=measured_at, usable=True)


def _cycle(
    now: datetime,
    profile: CycleProfile,
    outdoor: OutdoorSample,
    *,
    min_on_seconds: int = 60,
    min_off_seconds: int = 60,
    stale_state_seconds: int = 7200,
    prior: PriorPhaseHint | None = None,
) -> CycleInput:
    return CycleInput(
        now=now,
        profile=profile,
        outdoor=outdoor,
        min_on_seconds=min_on_seconds,
        min_off_seconds=min_off_seconds,
        stale_state_seconds=stale_state_seconds,
        prior=prior,
    )


# --- 1. Hand-derived fixed-cycle timeline (no outdoor source: forced Festtakt) ------


def test_fixed_cycle_first_entry_begins_with_off_phase() -> None:
    out = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    assert out.decision.heating_requested is False
    assert out.decision.reason_code == REASON_ENTRY
    assert out.state.phase == PHASE_OFF
    assert out.state.source == SOURCE_FIXED
    assert out.state.phase_started_at == T0
    assert out.state.phase_deadline == T0 + timedelta(seconds=1200)


def test_fixed_cycle_multiple_pairs_hand_derived_timeline() -> None:
    """600s on / 1200s off, walked pair by pair from a fresh entry.

    t=0 enter -> off until 1200
    t=1200 -> on until 1800
    t=1800 -> off until 3000
    t=3000 -> on until 3600
    """
    profile = _fixed_profile()
    source = _no_source()

    out = advance(None, _cycle(T0, profile, source))
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_deadline == T0 + timedelta(seconds=1200)

    t1 = T0 + timedelta(seconds=1200)
    out = advance(out.state, _cycle(t1, profile, source))
    assert out.decision.heating_requested is True
    assert out.decision.reason_code == REASON_TRANSITION
    assert out.state.phase == PHASE_ON
    assert out.state.phase_started_at == t1
    assert out.state.phase_deadline == t1 + timedelta(seconds=600)

    t2 = t1 + timedelta(seconds=600)
    out = advance(out.state, _cycle(t2, profile, source))
    assert out.decision.heating_requested is False
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_deadline == t2 + timedelta(seconds=1200)

    t3 = t2 + timedelta(seconds=1200)
    out = advance(out.state, _cycle(t3, profile, source))
    assert out.decision.heating_requested is True
    assert out.state.phase_deadline == t3 + timedelta(seconds=600)


# --- 2. Exact phase boundaries ------------------------------------------------------


def test_phase_boundary_one_second_before_deadline_still_continues() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    just_before = state.phase_deadline - timedelta(seconds=1)
    out = advance(state, _cycle(just_before, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_CONTINUE
    assert out.decision.heating_requested is False
    assert out.state == state  # unchanged: nothing transitioned yet


def test_phase_boundary_exactly_at_deadline_transitions() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    out = advance(state, _cycle(state.phase_deadline, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_TRANSITION
    assert out.decision.heating_requested is True
    assert out.state.phase == PHASE_ON
    assert out.state.phase_started_at == state.phase_deadline


def test_phase_boundary_one_second_after_deadline_also_transitions_immediately() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    just_after = state.phase_deadline + timedelta(seconds=1)
    out = advance(state, _cycle(just_after, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_TRANSITION
    assert out.decision.heating_requested is True
    # Not backdated to the old deadline -- the new phase starts at the actual `now`.
    assert out.state.phase_started_at == just_after
    assert out.state.phase_deadline == just_after + timedelta(seconds=600)


# --- 3. Late cycle (no catch-up), long pause / clock regression (disturbed) --------


def test_late_cycle_does_not_extend_or_catch_up() -> None:
    """A control-loop tick that runs 500s late still gets the full next phase from
    the moment it actually ran, not a shortened one and not a backdated one."""
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    late = state.phase_deadline + timedelta(seconds=500)
    out = advance(state, _cycle(late, _fixed_profile(), _no_source(), stale_state_seconds=7200))
    assert out.state.phase == PHASE_ON
    assert out.state.phase_started_at == late
    assert out.state.phase_deadline == late + timedelta(seconds=600)


def test_long_pause_beyond_stale_threshold_forces_disturbed_off_restart() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    way_late = state.phase_deadline + timedelta(seconds=10_000)
    out = advance(
        state,
        _cycle(way_late, _fixed_profile(), _no_source(), stale_state_seconds=7200),
    )
    assert out.decision.reason_code == REASON_DISTURBED
    assert out.decision.heating_requested is False
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_started_at == way_late
    # Full profile off-duration, no credit for anything that happened during the gap.
    assert out.state.phase_deadline == way_late + timedelta(seconds=1200)


def test_clock_regression_forces_disturbed_off_restart() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    # Simulate a system clock jumping backwards past the recorded phase start.
    jumped_back = state.phase_started_at - timedelta(seconds=60)
    out = advance(state, _cycle(jumped_back, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_DISTURBED
    assert out.decision.heating_requested is False
    assert out.state.phase_started_at == jumped_back
    assert out.state.phase_deadline == jumped_back + timedelta(seconds=1200)


def test_now_exactly_at_phase_started_at_is_not_disturbed() -> None:
    """`now == phase_started_at` (e.g. two ticks landing on the same timestamp) is
    not a clock regression -- only `now` strictly before it is."""
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    out = advance(state, _cycle(state.phase_started_at, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_CONTINUE
    assert out.state == state


def test_overdue_exactly_at_stale_threshold_is_not_yet_disturbed() -> None:
    """The gap must *exceed* `stale_state_seconds` to count as disturbed -- landing
    exactly on the threshold is still the ordinary late-cycle case."""
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    exactly_at_threshold = state.phase_deadline + timedelta(seconds=7200)
    out = advance(
        state,
        _cycle(exactly_at_threshold, _fixed_profile(), _no_source(), stale_state_seconds=7200),
    )
    assert out.decision.reason_code == REASON_TRANSITION
    assert out.state.phase == PHASE_ON
    assert out.state.phase_started_at == exactly_at_threshold


def test_restart_with_trustworthy_state_continues_same_episode_no_new_on() -> None:
    """A process restart that reloads a still-fresh, plausible state must not start
    a fresh Ein-Phase -- it simply continues where it left off."""
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    reload_at = T0 + timedelta(seconds=300)  # well before the 1200s off deadline
    out = advance(state, _cycle(reload_at, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_CONTINUE
    assert out.decision.heating_requested is False
    assert out.state.phase == PHASE_OFF


# --- 4. Curve interpolation ----------------------------------------------------------


def test_curve_endpoint_minus_10() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("-10")))
    assert out.state.source == SOURCE_CURVE
    assert out.state.off_seconds == 600
    assert out.state.on_seconds == 1200


def test_curve_endpoint_zero() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("0")))
    assert out.state.off_seconds == 1200
    assert out.state.on_seconds == 600


def test_curve_endpoint_15_is_a_real_off_point() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("15")))
    assert out.state.on_seconds == 0
    assert out.state.off_seconds == 1800
    assert out.decision.heating_requested is False
    assert out.decision.reason_code == REASON_ENTRY
    assert out.state.warm_locked is True


def test_curve_below_lower_bound_clamps_to_lower_endpoint() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("-25")))
    assert out.state.on_seconds == 1200
    assert out.state.off_seconds == 600


def test_curve_above_upper_bound_clamps_to_upper_endpoint() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("30")))
    assert out.state.on_seconds == 0
    assert out.state.off_seconds == 1800


def test_curve_midpoint_minus_5_gives_900_900() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("-5")))
    assert out.state.on_seconds == 900
    assert out.state.off_seconds == 900
    assert out.decision.reason_code == REASON_ENTRY
    assert "kennlinie" in out.decision.reason.lower() or "-5" in out.decision.reason


def test_curve_repeating_fraction_rounds_with_exact_decimal_arithmetic() -> None:
    """A 1/3 split (outdoor 1 of a 0..3 span) forces a repeating decimal fraction.

    Hand computation in exact rational arithmetic:
        fraction = 1/3
        on  = 100 + 1/3 * (0 - 100)   = 66.6666...  -> rounds to 67
        off = 200 + 1/3 * (900 - 200) = 433.3333...  -> rounds to 433
    A naive float(Decimal(...)) cast anywhere on this path would risk drifting by a
    few ULP before rounding; this pins the exact, reproducible Decimal result.
    """
    profile = CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(
            CurvePoint(Decimal("0"), 100, 200),
            CurvePoint(Decimal("3"), 0, 900),
        ),
    )
    out = advance(None, _cycle(T0, profile, _usable("1"), min_on_seconds=1, min_off_seconds=1))
    assert out.state.on_seconds == 67
    assert out.state.off_seconds == 433


def test_curve_half_up_rounding_boundary_rounds_up() -> None:
    profile = CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(
            CurvePoint(Decimal("0"), 100, 200),
            CurvePoint(Decimal("2"), 101, 200),
        ),
    )
    out = advance(None, _cycle(T0, profile, _usable("1"), min_on_seconds=1, min_off_seconds=1))
    # 100 + 0.5 * 1 = 100.5 -> rounds up to 101, not truncated down to 100.
    assert out.state.on_seconds == 101


def test_missing_outdoor_source_falls_back_to_fixed_cycle() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _no_source()))
    assert out.state.source == SOURCE_FIXED
    assert out.state.on_seconds == profile.fixed_on_seconds
    assert out.state.off_seconds == profile.fixed_off_seconds


def test_stale_outdoor_source_falls_back_to_fixed_cycle() -> None:
    profile = _curve_profile()
    stale = OutdoorSample(value_c=Decimal("-5"), measured_at=T0, usable=False)
    out = advance(None, _cycle(T0, profile, stale))
    assert out.state.source == SOURCE_FIXED


# --- 5. Source switch only at pair boundary -----------------------------------------


def test_source_switch_waits_for_next_pair_boundary() -> None:
    """Outdoor source becomes unusable mid-Ein-phase: the running phase keeps its
    pinned curve values; only the next pair boundary (the following Aus->resolve
    step) switches to Festtakt."""
    profile = _curve_profile()
    # Enter at 0 C: on=600, off=1200, curve-sourced -- credit the full Aus-Dauer so
    # entry's Aus-phase is already due, then tick once more (same `now`) to reach
    # the Ein-phase cleanly for this test's timeline.
    entry = advance(
        None,
        _cycle(T0, profile, _usable("0"), prior=PriorPhaseHint(on=False, elapsed_seconds=1200)),
    )
    assert entry.state.phase == PHASE_OFF
    assert entry.state.phase_deadline <= T0
    entry = advance(entry.state, _cycle(T0, profile, _usable("0")))
    assert entry.state.phase == PHASE_ON
    assert entry.state.source == SOURCE_CURVE
    on_deadline = entry.state.phase_deadline

    # Outdoor drops out mid-phase -- must not affect the running phase at all.
    mid_phase = entry.state.phase_started_at + timedelta(seconds=100)
    out = advance(entry.state, _cycle(mid_phase, profile, _no_source()))
    assert out.decision.reason_code == REASON_CONTINUE
    assert out.state.source == SOURCE_CURVE
    assert out.state.phase_deadline == on_deadline

    # Phase ends (Ein -> Aus): still the same pair, still curve-pinned values.
    out = advance(out.state, _cycle(on_deadline, profile, _no_source()))
    assert out.state.phase == PHASE_OFF
    assert out.state.source == SOURCE_CURVE
    off_deadline = out.state.phase_deadline

    # Next pair boundary (Aus ends): outdoor is still unusable -> switches to Festtakt now.
    out = advance(out.state, _cycle(off_deadline, profile, _no_source()))
    assert out.state.source == SOURCE_FIXED
    assert out.state.on_seconds == profile.fixed_on_seconds


# --- 6. Warm-restart hysteresis band -------------------------------------------------


def test_warm_lock_engages_at_threshold_and_suppresses_ein() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("15")))
    assert out.state.warm_locked is True
    assert out.decision.heating_requested is False


def test_warm_lock_engages_exactly_at_the_threshold_not_only_strictly_above() -> None:
    """15 C is *the* warm point, not just "above" it -- `>=`, not `>`. Distinguishing
    case: enter exactly at the threshold, then drop to a value that is below the
    threshold but still inside the hysteresis band. If entry had not engaged the
    lock (a `>` bug), that second pair would incorrectly resume cycling instead of
    staying locked."""
    profile = _curve_profile()
    entry = advance(None, _cycle(T0, profile, _usable("15")))
    assert entry.state.warm_locked is True
    within_band = advance(entry.state, _cycle(entry.state.phase_deadline, profile, _usable("14.5")))
    assert within_band.state.warm_locked is True
    assert within_band.decision.heating_requested is False


def test_warm_lock_holds_within_hysteresis_band() -> None:
    """14.5 C is below the 15 C warm point but still inside the 1K hysteresis band
    (threshold - 1K = 14): must stay locked, not resume cycling yet."""
    profile = _curve_profile()
    locked_state = advance(None, _cycle(T0, profile, _usable("15"))).state
    off_deadline = locked_state.phase_deadline
    out = advance(locked_state, _cycle(off_deadline, profile, _usable("14.5")))
    assert out.state.warm_locked is True
    assert out.decision.heating_requested is False
    assert out.decision.reason_code == REASON_WARM_LOCK


def test_warm_lock_holds_exactly_at_the_hysteresis_boundary() -> None:
    """threshold - hysteresis (15 - 1 = 14 C exactly) must still be locked -- the
    concept says "unter" (strictly under) the band's lower edge releases it, not
    "at or under"."""
    profile = _curve_profile()
    locked_state = advance(None, _cycle(T0, profile, _usable("15"))).state
    out = advance(locked_state, _cycle(locked_state.phase_deadline, profile, _usable("14")))
    assert out.state.warm_locked is True
    assert out.decision.heating_requested is False


def test_warm_lock_releases_below_hysteresis_band() -> None:
    """Below threshold - hysteresis (< 14 C): the lock releases and normal
    interpolated cycling resumes at the next pair boundary."""
    profile = _curve_profile()
    locked_state = advance(None, _cycle(T0, profile, _usable("15"))).state
    off_deadline = locked_state.phase_deadline
    out = advance(locked_state, _cycle(off_deadline, profile, _usable("10")))
    assert out.state.warm_locked is False
    assert out.decision.heating_requested is True
    assert out.state.on_seconds > 0


def test_warm_lock_does_not_abort_an_already_running_on_phase() -> None:
    """Reaching the warm point mid-Ein-phase must not cut that phase short -- it
    only stops the *next* Ein-Anforderung."""
    profile = _curve_profile()
    entry = advance(
        None,
        _cycle(T0, profile, _usable("0"), prior=PriorPhaseHint(on=False, elapsed_seconds=1200)),
    )
    entry = advance(entry.state, _cycle(T0, profile, _usable("0")))
    assert entry.state.phase == PHASE_ON
    on_deadline = entry.state.phase_deadline
    mid_phase = entry.state.phase_started_at + timedelta(seconds=100)
    out = advance(entry.state, _cycle(mid_phase, profile, _usable("15")))
    assert out.decision.reason_code == REASON_CONTINUE
    assert out.decision.heating_requested is True
    assert out.state.phase_deadline == on_deadline


# --- 7. Minimum-duration clamping -----------------------------------------------------


def test_short_interpolated_on_phase_is_clamped_to_zone_minimum() -> None:
    profile = CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(
            CurvePoint(Decimal("10"), 30, 1800),
            CurvePoint(Decimal("15"), 0, 1800),
        ),
    )
    out = advance(
        None,
        _cycle(T0, profile, _usable("10"), min_on_seconds=120, min_off_seconds=60),
    )
    assert out.state.on_seconds == 120  # clamped up from the interpolated 30s


def test_zero_on_seconds_is_never_promoted_by_minimum_duration() -> None:
    profile = _curve_profile()
    out = advance(
        None,
        _cycle(T0, profile, _usable("15"), min_on_seconds=120, min_off_seconds=60),
    )
    assert out.state.on_seconds == 0
    assert out.decision.heating_requested is False


def test_off_seconds_is_clamped_to_zone_minimum() -> None:
    profile = _fixed_profile(on_seconds=600, off_seconds=30)
    out = advance(None, _cycle(T0, profile, _no_source(), min_off_seconds=90))
    assert out.state.off_seconds == 90


# --- 8. First-entry credit for a previously known actuator state --------------------


def test_entry_credits_remaining_minimum_on_of_an_already_on_relay() -> None:
    profile = _fixed_profile()
    out = advance(
        None,
        _cycle(
            T0,
            profile,
            _no_source(),
            min_on_seconds=600,
            prior=PriorPhaseHint(on=True, elapsed_seconds=400),
        ),
    )
    assert out.decision.heating_requested is True
    assert out.decision.reason_code == REASON_ENTRY
    assert out.state.phase == PHASE_ON
    # 600s minimum, 400s already elapsed -> 200s remain.
    assert out.state.phase_deadline == T0 + timedelta(seconds=200)


def test_entry_with_already_on_relay_records_the_resolved_pair_on_duration() -> None:
    """`CycleState.on_seconds` is documented as "this pair's Ein-Dauer" -- the
    resolved Festtakt/Kennlinie value, not the (possibly much shorter) zone
    minimum used only to size the credited remainder. A caller reading this field
    back (e.g. for the explanation text of a later `REASON_CONTINUE` tick during
    this same credited phase) must see the real pair duration, not the minimum."""
    profile = _fixed_profile(on_seconds=600, off_seconds=1200)
    out = advance(
        None,
        _cycle(
            T0,
            profile,
            _no_source(),
            min_on_seconds=60,  # deliberately much smaller than the profile's 600s
            prior=PriorPhaseHint(on=True, elapsed_seconds=10),
        ),
    )
    assert out.state.on_seconds == 600
    assert out.state.off_seconds == 1200
    # The credited remainder itself still only honours the zone minimum (60s), not
    # the full resolved pair duration -- that part of the behaviour is unaffected.
    assert out.state.phase_deadline == T0 + timedelta(seconds=50)


def test_entry_with_already_on_relay_ignores_elapsed_beyond_minimum() -> None:
    """If the relay was already on for longer than the minimum, the credited
    remainder does not go negative -- it transitions on the very next tick."""
    profile = _fixed_profile()
    out = advance(
        None,
        _cycle(
            T0,
            profile,
            _no_source(),
            min_on_seconds=600,
            prior=PriorPhaseHint(on=True, elapsed_seconds=900),
        ),
    )
    assert out.state.phase == PHASE_ON
    assert out.state.phase_deadline <= T0


def test_entry_credits_already_elapsed_off_time() -> None:
    profile = _fixed_profile(off_seconds=1200)
    out = advance(
        None,
        _cycle(T0, profile, _no_source(), prior=PriorPhaseHint(on=False, elapsed_seconds=1000)),
    )
    assert out.state.phase == PHASE_OFF
    # 1200s off required, 1000s already elapsed -> 200s remain.
    assert out.state.phase_deadline == T0 + timedelta(seconds=200)


def test_entry_with_unknown_prior_state_sends_off_and_waits_full_duration() -> None:
    profile = _fixed_profile(off_seconds=1200)
    out = advance(None, _cycle(T0, profile, _no_source(), prior=None))
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_started_at == T0
    assert out.state.phase_deadline == T0 + timedelta(seconds=1200)
    assert out.decision.heating_requested is False


def test_disturbed_restart_ignores_any_prior_credit() -> None:
    profile = _fixed_profile()
    state = advance(None, _cycle(T0, profile, _no_source())).state
    way_late = state.phase_deadline + timedelta(seconds=10_000)
    out = advance(
        state,
        _cycle(
            way_late,
            profile,
            _no_source(),
            stale_state_seconds=7200,
            prior=PriorPhaseHint(on=True, elapsed_seconds=590),
        ),
    )
    assert out.decision.reason_code == REASON_DISTURBED
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_deadline == way_late + timedelta(seconds=1200)


# --- Determinism / no hidden state ---------------------------------------------------


def test_advance_is_pure_and_deterministic() -> None:
    profile = _curve_profile()
    cycle = _cycle(T0, profile, _usable("-5"))
    first = advance(None, cycle)
    second = advance(None, cycle)
    assert first == second


def test_reason_text_names_takt_source_and_outdoor_temperature() -> None:
    profile = _curve_profile()
    out = advance(None, _cycle(T0, profile, _usable("-5")))
    text = out.decision.reason.lower()
    assert "-5" in out.decision.reason
    assert "kennlinie" in text or "outdoor" in text or "außentemperatur" in text


def test_state_records_chosen_source() -> None:
    fixed_out = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    assert fixed_out.state.source == SOURCE_FIXED
    curve_out = advance(None, _cycle(T0, _curve_profile(), _usable("-5")))
    assert curve_out.state.source == SOURCE_CURVE


def test_state_is_immutable_dataclass() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source())).state
    assert isinstance(state, CycleState)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.phase = PHASE_ON  # type: ignore[misc]


def test_entry_credits_never_more_than_the_off_duration() -> None:
    """A relay that has been off for hours is credited the Aus-Dauer, not the hours:
    the phase is then due exactly now, not overdue by hours (which the next call
    would misread as a stale, disturbed state)."""
    profile = _fixed_profile(off_seconds=1200)
    out = advance(
        None,
        _cycle(T0, profile, _no_source(), prior=PriorPhaseHint(on=False, elapsed_seconds=90000)),
    )
    assert out.state.phase == PHASE_OFF
    assert out.state.phase_started_at == T0 - timedelta(seconds=1200)
    assert out.state.phase_deadline == T0


# --- 9. Mutationslauf (Auftrag 10): Begruendungstexte, Randwerte, Unveraenderlichkeit --


def test_all_cycle_types_are_immutable() -> None:
    state = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    objs = [
        CurvePoint(Decimal("0"), 1, 1),
        _fixed_profile(),
        _no_source(),
        PriorPhaseHint(on=True, elapsed_seconds=1),
        state.state,
        _cycle(T0, _fixed_profile(), _no_source()),
        state.decision,
        state,
    ]
    for obj in objs:
        field = dataclasses.fields(obj)[0].name  # type: ignore[arg-type]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)


def test_cycle_state_is_not_warm_locked_by_default() -> None:
    state = CycleState(PHASE_OFF, T0, T0, SOURCE_FIXED, 1, 1)
    assert state.warm_locked is False


def test_fixed_cycle_never_carries_a_warm_lock() -> None:
    out = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    assert out.state.warm_locked is False
    boundary = advance(out.state, _cycle(out.state.phase_deadline, _fixed_profile(), _no_source()))
    assert boundary.state.warm_locked is False


def test_curve_above_the_last_point_takes_the_last_points_pair_not_another_one() -> None:
    # Letzter Stuetzpunkt mit Ein > 0 (kein Warm-Aus-Punkt), Aussentemperatur darueber:
    # exakt das Wertepaar des letzten Punkts, nicht das eines anderen Index.
    profile = CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(
            CurvePoint(Decimal("-10"), 1200, 600),
            CurvePoint(Decimal("0"), 900, 700),
            CurvePoint(Decimal("15"), 300, 1800),
        ),
    )
    out = advance(None, _cycle(T0, profile, _usable("20")))
    assert (out.state.on_seconds, out.state.off_seconds) == (300, 1800)


def test_curve_on_duration_of_one_second_is_still_raised_to_the_minimum() -> None:
    profile = CycleProfile(
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(CurvePoint(Decimal("-10"), 1, 600), CurvePoint(Decimal("0"), 1, 600)),
    )
    out = advance(None, _cycle(T0, profile, _usable("-5"), min_on_seconds=60))
    assert out.state.on_seconds == 60


def test_entry_reason_text_fixed_cycle() -> None:
    out = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    assert out.decision.reason == (
        "Notbetriebstakt startet: Aus-Phase für 1200 s, Taktquelle Festtakt, "
        "keine Außentemperaturmessung. Takt: 600 s Ein / 1200 s Aus."
    )


def test_disturbed_restart_reason_text() -> None:
    first = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    later = T0 + timedelta(days=1)
    out = advance(first.state, _cycle(later, _fixed_profile(), _no_source()))
    assert out.decision.reason_code == REASON_DISTURBED
    assert out.decision.reason == (
        "Störung erkannt, Notbetriebstakt neu gestartet: Aus-Phase für 1200 s, "
        "Taktquelle Festtakt, keine Außentemperaturmessung. Takt: 600 s Ein / 1200 s Aus."
    )


@pytest.mark.parametrize(("elapsed", "remaining"), [(180, 120), (300, 0), (400, 0)])
def test_already_on_entry_reason_states_remaining_minimum_on(elapsed: int, remaining: int) -> None:
    out = advance(
        None,
        _cycle(
            T0,
            _fixed_profile(),
            _no_source(),
            min_on_seconds=300,
            prior=PriorPhaseHint(on=True, elapsed_seconds=elapsed),
        ),
    )
    assert out.decision.heating_requested is True
    assert out.decision.reason == (
        "Notbetriebstakt übernimmt eine bereits laufende Ein-Phase: verbleibende "
        f"Mindest-Ein-Dauer {remaining} s werden gehalten, danach Aus nach Takt (Festtakt)."
    )


def test_already_on_entry_reason_names_the_curve_as_source() -> None:
    out = advance(
        None,
        _cycle(
            T0,
            _curve_profile(),
            _usable("-5"),
            min_on_seconds=300,
            prior=PriorPhaseHint(on=True, elapsed_seconds=100),
        ),
    )
    assert out.state.source == SOURCE_CURVE
    assert out.decision.reason.endswith("danach Aus nach Takt (Außenkennlinie).")


def test_continue_reason_texts_for_off_and_on_phase_and_outdoor_judgement() -> None:
    off = advance(None, _cycle(T0, _fixed_profile(), _no_source()))
    cont_off = advance(off.state, _cycle(T0 + timedelta(seconds=1), _fixed_profile(), _no_source()))
    assert cont_off.decision.reason == (
        "Notbetriebstakt läuft weiter, aktuelle Aus-Phase für 1200 s, Taktquelle Festtakt, "
        "keine Außentemperaturmessung. Takt: 600 s Ein / 1200 s Aus."
    )
    on = advance(
        None,
        _cycle(
            T0,
            _fixed_profile(),
            _no_source(),
            prior=PriorPhaseHint(on=True, elapsed_seconds=0),
        ),
    )
    unusable = OutdoorSample(Decimal("-5"), T0, usable=False)
    cont_on = advance(on.state, _cycle(T0 + timedelta(seconds=1), _fixed_profile(), unusable))
    assert cont_on.decision.reason == (
        "Notbetriebstakt läuft weiter, aktuelle Ein-Phase für 600 s, Taktquelle Festtakt, "
        "Außentemperatur -5,0 °C (unbrauchbar). Takt: 600 s Ein / 1200 s Aus."
    )
    curve = advance(None, _cycle(T0, _curve_profile(), _usable("-5")))
    cont_curve = advance(
        curve.state, _cycle(T0 + timedelta(seconds=1), _curve_profile(), _usable("-5"))
    )
    assert cont_curve.decision.reason == (
        "Notbetriebstakt läuft weiter, aktuelle Aus-Phase für 900 s, Taktquelle Außenkennlinie, "
        "Außentemperatur -5,0 °C (brauchbar). Takt: 900 s Ein / 900 s Aus."
    )


def test_transition_reason_texts_on_to_off_and_off_to_on() -> None:
    on = advance(
        None,
        _cycle(
            T0,
            _fixed_profile(),
            _no_source(),
            prior=PriorPhaseHint(on=True, elapsed_seconds=0),
        ),
    )
    to_off = advance(on.state, _cycle(on.state.phase_deadline, _fixed_profile(), _no_source()))
    assert to_off.decision.reason_code == REASON_TRANSITION
    assert to_off.decision.reason == (
        "Notbetriebstakt wechselt auf Aus-Phase für 1200 s, Taktquelle Festtakt, "
        "keine Außentemperaturmessung. Takt: 600 s Ein / 1200 s Aus."
    )
    to_on = advance(
        to_off.state, _cycle(to_off.state.phase_deadline, _fixed_profile(), _no_source())
    )
    assert to_on.decision.heating_requested is True
    assert to_on.decision.reason == (
        "Notbetriebstakt wechselt auf Ein-Phase für 600 s, Taktquelle Festtakt, "
        "keine Außentemperaturmessung. Takt: 600 s Ein / 1200 s Aus."
    )


def test_warm_lock_reason_text() -> None:
    first = advance(None, _cycle(T0, _curve_profile(), _usable("20")))
    out = advance(first.state, _cycle(first.state.phase_deadline, _curve_profile(), _usable("20")))
    assert out.decision.reason_code == REASON_WARM_LOCK
    assert out.decision.heating_requested is False
    assert out.decision.reason == (
        "Wiederanlaufsperre hält Aus-Phase für 1800 s, Taktquelle Außenkennlinie, "
        "Außentemperatur 20,0 °C (brauchbar). Takt: 0 s Ein / 1800 s Aus."
    )
