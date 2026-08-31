"""Tests for the pure control decision `thermoctl.domain.regelung.entscheiden`.

This task is measured by its tests (assignment task 6): each of the six rules
individually, their precedence against one another, the edge cases exactly at
the threshold, and explicitly the defect of the legacy system that `thermoctl`
must not repeat.
"""

from decimal import Decimal

import pytest

from thermoctl.domain.control_loop import (
    REASON_CODE_BLOCKED_MINIMUM_DURATION,
    REASON_CODE_FROST_SENSOR_FAILURE,
    REASON_CODE_HEATING,
    REASON_CODE_NO_SOURCE,
    REASON_CODE_OFF,
    REASON_CODE_UNCHANGED,
    REASON_CODE_VALVE_PROTECTION,
    REASON_CODE_WINDOW_OPEN,
    Situation,
    decide,
)
from thermoctl.domain.zone_settings import ControlParameters


def _parameter(
    *,
    hysteresis_k: Decimal = Decimal("0.5"),
    min_on_seconds: int = 300,
    min_off_seconds: int = 300,
    sensor_timeout_seconds: int = 600,
    temperature_offset_k: Decimal = Decimal("0.0"),
    window_resume_delay_seconds: int = 300,
    solar_setback_max_k: Decimal = Decimal("2.0"),
    valve_protection_enabled: bool = False,
    valve_protection_interval_days: int = 30,
    valve_protection_duration_minutes: int = 10,
) -> ControlParameters:
    return ControlParameters(
        hysteresis_k=hysteresis_k,
        min_on_seconds=min_on_seconds,
        min_off_seconds=min_off_seconds,
        sensor_timeout_seconds=sensor_timeout_seconds,
        temperature_offset_k=temperature_offset_k,
        window_resume_delay_seconds=window_resume_delay_seconds,
        solar_setback_max_k=solar_setback_max_k,
        valve_protection_enabled=valve_protection_enabled,
        valve_protection_interval_days=valve_protection_interval_days,
        valve_protection_duration_minutes=valve_protection_duration_minutes,
    )


def _lage(
    *,
    measured_c: Decimal | None = Decimal("20.0"),
    setpoint_c: Decimal = Decimal("21.0"),
    setpoint_reason: str = "Zeitplan: Modus Tag ab 06:00",
    frost_c: Decimal = Decimal("16.0"),
    operating_mode: str = "auto",
    heating_now: bool = False,
    held_for_s: int | None = 1000,
    window_open: bool = False,
    window_closed_for_s: int | None = 1000,
    sensor_status: str = "ok",
    parameter: ControlParameters | None = None,
    override_active: bool = False,
    valve_protection_due: bool = False,
    valve_protection_active: bool = False,
) -> Situation:
    return Situation(
        measured_c=measured_c,
        setpoint_c=setpoint_c,
        setpoint_reason=setpoint_reason,
        frost_c=frost_c,
        operating_mode=operating_mode,
        heating_now=heating_now,
        held_for_s=held_for_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        sensor_status=sensor_status,
        parameter=parameter or _parameter(),
        override_active=override_active,
        valve_protection_due=valve_protection_due,
        valve_protection_active=valve_protection_active,
    )


def test_rule7_a_due_valve_protection_run_heats_with_an_explicit_reason() -> None:
    decision = decide(_lage(
        measured_c=Decimal("21.0"), setpoint_c=Decimal("16.0"),
        parameter=_parameter(valve_protection_enabled=True, min_off_seconds=0),
        valve_protection_due=True,
    ))
    assert decision.heating is True
    assert decision.reason_code == REASON_CODE_VALVE_PROTECTION
    assert "Ventilschutzlauf" in decision.reason


