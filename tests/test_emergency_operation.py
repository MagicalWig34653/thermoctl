"""Tests for the pure zone emergency-operation state machine
(`thermoctl.domain.emergency_operation`).

Covers Auftrag 5b of `lokal/plaene/0.11.0-notbetrieb.md`: the zone-level stage
automaton (`normal -> ersatzquelle -> notbetrieb -> rueckkehrpruefung -> normal`),
episode begin/end, recovery counting, the once-per-episode handover signal and the
PI-neutralisation signal. Nothing here is wired to the DB, the control loop or the
publisher yet -- that is Auftrag 7.

Layout:

1. every stage transition from the plan's diagram (1.2),
2. recovery counting: exact 2 samples/60s, one short of either bound,
3. no double-counting the same measurement timestamp,
4. fallback during rueckkehrpruefung continues the same episode,
5. a device swap while tracking the replacement restarts the count,
6. "never measured since start" enters notbetrieb immediately,
7. enabled=False forces normal behaviour,
8. the handover-due signal fires exactly once per episode, across cycles and a
   rueckkehrpruefung fallback,
9. the PI-neutralisation signal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from thermoctl.domain.emergency_operation import (
    KIND_WANDFUEHLER,
    REASON_DEAKTIVIERT,
    REASON_ERSATZQUELLE_EINTRITT,
    REASON_NOTBETRIEB_EINTRITT,
    REASON_RUECKFALL,
    REASON_RUECKKEHR_ABGESCHLOSSEN,
    REASON_RUECKKEHR_NEUSTART,
    STAGE_ERSATZQUELLE,
    STAGE_NORMAL,
    STAGE_NOTBETRIEB,
    STAGE_RUECKKEHRPRUEFUNG,
    TRIGGER_ALLE_QUELLEN,
    TRIGGER_WANDFUEHLER,
    SourceReading,
    StageInput,
    ZoneEmergencyState,
    advance,
)

T0 = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)

USABLE_WALL = SourceReading(usable=True, measured_at=T0)
UNUSABLE_WALL = SourceReading(usable=False, measured_at=None)
USABLE_REPLACEMENT = SourceReading(
    usable=True, measured_at=T0, device_id=7, device_name="TRV Wohnzimmer"
)
UNUSABLE_REPLACEMENT = SourceReading(usable=False, measured_at=None)


def _input(
    now: datetime,
    *,
    wall: SourceReading = UNUSABLE_WALL,
    replacement: SourceReading = UNUSABLE_REPLACEMENT,
    enabled: bool = True,
    recovery_seconds: int = 60,
    recovery_samples: int = 2,
) -> StageInput:
    return StageInput(
        now=now,
        enabled=enabled,
        wall_probe=wall,
        replacement=replacement,
        recovery_seconds=recovery_seconds,
        recovery_samples=recovery_samples,
    )


# --- 1. Every transition of the diagram -------------------------------------------


def test_normal_stays_normal_while_wall_probe_usable() -> None:
    out = advance(None, _input(T0, wall=USABLE_WALL))
    assert out.state.stage == STAGE_NORMAL
    assert not out.events.episode_started
    assert not out.events.pi_neutralized


def test_wall_probe_failure_with_usable_replacement_enters_ersatzquelle() -> None:
    out = advance(None, _input(T0, replacement=USABLE_REPLACEMENT))
    assert out.state.stage == STAGE_ERSATZQUELLE
    assert out.events.episode_started
    assert out.events.trigger_kind == TRIGGER_WANDFUEHLER
    assert out.events.reason_code == REASON_ERSATZQUELLE_EINTRITT
    assert out.events.pi_neutralized
    assert not out.events.handover_due


def test_ersatzquelle_escalates_to_notbetrieb_once_replacement_also_fails() -> None:
    entered = advance(None, _input(T0, replacement=USABLE_REPLACEMENT))
    escalated = advance(entered.state, _input(T0 + timedelta(seconds=5)))
    assert escalated.state.stage == STAGE_NOTBETRIEB
    assert escalated.state.episode_open
    assert escalated.events.handover_due
    assert not escalated.events.episode_started  # same episode, not a new one


def test_notbetrieb_moves_to_rueckkehrpruefung_once_a_source_returns() -> None:
    notbetrieb = advance(None, _input(T0)).state
    assert notbetrieb.stage == STAGE_NOTBETRIEB
    returned = advance(
        notbetrieb, _input(T0 + timedelta(seconds=10), wall=USABLE_WALL)
    )
    assert returned.state.stage == STAGE_RUECKKEHRPRUEFUNG
    assert returned.state.tracked_kind == KIND_WANDFUEHLER


def test_wall_probe_takes_priority_when_both_sources_return_in_the_same_cycle() -> None:
    """Plan 1.3: the wall probe is preferred whenever both recover together."""
    notbetrieb = advance(None, _input(T0)).state
    returned = advance(
        notbetrieb,
        _input(
            T0 + timedelta(seconds=10),
            wall=USABLE_WALL,
            replacement=USABLE_REPLACEMENT,
        ),
    )
    assert returned.state.stage == STAGE_RUECKKEHRPRUEFUNG
    assert returned.state.tracked_kind == KIND_WANDFUEHLER
    assert returned.state.tracked_device_id is None


def test_recovery_completed_against_wall_probe_returns_to_normal() -> None:
    notbetrieb = advance(None, _input(T0)).state
    started = advance(
        notbetrieb, _input(T0 + timedelta(seconds=1), wall=USABLE_WALL)
    ).state
    second_reading = SourceReading(usable=True, measured_at=T0 + timedelta(seconds=61))
    finished = advance(
        started,
        _input(T0 + timedelta(seconds=61), wall=second_reading),
    )
    assert finished.state.stage == STAGE_NORMAL
    assert not finished.state.episode_open
    assert finished.events.episode_ended
    assert not finished.events.pi_neutralized


def test_recovery_completed_against_replacement_returns_to_ersatzquelle_not_normal() -> None:
    notbetrieb = advance(None, _input(T0)).state
    started = advance(
        notbetrieb, _input(T0 + timedelta(seconds=1), replacement=USABLE_REPLACEMENT)
    ).state
    second = SourceReading(
        usable=True,
        measured_at=T0 + timedelta(seconds=61),
        device_id=7,
        device_name="TRV Wohnzimmer",
    )
    finished = advance(
        started,
        _input(T0 + timedelta(seconds=61), replacement=second),
    )
    assert finished.state.stage == STAGE_ERSATZQUELLE
    assert not finished.events.episode_ended
    assert finished.events.pi_neutralized
    assert finished.state.episode_open


def test_ersatzquelle_holds_while_replacement_stays_usable_and_wall_does_not() -> None:
    entered = advance(None, _input(T0, replacement=USABLE_REPLACEMENT)).state
    held = advance(
        entered, _input(T0 + timedelta(seconds=30), replacement=USABLE_REPLACEMENT)
    )
    assert held.state.stage == STAGE_ERSATZQUELLE
    assert held.state.episode_open
    assert held.events.pi_neutralized
    assert not held.events.episode_ended


def test_ersatzquelle_recovers_to_normal_once_wall_probe_returns() -> None:
    entered = advance(None, _input(T0, replacement=USABLE_REPLACEMENT)).state
    first = advance(
        entered, _input(T0 + timedelta(seconds=1), wall=USABLE_WALL)
    )
    assert first.state.stage == STAGE_ERSATZQUELLE
    assert first.state.recovery_sample_count == 1

    second_reading = SourceReading(usable=True, measured_at=T0 + timedelta(seconds=61))
    finished = advance(
        first.state, _input(T0 + timedelta(seconds=61), wall=second_reading)
    )
    assert finished.state.stage == STAGE_NORMAL
    assert finished.events.episode_ended
    assert not finished.events.pi_neutralized


# --- 2. Recovery counting: exact bounds and just under ----------------------------


def test_recovery_needs_exactly_two_samples_and_sixty_seconds() -> None:
    notbetrieb = advance(None, _input(T0)).state
    first = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    assert first.stage == STAGE_RUECKKEHRPRUEFUNG
    assert first.recovery_sample_count == 1

    second_reading = SourceReading(usable=True, measured_at=T0 + timedelta(seconds=60))
    out = advance(first, _input(T0 + timedelta(seconds=60), wall=second_reading))
    assert out.state.stage == STAGE_NORMAL
    assert out.events.reason_code == REASON_RUECKKEHR_ABGESCHLOSSEN


def test_recovery_with_only_one_sample_after_sixty_seconds_does_not_complete() -> None:
    notbetrieb = advance(None, _input(T0)).state
    first = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    # Same measurement timestamp seen again 60s later -- still only one distinct sample.
    out = advance(first, _input(T0 + timedelta(seconds=60), wall=USABLE_WALL))
    assert out.state.stage == STAGE_RUECKKEHRPRUEFUNG
    assert out.state.recovery_sample_count == 1


def test_recovery_at_fifty_nine_seconds_does_not_complete_even_with_two_samples() -> None:
    notbetrieb = advance(None, _input(T0)).state
    first = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    second_reading = SourceReading(usable=True, measured_at=T0 + timedelta(seconds=59))
    out = advance(first, _input(T0 + timedelta(seconds=59), wall=second_reading))
    assert out.state.stage == STAGE_RUECKKEHRPRUEFUNG
    assert out.state.recovery_sample_count == 2


def test_recovery_at_sixty_one_seconds_with_two_samples_completes() -> None:
    notbetrieb = advance(None, _input(T0)).state
    first = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    second_reading = SourceReading(usable=True, measured_at=T0 + timedelta(seconds=61))
    out = advance(first, _input(T0 + timedelta(seconds=61), wall=second_reading))
    assert out.state.stage == STAGE_NORMAL


# --- 3. Same measurement timestamp never counted twice ----------------------------


def test_same_measurement_timestamp_across_many_cycles_counts_once() -> None:
    notbetrieb = advance(None, _input(T0)).state
    state = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    for offset in (10, 20, 30, 40, 50):
        state = advance(
            state, _input(T0 + timedelta(seconds=offset), wall=USABLE_WALL)
        ).state
        assert state.recovery_sample_count == 1
    # 60s elapsed, but only one distinct timestamp ever seen -- must not complete.
    out = advance(state, _input(T0 + timedelta(seconds=61), wall=USABLE_WALL))
    assert out.state.stage == STAGE_RUECKKEHRPRUEFUNG


# --- 4. Fallback during rueckkehrpruefung continues the same episode -------------


def test_fallback_during_rueckkehrpruefung_returns_to_notbetrieb_same_episode() -> None:
    notbetrieb = advance(None, _input(T0)).state
    checking = advance(notbetrieb, _input(T0, wall=USABLE_WALL)).state
    assert checking.stage == STAGE_RUECKKEHRPRUEFUNG

    fallen_back = advance(checking, _input(T0 + timedelta(seconds=5)))
    assert fallen_back.state.stage == STAGE_NOTBETRIEB
    assert fallen_back.state.episode_open
    assert not fallen_back.events.episode_started
    assert not fallen_back.events.episode_ended
    assert fallen_back.events.reason_code == REASON_RUECKFALL

    # And the recovery attempt must have been wiped, not merely paused.
    resumed = advance(
        fallen_back.state, _input(T0 + timedelta(seconds=6), wall=USABLE_WALL)
    ).state
    assert resumed.recovery_sample_count == 1
    assert resumed.stage == STAGE_RUECKKEHRPRUEFUNG


# --- 5. A device swap while tracking the replacement restarts the count ----------


def test_replacement_device_swap_during_rueckkehrpruefung_restarts_count() -> None:
    notbetrieb = advance(None, _input(T0)).state
    device_a = SourceReading(usable=True, measured_at=T0, device_id=7, device_name="TRV A")
    checking = advance(notbetrieb, _input(T0, replacement=device_a)).state
    assert checking.stage == STAGE_RUECKKEHRPRUEFUNG
    assert checking.tracked_device_id == 7
    assert checking.recovery_sample_count == 1

    device_b = SourceReading(
        usable=True, measured_at=T0 + timedelta(seconds=30), device_id=9, device_name="TRV B"
    )
    swapped = advance(checking, _input(T0 + timedelta(seconds=30), replacement=device_b))
    assert swapped.state.stage == STAGE_RUECKKEHRPRUEFUNG
    assert swapped.state.tracked_device_id == 9
    assert swapped.state.recovery_sample_count == 1
    assert swapped.events.reason_code == REASON_RUECKKEHR_NEUSTART

    # A recovery that would have completed against the old timeline must not
    # complete just because 60s have passed since the *original* start -- it
    # restarted against device B.
    still_checking = advance(
        swapped.state,
        _input(
            T0 + timedelta(seconds=65),
            replacement=SourceReading(
                usable=True,
                measured_at=T0 + timedelta(seconds=30),
                device_id=9,
                device_name="TRV B",
            ),
        ),
    )
    assert still_checking.state.stage == STAGE_RUECKKEHRPRUEFUNG


# --- 6. Never measured since start enters notbetrieb immediately -----------------


def test_never_measured_since_start_enters_notbetrieb_immediately() -> None:
    out = advance(None, _input(T0))
    assert out.state.stage == STAGE_NOTBETRIEB
    assert out.events.episode_started
    assert out.events.trigger_kind == TRIGGER_ALLE_QUELLEN
    assert out.events.reason_code == REASON_NOTBETRIEB_EINTRITT
    assert out.events.handover_due


# --- 7. enabled=False always behaves like normal ---------------------------------


def test_disabled_behaves_like_normal_even_mid_episode() -> None:
    notbetrieb = advance(None, _input(T0)).state
    assert notbetrieb.episode_open

    out = advance(notbetrieb, _input(T0 + timedelta(seconds=5), enabled=False))
    assert out.state.stage == STAGE_NORMAL
    assert out.state == ZoneEmergencyState()
    assert out.events.episode_ended
    assert out.events.reason_code == REASON_DEAKTIVIERT
    assert not out.events.pi_neutralized
    assert not out.events.handover_due


def test_disabled_with_no_prior_episode_does_not_claim_one_ended() -> None:
    out = advance(None, _input(T0, enabled=False))
    assert out.state.stage == STAGE_NORMAL
    assert not out.events.episode_ended


# --- 8. Handover-due fires exactly once per episode ------------------------------


def test_handover_due_fires_once_and_not_again_across_cycles() -> None:
    entered = advance(None, _input(T0))
    assert entered.events.handover_due
    still_down = advance(entered.state, _input(T0 + timedelta(seconds=30)))
    assert not still_down.events.handover_due
    still_down_again = advance(still_down.state, _input(T0 + timedelta(seconds=60)))
    assert not still_down_again.events.handover_due


def test_handover_due_does_not_refire_after_fallback_from_rueckkehrpruefung() -> None:
    notbetrieb = advance(None, _input(T0))
    assert notbetrieb.events.handover_due
    checking = advance(notbetrieb.state, _input(T0 + timedelta(seconds=1), wall=USABLE_WALL))
    assert not checking.events.handover_due
    fallen_back = advance(checking.state, _input(T0 + timedelta(seconds=2)))
    assert fallen_back.state.stage == STAGE_NOTBETRIEB
    assert not fallen_back.events.handover_due
    still_down = advance(fallen_back.state, _input(T0 + timedelta(seconds=3)))
    assert not still_down.events.handover_due


def test_handover_due_fires_again_in_a_later_episode() -> None:
    entered = advance(None, _input(T0))
    assert entered.events.handover_due
    # Full recovery: wall probe comes back for the required count/duration.
    checking = advance(entered.state, _input(T0 + timedelta(seconds=1), wall=USABLE_WALL))
    recovered = advance(
        checking.state,
        _input(
            T0 + timedelta(seconds=61),
            wall=SourceReading(usable=True, measured_at=T0 + timedelta(seconds=61)),
        ),
    )
    assert recovered.state.stage == STAGE_NORMAL

    # A brand-new episode later must signal handover again.
    new_episode = advance(recovered.state, _input(T0 + timedelta(seconds=120)))
    assert new_episode.state.stage == STAGE_NOTBETRIEB
    assert new_episode.events.handover_due


def test_handover_due_does_not_refire_when_escalating_from_ersatzquelle() -> None:
    entered = advance(None, _input(T0, replacement=USABLE_REPLACEMENT))
    assert not entered.events.handover_due
    escalated = advance(entered.state, _input(T0 + timedelta(seconds=1)))
    assert escalated.state.stage == STAGE_NOTBETRIEB
    assert escalated.events.handover_due
    still_down = advance(escalated.state, _input(T0 + timedelta(seconds=2)))
    assert not still_down.events.handover_due


def test_handover_due_does_not_refire_on_a_second_escalation_in_the_same_episode() -> None:
    """Kreuzreview finding: `notbetrieb -> rueckkehrpruefung` (via Ersatzquelle)
    `-> ersatzquelle` (flag already set) `->` both sources fail again must not
    signal handover a second time within the same episode. A hard-coded
    `first_time = True` in `_ersatzquelle`'s escalation branch would pass every
    other handover test (they all reach that branch with the flag still
    unset) but must fail here, where it is already set."""
    notbetrieb = advance(None, _input(T0))
    assert notbetrieb.events.handover_due

    checking = advance(
        notbetrieb.state, _input(T0 + timedelta(seconds=1), replacement=USABLE_REPLACEMENT)
    )
    assert checking.state.stage == STAGE_RUECKKEHRPRUEFUNG

    recovered = advance(
        checking.state,
        _input(
            T0 + timedelta(seconds=61),
            replacement=SourceReading(
                usable=True,
                measured_at=T0 + timedelta(seconds=61),
                device_id=7,
                device_name="TRV Wohnzimmer",
            ),
        ),
    )
    assert recovered.state.stage == STAGE_ERSATZQUELLE
    assert recovered.state.handover_due_signalled  # carried over, not reset

    escalated_again = advance(recovered.state, _input(T0 + timedelta(seconds=62)))
    assert escalated_again.state.stage == STAGE_NOTBETRIEB
    assert not escalated_again.events.episode_started
    assert not escalated_again.events.handover_due


# --- 9. PI-neutralisation signal --------------------------------------------------


@pytest.mark.parametrize(
    ("wall", "replacement", "expected_stage"),
    [
        (USABLE_WALL, UNUSABLE_REPLACEMENT, STAGE_NORMAL),
        (UNUSABLE_WALL, USABLE_REPLACEMENT, STAGE_ERSATZQUELLE),
        (UNUSABLE_WALL, UNUSABLE_REPLACEMENT, STAGE_NOTBETRIEB),
    ],
)
def test_pi_neutralized_matches_whether_stage_is_normal(
    wall: SourceReading, replacement: SourceReading, expected_stage: str
) -> None:
    out = advance(None, _input(T0, wall=wall, replacement=replacement))
    assert out.state.stage == expected_stage
    assert out.events.pi_neutralized == (expected_stage != STAGE_NORMAL)
