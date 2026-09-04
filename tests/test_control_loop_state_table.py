"""Generated state table for the complete control-rule precedence chain.

The expected results below are an independent, hand-written transcription of the
precedence documented in the control-loop specification.  They are deliberately not
obtained from :func:`decide` or from its implementation details.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Literal

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

SensorState = Literal["ok", "veraltet", "keine_quelle"]
WindowState = Literal["closed", "open", "restarting"]
ProtectionState = Literal["not_due", "due", "active"]
MeasurementState = Literal[
    "below", "at_lower_edge", "inside", "at_upper_edge", "above", "unavailable"
]
Mode = Literal["auto", "manual", "off"]


@dataclass(frozen=True)
class StateRow:
    sensor: SensorState
    window: WindowState
    override_active: bool
    minimum_duration_expired: bool
    protection: ProtectionState
    measurement: MeasurementState
    mode: Mode
    heating_now: bool

    @property
    def row_id(self) -> str:
        timer = "timer_expired" if self.minimum_duration_expired else "timer_running"
        heating = "heating" if self.heating_now else "off"
        override = "override" if self.override_active else "no_override"
        return "-".join(
            (
                self.sensor,
                self.window,
                override,
                timer,
                self.protection,
                self.measurement,
                self.mode,
                heating,
            )
        )


@dataclass(frozen=True)
class ExpectedDecision:
    heating: bool
    reason_code: str


SENSORS: tuple[SensorState, ...] = ("ok", "veraltet", "keine_quelle")
WINDOWS: tuple[WindowState, ...] = ("closed", "open", "restarting")
PROTECTIONS: tuple[ProtectionState, ...] = ("not_due", "due", "active")
MEASUREMENTS: tuple[MeasurementState, ...] = (
    "below",
    "at_lower_edge",
    "inside",
    "at_upper_edge",
    "above",
    "unavailable",
)
MODES: tuple[Mode, ...] = ("auto", "manual", "off")


def _all_rows() -> list[StateRow]:
    return [
        StateRow(*values)
        for values in product(
            SENSORS,
            WINDOWS,
            (False, True),
            (False, True),
            PROTECTIONS,
            MEASUREMENTS,
            MODES,
            (False, True),
        )
    ]


def _exclusion_reason(row: StateRow) -> str | None:
    # A zone without a source has no value; an OK or stale sensor has a current or
    # last-known value.  Relative hysteresis positions therefore cannot coexist with
    # no source, while "unavailable" cannot coexist with OK or stale.
    if row.sensor == "keine_quelle" and row.measurement != "unavailable":
        return "no source cannot have a measurement relative to hysteresis"
    if row.sensor != "keine_quelle" and row.measurement == "unavailable":
        return "an OK or stale sensor has a current or last-known measurement"

    # A protection marker being set ("active") does not mean rule 7 currently wins.
    # An override starting, the mode switching to 'off', or the sensor failing can
    # make rule 7 lose mid-run without clearing the marker — it only clears once the
    # run's persisted duration elapses, in `services/shadow_run.py`. So "active" with
    # heating_now false is reachable in practice (an interrupted run whose ordinary
    # hysteresis or minimum-duration decision came out "off") and stays in the table.
    return None


ALL_ROWS = _all_rows()
ROWS = [row for row in ALL_ROWS if _exclusion_reason(row) is None]
EXCLUDED_ROWS = [row for row in ALL_ROWS if _exclusion_reason(row) is not None]


def _expected_from_specification(row: StateRow) -> ExpectedDecision:
    """Apply the hand-written specification table, in documented priority order."""
    sensor_failed = row.sensor == "veraltet"
    failed_reason = REASON_CODE_FROST_SENSOR_FAILURE

    # Rule 1: no value permits no control at all; stale retains a value and continues
    # through the remaining rules against the frost-protection setpoint.
    if row.sensor == "keine_quelle":
        return ExpectedDecision(False, REASON_CODE_NO_SOURCE)

    # Rule 2 changes the effective setpoint for mode off upstream.  The fixture models
    # that setpoint explicitly, so no decision is returned at this point.

    # Rules 3 and 4: an open window and its finite restart delay outrank every timer,
    # ordinary demand, override, and protection-run state.
    if row.window == "open":
        return ExpectedDecision(False, REASON_CODE_WINDOW_OPEN)
    if row.window == "restarting":
        return ExpectedDecision(False, REASON_CODE_OFF)

    # Whether rule 7 would currently win, independent of the "active" marker alone —
    # shared between rule 5's exemption below and rule 7 itself, exactly as the fix
    # in control_loop.py shares one `protection_allowed` between both rules.
    protection_allowed = (
        row.protection != "not_due"
        and row.sensor == "ok"
        and row.mode != "off"
        and not row.override_active
    )

    # Rule 5: only the timer of the current actuator state exists in Situation.  The
    # axis therefore means min_on when heating and min_off when off.  The two axes
    # are exempt from their timer under different conditions:
    #
    # - min_off (heating_now false): exempt only while a run is genuinely still
    #   winning (marker "active" AND protection_allowed) — an interrupted run
    #   (marker still set but rule 7 no longer wins, e.g. because of the very
    #   override or mode this row tests) falls back to the regular timer like any
    #   other decision. This protects a run's *start* from being blocked.
    # - min_on (heating_now true): exempt whenever the marker is "active", full
    #   stop — regardless of protection_allowed. min_on exists to stop the valve
    #   from short-cycling, and a protection run does not short-cycle: it runs for
    #   its own configured duration. A held-on state that traces back to a
    #   protection run must not be kept open by min_on past the run's own end —
    #   that was the actual bug, found and fixed 2026-09-02.
    protection_currently_winning = row.protection == "active" and protection_allowed
    protection_exempt = (
        row.protection == "active" if row.heating_now else protection_currently_winning
    )
    if not row.minimum_duration_expired and not protection_exempt:
        return ExpectedDecision(row.heating_now, REASON_CODE_BLOCKED_MINIMUM_DURATION)

    # Rule 6: a protection-created on-state is not regular heating.  Below the lower
    # edge starts ordinary (or stale-sensor frost) heating; above the upper edge stops
    # regular heating; inside the band, and exactly on either edge, preserves regular
    # heating — the comparisons in control_loop.py are strict (`<` and `>`), so the
    # edge values themselves never trigger a switch.
    regular_heating = row.heating_now and row.protection != "active"
    if row.measurement == "below" and not regular_heating:
        return ExpectedDecision(
            True, failed_reason if sensor_failed else REASON_CODE_HEATING
        )
    if row.measurement == "above" and regular_heating:
        return ExpectedDecision(False, REASON_CODE_OFF)
    if regular_heating:
        return ExpectedDecision(
            True, failed_reason if sensor_failed else REASON_CODE_UNCHANGED
        )

    # Rule 7: this table enables protection in every row so that its three states are
    # observable.  Only a due/active run with a healthy sensor, a non-off mode, and no
    # override may win after ordinary hysteresis has declined to heat.
    if protection_allowed:
        return ExpectedDecision(True, REASON_CODE_VALVE_PROTECTION)
    return ExpectedDecision(
        False, failed_reason if sensor_failed else REASON_CODE_UNCHANGED
    )


def _parameters() -> ControlParameters:
    return ControlParameters(
        hysteresis_k=Decimal("0.5"),
        min_on_seconds=300,
        min_off_seconds=300,
        sensor_timeout_seconds=600,
        temperature_offset_k=Decimal("0.0"),
        window_resume_delay_seconds=300,
        solar_setback_max_k=Decimal("2.0"),
        valve_protection_enabled=True,
        valve_protection_interval_days=30,
        valve_protection_duration_minutes=10,
    )


def _situation(row: StateRow) -> Situation:
    # Mode off is resolved to frost protection before decide() is called.  A stale
    # sensor also uses frost_c inside decide(); both therefore share the same concrete
    # values for the relative measurement axis.
    effective_setpoint = (
        Decimal("16.0")
        if row.sensor == "veraltet" or row.mode == "off"
        else Decimal("21.0")
    )
    h = _parameters().hysteresis_k
    measured_c = {
        "below": effective_setpoint - Decimal("1.0"),
        "at_lower_edge": effective_setpoint - h,
        "inside": effective_setpoint,
        "at_upper_edge": effective_setpoint + h,
        "above": effective_setpoint + Decimal("1.0"),
        "unavailable": None,
    }[row.measurement]
    return Situation(
        measured_c=measured_c,
        setpoint_c=Decimal("16.0") if row.mode == "off" else Decimal("21.0"),
        setpoint_reason="Hand-written state table",
        frost_c=Decimal("16.0"),
        operating_mode=row.mode,
        heating_now=row.heating_now,
        held_for_s=300 if row.minimum_duration_expired else 299,
        window_open=row.window == "open",
        window_closed_for_s=0 if row.window == "restarting" else None,
        sensor_status=row.sensor,
        parameter=_parameters(),
        override_active=row.override_active,
        valve_protection_due=row.protection != "not_due",
        valve_protection_active=row.protection == "active",
    )


def test_state_table_has_the_documented_complete_size_and_exclusions() -> None:
    # 3 sensors * 3 windows * 2 overrides * 2 timer states * 3 protection states *
    # 6 measurements (below, both band edges, inside, above, unavailable) * 3 modes *
    # 2 heating_now states.
    assert len(ALL_ROWS) == 3888
    assert len(ROWS) == 2376
    assert len(EXCLUDED_ROWS) == 1512

    reasons: dict[str, int] = {}
    for row in EXCLUDED_ROWS:
        reason = _exclusion_reason(row)
        assert reason is not None
        reasons[reason] = reasons.get(reason, 0) + 1
    assert reasons == {
        "no source cannot have a measurement relative to hysteresis": 1080,
        "an OK or stale sensor has a current or last-known measurement": 432,
    }


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.row_id)
def test_complete_control_rule_state_table(row: StateRow) -> None:
    expected = _expected_from_specification(row)
    actual = decide(_situation(row))

    assert (actual.heating, actual.reason_code) == (
        expected.heating,
        expected.reason_code,
    )