def test_rule7_a_stale_sensor_never_starts_valve_protection() -> None:
    decision = decide(_lage(
        measured_c=Decimal("21.0"), setpoint_c=Decimal("16.0"),
        sensor_status="veraltet",
        parameter=_parameter(valve_protection_enabled=True, min_off_seconds=0),
        valve_protection_due=True,
    ))

    assert decision.heating is False
    assert decision.reason_code == REASON_CODE_FROST_SENSOR_FAILURE


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"sensor_status": "keine_quelle", "measured_c": None}, REASON_CODE_NO_SOURCE),
        ({"operating_mode": "off"}, REASON_CODE_UNCHANGED),
        ({"window_open": True}, REASON_CODE_WINDOW_OPEN),
        ({"override_active": True}, REASON_CODE_UNCHANGED),
    ],
)
def test_rule7_never_overrides_a_higher_priority_rule(
    changes: dict[str, object], reason_code: str,
) -> None:
    arguments: dict[str, object] = {
        "measured_c": Decimal("21.0"), "setpoint_c": Decimal("16.0"),
        "parameter": _parameter(valve_protection_enabled=True, min_off_seconds=0),
        "valve_protection_due": True,
    }
    arguments.update(changes)
    decision = decide(_lage(**arguments))  # type: ignore[arg-type]
    assert decision.heating is False
    assert decision.reason_code == reason_code


def test_rule7_is_off_by_default() -> None:
    decision = decide(_lage(
        measured_c=Decimal("21.0"), setpoint_c=Decimal("16.0"),
        valve_protection_due=True, parameter=_parameter(min_off_seconds=0),
    ))
    assert decision.heating is False


# ---------------------------------------------------------------------------
# Rule 1 — sensor failure
# ---------------------------------------------------------------------------


def test_rule1_a_stale_sensor_controls_to_frost_protection() -> None:
    """A stale reading no longer supports a normal heating decision — but it does
    support frost protection.

    Shutting off permanently would be the more dangerous answer: that is exactly
    how a pipe freezes in January. Instead, the frost-protection setpoint
    applies; it is low enough that the system, working off a wrong value, heats
    at most to a harmless level.
    """
    e = decide(
        _lage(sensor_status="veraltet", measured_c=Decimal("10.0"), setpoint_c=Decimal("21.0"),
              frost_c=Decimal("16.0"))
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_FROST_SENSOR_FAILURE
    assert "16.0" in e.reason and "Frostschutz" in e.reason


def test_rule1_a_stale_sensor_does_not_heat_to_the_normal_setpoint() -> None:
    """With a failed sensor, the actual setpoint explicitly no longer applies."""
    e = decide(
        _lage(sensor_status="veraltet", measured_c=Decimal("18.0"), setpoint_c=Decimal("21.0"),
              frost_c=Decimal("16.0"))
    )
    # At 21 °C, 18 °C would trigger heating; at the frost-protection value of 16 °C it does not.
    assert e.heating is False
    assert e.reason_code == REASON_CODE_FROST_SENSOR_FAILURE


def test_rule1_no_source_does_not_heat() -> None:
    """Without any measured value there is nothing to control against — then only off remains."""
    e = decide(_lage(sensor_status="keine_quelle", measured_c=None))
    assert e.heating is False
    assert e.reason_code == REASON_CODE_NO_SOURCE


def test_rule1_a_stale_sensor_without_a_value_does_not_heat() -> None:
    """Stale AND without a value: even frost protection needs something to measure against."""
    e = decide(_lage(sensor_status="veraltet", measured_c=None))
    assert e.heating is False
    assert e.reason_code == REASON_CODE_NO_SOURCE


def test_rule1_safety_net_status_ok_without_a_measured_value() -> None:
    """A contract violation by the caller (status 'ok', but no measured value) does
    not lead to a crash, but to the same safe answer as 'keine_quelle'."""
    e = decide(_lage(sensor_status="ok", measured_c=None))
    assert e.heating is False
    assert e.reason_code == REASON_CODE_NO_SOURCE


# ---------------------------------------------------------------------------
# Rule 2 — operating mode 'off'
# ---------------------------------------------------------------------------


def test_rule2_off_runs_through_the_normal_rule() -> None:
    """'off' does not mean powerless: the caller has already resolved soll_c to the
    frost-protection value (aufgeloester_sollwert), and from here on the normal
    hysteresis simply applies to it — with a sufficiently cold measured value,
    heating happens even in state 'off'."""
    e = decide(
        _lage(
            operating_mode="off",
            setpoint_c=Decimal("16.0"),
            setpoint_reason="Betriebsart Aus — Frostschutz",
            measured_c=Decimal("10.0"),
            heating_now=False,
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_HEATING


def test_regel2_off_schaltet_auch_wieder_aus() -> None:
    e = decide(
        _lage(
            operating_mode="off",
            setpoint_c=Decimal("16.0"),
            measured_c=Decimal("18.0"),
            heating_now=True,
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_OFF


# ---------------------------------------------------------------------------
# Rule 3 — window open
# ---------------------------------------------------------------------------


def test_rule3_an_open_window_does_not_heat_despite_a_cold_room() -> None:
    e = decide(_lage(window_open=True, measured_c=Decimal("5.0"), setpoint_c=Decimal("21.0")))
    assert e.heating is False
    assert e.reason_code == REASON_CODE_WINDOW_OPEN


# ---------------------------------------------------------------------------
# Rule 4 — restart delay
# ---------------------------------------------------------------------------


def test_regel4_wiederanlaufverzoegerung_haelt_ab() -> None:
    """Window only just closed again — the system should not immediately start
    heating against a room that is still cooling down."""
    e = decide(
        _lage(
            window_open=False,
            window_closed_for_s=100,
            parameter=_parameter(window_resume_delay_seconds=300),
            measured_c=Decimal("5.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is False
    assert "Wiederanlauf" in e.reason


def test_rule4_after_the_delay_expires_the_normal_rule_applies_again() -> None:
    e = decide(
        _lage(
            window_open=False,
            window_closed_for_s=301,
            parameter=_parameter(window_resume_delay_seconds=300),
            measured_c=Decimal("5.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_HEATING


def test_rule4_no_delay_without_a_known_closing_time() -> None:
    """`fenster_zu_seit_s is None` means 'no pending delay' (the window has never
    been open since recording began) — then there is nothing to wait out."""
    e = decide(
        _lage(
            window_open=False,
            window_closed_for_s=None,
            measured_c=Decimal("5.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is True


# ---------------------------------------------------------------------------
# Rule 5 — minimum switching duration
# ---------------------------------------------------------------------------


def test_rule5_the_minimum_on_time_keeps_the_valve_open() -> None:
    """Even though the hysteresis has long demanded 'off', a heating phase that
    has only just begun stays on for min_on_seconds — valve protection."""
    e = decide(
        _lage(
            heating_now=True,
            held_for_s=10,
            parameter=_parameter(min_on_seconds=300),
            measured_c=Decimal("30.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_rule5_the_minimum_off_time_keeps_the_valve_closed() -> None:
    e = decide(
        _lage(
            heating_now=False,
            held_for_s=10,
            parameter=_parameter(min_off_seconds=300),
            measured_c=Decimal("5.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_rule5_an_unknown_elapsed_time_does_not_lift_the_block_artificially() -> None:
    """`seit_s is None` means 'duration of the current state unknown', typically the
    first cycle after a restart with no history. A lock based on an unknown
    duration would itself be arbitrary; so it does not apply here, and the
    hysteresis continues to decide as usual — a freshly started service does not
    get stuck in a deadline that never began."""
    e = decide(
        _lage(
            heating_now=True,
            held_for_s=None,
            parameter=_parameter(min_on_seconds=300),
            measured_c=Decimal("30.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_OFF


# ---------------------------------------------------------------------------
# Rule 6 — hysteresis, including edge cases
# ---------------------------------------------------------------------------


def test_rule6_switches_on_below_setpoint_minus_hysteresis() -> None:
    e = decide(
        _lage(
            heating_now=False,
            measured_c=Decimal("20.4"),
            setpoint_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_HEATING


def test_rule6_switches_off_above_setpoint_plus_hysteresis() -> None:
    e = decide(
        _lage(
            heating_now=True,
            measured_c=Decimal("21.6"),
            setpoint_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_OFF


def test_rule6_exactly_on_the_switch_on_threshold_does_not_switch_on_yet() -> None:
    """current == setpoint - h: exactly at the threshold, it does not yet switch on."""
    e = decide(
        _lage(
            heating_now=False,
            measured_c=Decimal("20.5"),
            setpoint_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_UNCHANGED


def test_rule6_exactly_on_the_switch_off_threshold_does_not_switch_off_yet() -> None:
    """current == setpoint + h: exactly at the threshold, it does not yet switch off."""
    e = decide(
        _lage(
            heating_now=True,
            measured_c=Decimal("21.5"),
            setpoint_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_UNCHANGED


def test_rule6_inside_the_hysteresis_nothing_changes_in_either_direction() -> None:
    aus_bleibt_aus = decide(
        _lage(heating_now=False, measured_c=Decimal("21.0"), setpoint_c=Decimal("21.0"))
    )
    an_bleibt_an = decide(
        _lage(heating_now=True, measured_c=Decimal("21.0"), setpoint_c=Decimal("21.0"))
    )
    assert aus_bleibt_aus.heating is False
    assert an_bleibt_an.heating is True
    assert aus_bleibt_aus.reason_code == an_bleibt_an.reason_code == REASON_CODE_UNCHANGED


# ---------------------------------------------------------------------------
# The defect of the legacy system
# ---------------------------------------------------------------------------


def test_the_legacy_defect_no_constant_toggling_at_the_setpoint() -> None:
    """The legacy system decides `if ist < soll: an, sonst aus` — without hysteresis
    the valve toggles on every cycle at ist == soll (on, off, on, off, ...),
    because equality re-evaluates the same comparison each time with no memory
    of the previous state. This test runs several cycles with exactly
    `ist == soll` and shows that `thermoctl`, thanks to hysteresis, stays at the
    last-chosen state on every cycle, regardless of whether that state started
    as 'on' or 'off'.
    """
    measured_c = Decimal("21.0")
    setpoint_c = Decimal("21.0")
    parameter = _parameter(hysteresis_k=Decimal("0.5"), min_on_seconds=0, min_off_seconds=0)

    for start_state in (False, True):
        heating_now = start_state
        for cycle in range(5):
            e = decide(
                _lage(
                    measured_c=measured_c,
                    setpoint_c=setpoint_c,
                    heating_now=heating_now,
                    held_for_s=1000,
                    parameter=parameter,
                )
            )
            assert e.heating == start_state, f"cycle {cycle}: switched unexpectedly"
            assert e.reason_code == REASON_CODE_UNCHANGED
            heating_now = e.heating


# ---------------------------------------------------------------------------
# Precedence against one another — every pair of adjacent rules
# ---------------------------------------------------------------------------


def test_precedence_sensor_failure_beats_the_resolved_setpoint() -> None:
    """With a failed sensor, the frost-protection value applies, no matter what
    the schedule says."""
    e = decide(
        _lage(sensor_status="veraltet", operating_mode="off", measured_c=Decimal("5.0"),
              setpoint_c=Decimal("21.0"), frost_c=Decimal("16.0"))
    )
    assert e.reason_code == REASON_CODE_FROST_SENSOR_FAILURE
    assert e.heating is True
    assert "16.0" in e.reason


def test_precedence_an_open_window_beats_frost_protection_on_sensor_failure() -> None:
    """An open window wins even against frost protection.

    That is intentional: heating against an open window helps no one, and the
    zone does not cool down to frost level in that time. As soon as the window
    is closed, frost protection kicks in again.
    """
    e = decide(
        _lage(sensor_status="veraltet", measured_c=Decimal("5.0"), window_open=True)
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_WINDOW_OPEN


def test_precedence_sensor_failure_beats_an_open_window() -> None:
    """Specifically required in the assignment: sensor failure wins even against an open window."""
    e = decide(
        _lage(sensor_status="keine_quelle", measured_c=None, window_open=True)
    )
    assert e.reason_code == REASON_CODE_NO_SOURCE


def test_precedence_operating_mode_off_loses_to_an_open_window() -> None:
    """'off' merely leads to the frost-protection setpoint; an open window still
    wins against the resulting heating intent of the hysteresis."""
    e = decide(
        _lage(
            operating_mode="off",
            setpoint_c=Decimal("16.0"),
            measured_c=Decimal("5.0"),  # far below setpoint — hysteresis would call for 'on'
            window_open=True,
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_WINDOW_OPEN


def test_precedence_an_open_window_beats_the_restart_delay() -> None:
    """Contradictory input (window open, but a 'closed since' duration is also
    set) — rule 3 wins regardless of what rule 4 would say about it."""
    e = decide(
        _lage(
            window_open=True,
            window_closed_for_s=1,
            parameter=_parameter(window_resume_delay_seconds=300),
            measured_c=Decimal("5.0"),
        )
    )
    assert e.reason_code == REASON_CODE_WINDOW_OPEN


def test_precedence_the_restart_delay_beats_the_minimum_switch_time() -> None:
    """Window only just closed AND the current (off) state has also not yet reached
    the minimum duration — rule 4 decides with its own reasoning, not with that of
    rule 5."""
    e = decide(
        _lage(
            window_open=False,
            window_closed_for_s=10,
            parameter=_parameter(window_resume_delay_seconds=300, min_off_seconds=300),
            heating_now=False,
            held_for_s=10,
            measured_c=Decimal("5.0"),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_OFF
    assert e.reason_code != REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_precedence_the_minimum_switch_time_beats_the_hysteresis() -> None:
    """Specifically required in the assignment (in substance): a minimum duration
    still in progress holds the state, even though the hysteresis has long
    called for something else."""
    e = decide(
        _lage(
            heating_now=True,
            held_for_s=5,
            parameter=_parameter(min_on_seconds=300, hysteresis_k=Decimal("0.5")),
            measured_c=Decimal("30.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.heating is True
    assert e.reason_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_precedence_an_open_window_beats_the_minimum_switch_time() -> None:
    """Specifically required in the assignment: an open window wins against a
    minimum switching duration in progress that could otherwise have held the
    heating state."""
    e = decide(
        _lage(
            window_open=True,
            heating_now=True,
            held_for_s=5,
            parameter=_parameter(min_on_seconds=300),
            measured_c=Decimal("30.0"),
        )
    )
    assert e.heating is False
    assert e.reason_code == REASON_CODE_WINDOW_OPEN


# ---------------------------------------------------------------------------
# Edge case minimum switching duration: the lock ends exactly at the stated time
# ---------------------------------------------------------------------------


def test_exactly_at_the_minimum_duration_the_block_is_over() -> None:
    """seit_s == min_on_seconds: the lock is already over at this point
    (`<` instead of `<=` in the condition), the hysteresis decides normally again."""
    e = decide(
        _lage(
            heating_now=True,
            held_for_s=300,
            parameter=_parameter(min_on_seconds=300, hysteresis_k=Decimal("0.5")),
            measured_c=Decimal("30.0"),
            setpoint_c=Decimal("21.0"),
        )
    )
    assert e.reason_code != REASON_CODE_BLOCKED_MINIMUM_DURATION
    assert e.heating is False
    assert e.reason_code == REASON_CODE_OFF


# ---------------------------------------------------------------------------
# The offset has an effect
# ---------------------------------------------------------------------------


def test_the_offset_changes_the_decision_when_it_reaches_past_the_hysteresis() -> None:
    """The same raw measured value leads to a different decision with a
    sufficiently large offset — calibration takes effect before the rule, as
    required in section 6."""
    ohne_offset = decide(
        _lage(
            measured_c=Decimal("20.0"),
            setpoint_c=Decimal("21.0"),
            heating_now=False,
            parameter=_parameter(hysteresis_k=Decimal("0.5"), temperature_offset_k=Decimal("0.0")),
        )
    )
    mit_offset = decide(
        _lage(
            measured_c=Decimal("20.0"),
            setpoint_c=Decimal("21.0"),
            heating_now=False,
            parameter=_parameter(hysteresis_k=Decimal("0.5"), temperature_offset_k=Decimal("2.0")),
        )
    )
    assert ohne_offset.heating is True
    assert mit_offset.heating is False
    assert ohne_offset.reason_code != mit_offset.reason_code


# ---------------------------------------------------------------------------
# The reasoning carries concrete numbers, not a template
# ---------------------------------------------------------------------------


def test_the_reason_carries_the_actual_numbers_of_the_hysteresis_decision() -> None:
    e = decide(
        _lage(
            heating_now=False,
            measured_c=Decimal("20.4"),
            setpoint_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert "20.4" in e.reason
    assert "21.0" in e.reason
    assert "0.5" in e.reason


def test_the_reason_carries_the_actual_numbers_of_the_minimum_duration_decision() -> None:
    e = decide(
        _lage(
            heating_now=True,
            held_for_s=42,
            parameter=_parameter(min_on_seconds=300),
            measured_c=Decimal("30.0"),
        )
    )
    assert "42" in e.reason
    assert "300" in e.reason


def test_the_reason_carries_the_actual_numbers_of_the_restart_delay() -> None:
    e = decide(
        _lage(
            window_open=False,
            window_closed_for_s=17,
            parameter=_parameter(window_resume_delay_seconds=300),
            measured_c=Decimal("5.0"),
        )
    )
    assert "17" in e.reason
    assert "300" in e.reason
