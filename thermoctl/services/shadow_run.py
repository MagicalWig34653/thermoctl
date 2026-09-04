"""The shadow run: assemble the situation per zone, decide, log.

Dry run (section 1 of the specification): this module switches nothing and publishes
nothing. It reads `zone_state` (already advanced by `ingest.zonenzustand_fortschreiben`),
calls `regelung.entscheiden()`, and writes the result as a `shadow_decision` row. These
exact rows later become the basis for comparison against the old system (subproject 4).
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.control_loop import (
    REASON_CODE_BLOCKED_MINIMUM_DURATION,
    REASON_CODE_FROST_SENSOR_FAILURE,
    REASON_CODE_NO_SOURCE,
    REASON_CODE_VALVE_PROTECTION,
    REASON_CODE_WINDOW_OPEN,
    Decision,
    Situation,
    decide,
)
from thermoctl.domain.fault import NO_SOURCE
from thermoctl.domain.pi_control import (
    INTEGRATOR_RESET,
    RESET_REASON_ARMING,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
    ActuatorProfile,
    ModulatorState,
    PiCycleInput,
    PiCycleOutput,
    PiState,
    pi_cycle,
    pi_eligible,
    reset_pi_state,
)
from thermoctl.domain.schedule import Setpoint, resolved_setpoint
from thermoctl.domain.solar_setback import HourlyForecast, sun_expected
from thermoctl.domain.solar_setback import apply as apply_solar_setback
from thermoctl.domain.zone_settings import ControlParameters, control_parameters

log = logging.getLogger(__name__)

_FROST_DEFAULT = Decimal("16.0")

# Not a `pi_control` reset reason -- there is no stable short code for "the zone
# fails `pi_eligible()`" there (`PiEligibility.reason` is a human sentence for the
# interface, not a code; see the build report for why this was not added to
# `pi_control.py` for this task). The full sentence still reaches the shadow log's
# free-text `reason`, so nothing about *why* is lost -- only the structured column
# gets this one stable placeholder instead of `PiEligibility.reason` verbatim,
# which would not fit `controller_fallback_reason`'s 64 characters reliably.
PI_FALLBACK_INELIGIBLE = "pi_ungeeignet"


def _frost_setpoint(session: Session, zone: Zone, settings: Setting) -> Decimal:
    """The zone's frost protection setpoint for the configured frost protection mode.

    The same fallback as in `aufgeloester_sollwert()`: if the zone has no own value
    for this mode, an unremarkable default value applies instead of an error — a
    missing row in `zone_setpoint` must not bring control to a halt.
    """
    value = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == settings.frost_protection_mode_id,
        )
    )
    return value if value is not None else _FROST_DEFAULT


def _previous_state(
    session: Session, zone_id: int, now: datetime
) -> tuple[bool, int | None, bool | None]:
    """`heizt_gerade` and `seit_s` from the chain of this zone's own past decisions.

    In shadow run nothing actually switches, so there's no real valve state to read
    off whether and since when it's currently heating. The only truth available is
    therefore its own decision history: `would_heat` of the latest row counts as the
    current state, and `seit_s` is the time back to the oldest row that still carries
    the same value. This is exactly what later makes the log comparable to the old
    system (section 6 of the specification) — and is the reason the minimum switch
    duration (rule 5 in `regelung.entscheiden`) has any effect at all in shadow run:
    without this derivation, `seit_s` would be `None` on every cycle.

    Also returns the raw `previous_would_heat` for the new row: `None` if there's no
    history at all yet, otherwise the most recently decided value.
    """
    rows = list(
        session.execute(
            select(ShadowDecision.would_heat, ShadowDecision.decided_at)
            .where(ShadowDecision.zone_id == zone_id)
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        )
    )
    if not rows:
        return False, None, None

    current_state = rows[0].would_heat
    start = rows[0].decided_at
    for state, moment in rows:
        if state != current_state:
            break
        start = moment
    return current_state, int((now - start).total_seconds()), current_state


def _window_situation(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, int | None]:
    """Window state and duration since the last closing, from history."""
    if state is None or state.window_open is not False:
        return bool(state and state.window_open), None

    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    role = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    if contact is None or role is None:
        return False, None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == role.id,
            )
        )
    )
    last_closed: datetime | None = None
    for device_id in devices_ids:
        previous_value: str | None = None
        for value, measured_at in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == device_id,
                Measurement.capability_id == contact.id,
                Measurement.value_text.in_(("true", "false")),
            )
            .order_by(Measurement.measured_at, Measurement.id)
        ):
            if value == "true" and previous_value == "false":
                last_closed = max(
                    last_closed or measured_at,
                    measured_at,
                )
            previous_value = value
    if last_closed is None:
        return False, None
    return False, max(0, int((now - last_closed).total_seconds()))


def _with_solar_setback(
    setpoint: Setpoint,
    frost_c: Decimal,
    zone: Zone,
    parameter: ControlParameters,
    settings: Setting,
    forecast: list[HourlyForecast] | None,
    now: datetime,
) -> tuple[Decimal, str]:
    """The setpoint and its reasoning, corrected for an expected solar gain.

    Correction happens **here**, before `Situation` is built -- not inside
    `regelung.entscheiden()`, which stays exactly as unaware of solar setback as it
    was before this feature existed (see `domain.solar_setback` for why). `forecast`
    being `None` (feature off, no location configured, or the source unreachable --
    `integrations.forecast.ForecastCache` already collapses all three to the same
    thing) and a zone with `solar_gain_factor == 0` both fall straight through
    `solar_setback.apply()` to "no correction", so this function never needs to tell
    those cases apart itself.
    """
    setpoint_c: Decimal = setpoint.temperature_c
    setpoint_reason: str = setpoint.reason
    if forecast is None:
        return setpoint_c, setpoint_reason
    expects_sun = sun_expected(forecast, now, settings.solar_setback_lookahead_hours)
    result = apply_solar_setback(
        setpoint_c,
        frost_c,
        factor=zone.solar_gain_factor,
        max_reduction_k=parameter.solar_setback_max_k,
        expects_sun=expects_sun,
    )
    if result is None:
        return setpoint_c, setpoint_reason
    return (
        result.setpoint_c,
        f"{setpoint_reason} Sonnenabsenkung: -{result.reduction_k} K wegen erwarteter "
        f"Sonneneinstrahlung in den nächsten {settings.solar_setback_lookahead_hours} Stunden.",
    )


def _effective_override(session: Session, zone: Zone, now: datetime) -> ZoneOverride | None:
    """The override currently in force, if any -- same query `resolved_setpoint()`
    uses, but keeping the row itself: `_process_zone` needs the plain boolean for
    `Situation.override_active` (as before), and the PI wiring below needs the
    override's own id for its setpoint-context key (section 2 of the PI
    specification), which a boolean cannot give it.
    """
    return session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= now,
            (ZoneOverride.ends_at.is_(None) | (ZoneOverride.ends_at > now)),
        )
        .order_by(ZoneOverride.created_at.desc(), ZoneOverride.id.desc())
    ).first()


# --------------------------------------------------------------------------- #
# PI wiring (steps 4 and 5 of the build order in section 11 of the PI
# specification). `thermoctl.domain.pi_control` supplies the pure arithmetic and
# window modulator (step 3, already built and mutation-tested); everything below
# is the orchestration that decides, once per zone and cycle, whether to call it
# at all, loads and persists `ZoneState`'s PI columns, and turns the result into
# `ShadowDecision`'s structured PI diagnostics.
#
# `decide()` itself is untouched -- `Situation`/`Decision` do not gain a PI notion
# of their own, and the 2.376-line state-table test keeps proving that for
# `pi_enabled=False` (every zone until someone flips the latch directly in the
# database; there is deliberately no operating path for it yet, see
# `tests/test_pi_schema.py`). PI replaces only what section 6, rule 6 of
# `control_loop.decide()` would otherwise decide -- rules 1, 3, 4 and 7 keep
# exactly the precedence they already have, because their conditions are read
# from `Situation`/`decision.reason_code` here, never recomputed.
# --------------------------------------------------------------------------- #


def _pi_actuator_profiles(session: Session, zone: Zone) -> list[ActuatorProfile]:
    """Every device carrying the zone's `actuator` role -- self-regulating or not.

    `domain.switch_commands.switch_commands()`/`thermostat_commands()` already
    filter self-regulating devices out of their own results; `pi_eligible()` needs to
    see them anyway, because its verdict is device-accurate rather than zone-wide
    (the "Feststehender Zuschnitt" section of the PI specification, as amended): a
    self-regulating valve never receives PI's `heating` decision at all, so
    `pi_eligible()` has to know a profile is self-regulating in order to *skip* it,
    not reject the zone for it -- while a non-self-regulating thermostat-capable
    actuator still must reject the zone, since `thermostat_commands()` would turn
    PI's decision into a setpoint jump. Handing over only the already-narrowed
    switch actuators would hide exactly the distinction `pi_eligible()` needs to
    draw.
    """
    actuator_role = session.scalar(select(DeviceRole).where(DeviceRole.code == "actuator"))
    if actuator_role is None:
        return []
    switch = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "switch"))
    thermostat = session.scalar(
        select(DeviceCapability).where(DeviceCapability.code == "thermostat")
    )
    rows = session.execute(
        select(ZoneDevice.device_id, ZoneDevice.self_regulating).where(
            ZoneDevice.zone_id == zone.id,
            ZoneDevice.device_role_id == actuator_role.id,
        )
    )
    profiles: list[ActuatorProfile] = []
    for device_id, self_regulating in rows:
        capability_ids = set(
            session.scalars(
                select(DeviceCapabilityLink.capability_id).where(
                    DeviceCapabilityLink.device_id == device_id
                )
            )
        )
        profiles.append(
            ActuatorProfile(
                self_regulating=bool(self_regulating),
                has_switch_capability=switch is not None and switch.id in capability_ids,
                has_thermostat_capability=(
                    thermostat is not None and thermostat.id in capability_ids
                ),
            )
        )
    return profiles


def _pi_setpoint_context_key(setpoint: Setpoint, override: ZoneOverride | None) -> str:
    """A stable key for "which setpoint context is in effect" (section 2 of the PI
    specification): its origin and identity, never the free-text reason -- the
    specification explicitly rules out comparing `setpoint.reason`.

    Only ever called once `_pi_gate_reason()` has already ruled out this cycle's
    setpoint being the frost-protection one (operating mode 'off', or the
    frost-protection mode itself, both go through `RESET_REASON_FROST` first, in
    `_pi_outcome`) -- so unlike `resolved_setpoint()`'s own precedence, this
    function never needs an 'off' or "no schedule at all" branch of its own: by the
    time it runs, `setpoint.mode_id` is always set (`resolved_setpoint()` never
    returns `None` there except for a fixed-temperature override, already handled
    by the `override is not None` branch below). It still adds the override's own
    id as an extra axis on top of `setpoint.mode_id`: that alone cannot tell an
    override apart from a schedule point naming the same mode, and section 2
    explicitly requires a reset on both the start and the end of an override even
    then. Boost needs no separate case -- the specification is explicit that boost
    is technically an override (`ZoneOverride`), so it already goes through the
    `override` branch.
    """
    if override is not None:
        return f"override:{override.id}"
    assert setpoint.mode_id is not None  # see docstring: ruled out by the caller's gate
    return f"zeitplan:{setpoint.mode_id}"


def _pi_gate_reason(
    reason_code: str, *, resume_delay_active: bool, frost_effective: bool
) -> str | None:
    """Which of section 4's PI-resetting precedence rules governs this cycle, if
    any. `None` means none of them do -- exactly "wo die gewöhnliche Regelung
    heizen würde" (rule 6's territory, including the plain "stay off" case rule 6
    falls through to when rule 7 does not apply either): PI computes a real
    candidate for this cycle.

    Reads `decision.reason_code` wherever `decide()`'s own code already names the
    rule unambiguously -- sensor failure (`REASON_CODE_NO_SOURCE` for "keine
    Quelle", `REASON_CODE_FROST_SENSOR_FAILURE` for a stale reading kept usable via
    the frost setpoint -- together exactly section 4's "Sensorausfall" row) and
    valve protection (`REASON_CODE_VALVE_PROTECTION`, unique to rule 7 winning) are
    each produced by exactly one branch of `decide()`. Two of `decide()`'s codes are
    *not* unique, though, and need the caller's own booleans instead:
    `REASON_CODE_OFF` is returned both by rule 4 (the window resume delay) and by
    rule 6's ordinary "off" branch -- only the former is one of section 4's rules,
    so `resume_delay_active` (computed the same way `Situation.window_closed_for_s`
    already is) tells them apart. And a setpoint resolved to the frost-protection
    mode is not a separate branch in `decide()` at all -- it simply feeds a
    different setpoint into the very same rule 6 -- so `frost_effective` is computed
    independently by the caller from `resolved_setpoint()`'s own result.

    Order matters only for which single reason gets attributed when more than one
    would apply -- the effective boolean is `decide()`'s regardless -- and mirrors
    `decide()`'s actual rule order (1, 3, 4, ..., 7) wherever that is well-defined.
    Valve protection is checked from `decide()`'s own code (authoritative: it is
    only ever returned once rule 6 has already deferred) *before* the independently
    computed `frost_effective`, so a cycle where protection actually wins is
    attributed to protection, not to the frost setpoint that never got to decide
    anything that cycle.
    """
    if reason_code in (REASON_CODE_NO_SOURCE, REASON_CODE_FROST_SENSOR_FAILURE):
        return RESET_REASON_SENSOR_FAILURE
    if reason_code == REASON_CODE_WINDOW_OPEN:
        return RESET_REASON_WINDOW_OPEN
    if resume_delay_active:
        return RESET_REASON_WINDOW_OPEN
    if reason_code == REASON_CODE_VALVE_PROTECTION:
        return RESET_REASON_VALVE_PROTECTION
    if frost_effective:
        return RESET_REASON_FROST
    return None


def _aware(value: datetime | None) -> datetime | None:
    """UTC-aware, for `pi_control`'s arithmetic -- `window_start_for()` refuses a
    naive `datetime` on purpose (section 3 fixes the window to UTC quarter-hours,
    not local time, so a summer-time change never moves the boundary). Everywhere
    else in this application a naive `datetime` already means UTC implicitly (see
    e.g. `services/publishing.py::_as_text`) -- including every `DateTime` column
    `ZoneState` stores PI's own state in, and the `now` this module is called
    with. This is the one boundary where that implicit convention needs to become
    explicit; `_naive()` below is its exact inverse, used everywhere a value goes
    back into a column.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _load_pi_state(state: ZoneState) -> PiState:
    """Reconstructs the pure `PiState` from `ZoneState`'s durable PI columns.

    `ModulatorState.held_for_s` has no column of its own -- `pi_last_switch_at`
    (when the current on/off run started) plus `pi_last_evaluated_at` (this state's
    own "as of" timestamp) already determine it exactly, since the modulator's own
    bookkeeping accumulates real elapsed time cycle by cycle (`_write_pi_state`
    below is the inverse: it is what keeps `pi_last_switch_at` meaning exactly
    that). `None` for either column -- never run, or freshly reset -- means
    "unknown", matching `NEUTRAL_MODULATOR_STATE.held_for_s`.
    """
    held_for_s: int | None = None
    if state.pi_last_switch_at is not None and state.pi_last_evaluated_at is not None:
        held_for_s = int(
            (state.pi_last_evaluated_at - state.pi_last_switch_at).total_seconds()
        )
    modulator = ModulatorState(
        on=bool(state.pi_last_switch_heating),
        held_for_s=held_for_s,
        remainder_s=state.pi_time_balance_seconds,
        window_start=_aware(state.pi_window_started_at),
        frozen_duty=state.pi_window_duty,
    )
    return PiState(
        integral=state.pi_integral,
        last_evaluated_at=_aware(state.pi_last_evaluated_at),
        setpoint_context_key=state.pi_setpoint_context_key,
        modulator=modulator,
        awaiting_boundary_until=_aware(state.pi_awaiting_boundary_until),
        last_reset_reason=state.pi_last_reset_reason,
    )


def _write_pi_state(
    row: ZoneState, old_state: PiState, output: PiCycleOutput, now: datetime
) -> None:
    """Persists one regular `pi_cycle()` result -- the inverse of `_load_pi_state`.

    `pi_last_switch_at` only moves when the current on/off run genuinely restarted
    this cycle: either the modulator itself flipped (`output.switched`), or its
    starting point was reset from under it by a setpoint-context change this same
    cycle (`old_state`'s context key differs from the new one -- section 2's
    "Beginn und Ende einer Übersteuerung", handled inside `pi_cycle()` itself, see
    its docstring) even though the *chosen* on/off value happens not to have
    changed, or there was no previous run to speak of at all. Any other cycle
    leaves it untouched, so `_load_pi_state`'s derivation above keeps accumulating
    the true elapsed time of the *same* run.
    """
    new_state = output.state
    row.pi_integral = new_state.integral
    row.pi_last_evaluated_at = _naive(new_state.last_evaluated_at)
    row.pi_setpoint_context_key = new_state.setpoint_context_key
    row.pi_window_started_at = _naive(new_state.modulator.window_start)
    row.pi_window_duty = new_state.modulator.frozen_duty
    row.pi_time_balance_seconds = new_state.modulator.remainder_s
    if new_state.modulator.held_for_s is None:
        row.pi_last_switch_at = None
        row.pi_last_switch_heating = None
    else:
        row.pi_last_switch_heating = new_state.modulator.on
        context_reset_this_cycle = (
            old_state.setpoint_context_key is not None
            and old_state.setpoint_context_key != new_state.setpoint_context_key
        )
        if output.switched or context_reset_this_cycle or row.pi_last_switch_at is None:
            row.pi_last_switch_at = _naive(now)
    row.pi_awaiting_boundary_until = _naive(new_state.awaiting_boundary_until)
    row.pi_last_reset_reason = new_state.last_reset_reason


def _write_reset_state(row: ZoneState, reset_state: PiState) -> None:
    """Persists an out-of-band reset (section 4's precedence table, or the safe
    arming/invalid-state wait) -- unlike `_write_pi_state`, always unconditionally
    clears the switch-timing columns, matching `reset_pi_state()`'s own neutral
    modulator.
    """
    row.pi_integral = reset_state.integral
    row.pi_last_evaluated_at = _naive(reset_state.last_evaluated_at)
    row.pi_setpoint_context_key = reset_state.setpoint_context_key
    row.pi_window_started_at = _naive(reset_state.modulator.window_start)
    row.pi_window_duty = reset_state.modulator.frozen_duty
    row.pi_time_balance_seconds = reset_state.modulator.remainder_s
    row.pi_last_switch_at = None
    row.pi_last_switch_heating = None
    row.pi_awaiting_boundary_until = _naive(reset_state.awaiting_boundary_until)
    row.pi_last_reset_reason = reset_state.last_reset_reason


def _neutralize_pi_state(row: ZoneState) -> None:
    """The full wipe section 5 requires when PI is off or ineligible for this zone
    -- deliberately not `_write_reset_state()` with `reset_pi_state()`'s output,
    which still records a timestamp and a reason: "neutralisiert" means a later
    activation starts exactly as clean as a zone that has never run PI, including
    `pi_last_control_armed`, so the safe arming wait (section 4's closing
    paragraph) applies again in full the next time this zone is enabled.
    """
    row.pi_integral = Decimal("0")
    row.pi_last_evaluated_at = None
    row.pi_setpoint_context_key = None
    row.pi_last_control_armed = None
    row.pi_window_started_at = None
    row.pi_window_duty = None
    row.pi_time_balance_seconds = Decimal("0")
    row.pi_last_switch_at = None
    row.pi_last_switch_heating = None
    row.pi_awaiting_boundary_until = None
    row.pi_last_reset_reason = None


def _neutral_pi_fields() -> dict[str, object]:
    return {
        "requested_controller": "hysteresis",
        "effective_controller": "hysteresis",
        "controller_fallback_reason": None,
        "pi_error_k": None,
        "pi_proportional_term": None,
        "pi_integral_before": None,
        "pi_integral_after": None,
        "pi_raw_duty": None,
        "pi_frozen_duty": None,
        "pi_window_started_at": None,
        "pi_time_balance_before_seconds": None,
        "pi_time_balance_after_seconds": None,
        "pi_state_runtime_seconds": None,
        "pi_integrator_action": None,
        "pi_min_duration_decision": None,
        "pi_reset_reason": None,
        "pi_candidate_would_heat": None,
    }


def _pi_outcome(
    session: Session,
    zone: Zone,
    state: ZoneState | None,
    situation: Situation,
    decision: Decision,
    parameter: ControlParameters,
    settings: Setting,
    setpoint: Setpoint,
    override: ZoneOverride | None,
    now: datetime,
) -> tuple[bool, str | None, dict[str, object]]:
    """Everything PI contributes to one zone's cycle.

    Returns `(effective_heating, reason_suffix, shadow_decision_fields)`:
    `effective_heating` is `decision.heating` (the ordinary hysteresis decision)
    whenever PI is off, ineligible, temporarily unavailable, or a precedence rule
    from section 4 overrides it -- PI never invents a decision in any of those
    cases, it only explains, in `shadow_decision_fields`, why not. Only when PI is
    enabled, eligible, and none of section 4's rules apply does its own candidate
    become the effective decision -- this is the single point where step 5 connects
    `Decision.heating` to what `services/publishing.py` reads back out as
    `would_heat`; step 4 alone (calling this, but ignoring `effective_heating` and
    keeping `decision.heating`) is the "Parallelwert ohne Wirkung".
    """
    # `pi_control` requires a timezone-aware `now` (`window_start_for()`'s own
    # guard); everywhere else, including every caller of this module, a naive
    # `datetime` already means UTC implicitly. See `_aware()`'s docstring.
    now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    fields = _neutral_pi_fields()
    fields["requested_controller"] = "pi" if parameter.pi_enabled else "hysteresis"

    if not parameter.pi_enabled:
        if state is not None:
            _neutralize_pi_state(state)
        return decision.heating, None, fields

    if state is None:
        # Nothing to load or persist PI state into. `decide()` has already fallen
        # back to `REASON_CODE_NO_SOURCE` for this cycle -- no `zone_state` row
        # means no measurement either -- so there is nothing meaningful PI could
        # add regardless.
        return decision.heating, None, fields

    eligibility = pi_eligible(
        _pi_actuator_profiles(session, zone),
        control_cycle_seconds=settings.shadow_interval_seconds,
        pi_min_on_seconds=parameter.pi_min_on_seconds,
        pi_min_off_seconds=parameter.pi_min_off_seconds,
    )
    if not eligibility.eligible:
        _neutralize_pi_state(state)
        fields["controller_fallback_reason"] = PI_FALLBACK_INELIGIBLE
        return decision.heating, f"PI-Rückfall: {eligibility.reason}", fields

    armed = bool(settings.control_armed)
    previous_armed = state.pi_last_control_armed
    state.pi_last_control_armed = armed
    needs_safe_start = previous_armed is None or (previous_armed is False and armed)
    if needs_safe_start:
        reason = RESET_REASON_INVALID_STATE if previous_armed is None else RESET_REASON_ARMING
        _write_reset_state(state, reset_pi_state(reason, now=now, await_next_boundary=True))
        fields["controller_fallback_reason"] = reason
        fields["pi_reset_reason"] = reason
        fields["pi_integrator_action"] = INTEGRATOR_RESET
        return (
            decision.heating,
            f"PI-Rückfall: {reason}, wartet auf die nächste Fenstergrenze.",
            fields,
        )

    resume_delay_active = (
        situation.window_closed_for_s is not None
        and situation.window_closed_for_s < situation.parameter.window_resume_delay_seconds
    )
    frost_effective = (
        zone.operating_mode.code == "off"
        or setpoint.mode_id == settings.frost_protection_mode_id
    )
    gate = _pi_gate_reason(
        decision.reason_code,
        resume_delay_active=resume_delay_active,
        frost_effective=frost_effective,
    )
    if gate is not None:
        _write_reset_state(state, reset_pi_state(gate, now=now))
        fields["pi_reset_reason"] = gate
        fields["pi_integrator_action"] = INTEGRATOR_RESET
        return decision.heating, None, fields

    # No section-4 rule applies: rule 6's territory (or rule 7's plain "stay off"
    # fallthrough, which is behaviourally the same "stay off" rule 6 itself would
    # give -- see `_pi_gate_reason`'s docstring) -- PI computes a real candidate.
    assert situation.measured_c is not None  # the sensor gate above already excludes this
    pi_state = _load_pi_state(state)
    context_key = _pi_setpoint_context_key(setpoint, override)
    calibrated_c = situation.measured_c + parameter.temperature_offset_k
    error_k = situation.setpoint_c - calibrated_c
    fields["pi_error_k"] = error_k
    fields["pi_proportional_term"] = parameter.pi_gain_per_k * error_k

    output = pi_cycle(
        pi_state,
        PiCycleInput(
            now=now,
            error_k=error_k,
            setpoint_context_key=context_key,
            expected_cycle_seconds=settings.shadow_interval_seconds,
            gain_per_k=parameter.pi_gain_per_k,
            integral_time_minutes=Decimal(parameter.pi_integral_time_minutes),
            pi_min_on_seconds=parameter.pi_min_on_seconds,
            pi_min_off_seconds=parameter.pi_min_off_seconds,
        ),
    )
    _write_pi_state(state, pi_state, output, now)

    fields["pi_integral_before"] = pi_state.integral
    fields["pi_integral_after"] = output.state.integral
    fields["pi_raw_duty"] = output.duty_raw
    fields["pi_frozen_duty"] = output.state.modulator.frozen_duty
    fields["pi_window_started_at"] = _naive(output.state.modulator.window_start)
    fields["pi_time_balance_before_seconds"] = pi_state.modulator.remainder_s
    fields["pi_time_balance_after_seconds"] = output.state.modulator.remainder_s
    fields["pi_state_runtime_seconds"] = (
        Decimal(output.state.modulator.held_for_s)
        if output.state.modulator.held_for_s is not None
        else None
    )
    fields["pi_integrator_action"] = output.integrator_action
    fields["pi_reset_reason"] = output.state.last_reset_reason
    fields["pi_candidate_would_heat"] = output.heating

    if not output.pi_available:
        # A time gap, or still waiting out an earlier safe-start boundary --
        # `decide()`'s ordinary hysteresis answer already is the correct one for
        # this one cycle (section 4's closing paragraph).
        fields["controller_fallback_reason"] = output.reason_code
        return decision.heating, None, fields

    fields["pi_min_duration_decision"] = output.reason_code
    fields["effective_controller"] = "pi"
    assert output.heating is not None  # guaranteed whenever pi_available is True
    reason_suffix = (
        f"PI-Regelung: Fehler {error_k}K, Tastgrad {output.duty_raw}, "
        f"{output.reason_code} -> {'Heizen' if output.heating else 'Aus'}."
    )
    return output.heating, reason_suffix, fields


def _advance_valve_protection(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, bool]:
    """Advances the valve protection bookkeeping in `state` and reports its due-ness.

    Returns `(protection_due, protection_was_active)`. Two writes happen here, in the
    same order as before this was split out of `_process_zone`: closing an expired
    protection run, and the one-time bridge that condenses pre-existing shadow history
    into `last_regular_heat_at` for installations upgraded with history already in
    place. Both only ever touch `state`, so pulling them out changes nothing about when
    they run relative to the rest of the cycle.

    `protection_was_active` is deliberately the value of `protection_started is not
    None` from *before* this function's own write closes an expired run -- not the
    `protection_active` used internally to decide whether to close it. A cycle that
    closes an expired run must still report the zone as having been under protection:
    otherwise the previous on-state (which came from protection) would look like an
    ordinary hold to the hysteresis rule.

    That guard lasts exactly one cycle, and on its own it is not enough. If a zone's
    minimum *on* duration outlives its protection run -- both are per-zone settings,
    so `min_on_seconds=1200` together with a ten-minute run is a legal configuration
    -- the closing cycle answers `gesperrt_mindestdauer` and keeps the valve open,
    the marker is gone by the next cycle, and the protection on-state is then read as
    regular heating from there on. That is a known defect of the control rules, not
    of this bookkeeping -- found and fixed 2026-09-02 in
    `thermoctl.domain.control_loop.decide()`, which now exempts a held on-state from
    `min_on_seconds` whenever it traces back to a protection run.
    """
    interval = timedelta(days=zone.valve_protection_interval_days)
    run_duration = timedelta(minutes=zone.valve_protection_duration_minutes)
    protection_started = state.valve_protection_started_at if state is not None else None
    protection_active = (
        protection_started is not None and now < protection_started + run_duration
    )
    if state is not None and protection_started is not None and not protection_active:
        state.valve_protection_started_at = None
        # This timestamp closes a simulated shadow run, not a physical valve run.
        # Keeping it preserves the intended cadence in the comparison log; without
        # it, the still-due rule would restart in the same cycle and then forever
        # displace ordinary decisions. Actuator wiring must not treat this marker as
        # proof of movement; it will need its own confirmed-execution semantics.
        state.last_valve_protection_at = now
    if state is not None and not state.regular_heat_history_compacted:
        # One-time bridge for installations upgraded with existing shadow history.
        # Afterwards the condensed marker is authoritative, so protection scheduling
        # never has to reconstruct this operating state from the growing detailed log.
        state.last_regular_heat_at = session.scalar(
            select(ShadowDecision.decided_at)
            .where(
                ShadowDecision.zone_id == zone.id,
                ShadowDecision.would_heat.is_(True),
                ShadowDecision.outcome_code != REASON_CODE_VALVE_PROTECTION,
            )
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
                .limit(1)
        )
        state.regular_heat_history_compacted = True
    last_movement = max(
        (moment for moment in (
            zone.created_at,
            state.last_regular_heat_at if state is not None else None,
            state.last_valve_protection_at if state is not None else None,
        ) if moment is not None),
    )
    protection_due = now >= last_movement + interval
    return protection_due, protection_started is not None


def _apply_decision_to_state(
    state: ZoneState | None, decision: Decision, now: datetime
) -> None:
    """Writes the consequence of a decision back into `state`'s valve protection markers.

    Split out of `_process_zone` because it is a separate concern from *making* the
    decision: `decide()` above only looks at the situation as it stood at the start of
    the cycle, this writes down what that decision means for the next one.
    """
    if state is None:
        return
    if decision.reason_code == REASON_CODE_VALVE_PROTECTION:
        if state.valve_protection_started_at is None:
            state.valve_protection_started_at = now
    elif (
        decision.heating
        and decision.reason_code != REASON_CODE_BLOCKED_MINIMUM_DURATION
    ):
        # Normal control has taken ownership of the on-state. Keeping the
        # protection marker would make the next hysteresis cycle treat that
        # regular state as temporary protection and switch it off too early.
        state.valve_protection_started_at = None
        # Persist simulated regular heating as constant-size operating state,
        # separately from the unbounded detailed shadow log. The marker records a
        # regular heating decision, not a command or physical movement.
        state.last_regular_heat_at = now


def _process_zone(
    session: Session,
    zone: Zone,
    now: datetime,
    forecast: list[HourlyForecast] | None = None,
) -> ShadowDecision:
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollständig"

    state = session.get(ZoneState, zone.id)
    if state is None:
        measured_c = None
        sensor_status = NO_SOURCE
    else:
        measured_c = state.temperature_c
        sensor_status_row = session.get(SensorStatus, state.sensor_status_id)
        assert sensor_status_row is not None, "sensor_status-Zeile fehlt zur Referenz"
        sensor_status = sensor_status_row.code
    window_open, window_closed_for_s = _window_situation(session, zone, state, now)

    setpoint = resolved_setpoint(session, zone, now)
    frost_c = _frost_setpoint(session, zone, settings)
    parameter = control_parameters(session, zone)
    heating_now, held_for_s, previous_would_heat = _previous_state(session, zone.id, now)
    setpoint_c, setpoint_reason = _with_solar_setback(
        setpoint, frost_c, zone, parameter, settings, forecast, now
    )

    override = _effective_override(session, zone, now)
    override_active = override is not None
    protection_due, protection_was_active = _advance_valve_protection(session, zone, state, now)

    situation = Situation(
        measured_c=measured_c,
        setpoint_c=setpoint_c,
        setpoint_reason=setpoint_reason,
        frost_c=frost_c,
        operating_mode=zone.operating_mode.code,
        heating_now=heating_now,
        held_for_s=held_for_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        sensor_status=sensor_status,
        parameter=parameter,
        override_active=override_active,
        valve_protection_due=protection_due,
        # Also true in the first cycle at/after the deadline: the previous on-state
        # still came from protection and must not turn into an endless hysteresis hold.
        valve_protection_active=protection_was_active,
    )
    decision = decide(situation)

    # PI (steps 4 and 5 of the PI specification's build order, section 11): a
    # parallel candidate that -- once the zone is enabled for it, eligible, and no
    # precedence rule from section 4 overrides it -- becomes the effective
    # decision below. `decision` itself, and everything computed from `situation`
    # above, stays exactly the ordinary hysteresis path; nothing here feeds back
    # into `decide()`.
    effective_heating, pi_reason_suffix, pi_fields = _pi_outcome(
        session, zone, state, situation, decision, parameter, settings, setpoint, override, now
    )
    effective_decision = (
        decision
        if effective_heating == decision.heating and pi_reason_suffix is None
        else replace(
            decision,
            heating=effective_heating,
            reason=(
                decision.reason
                if pi_reason_suffix is None
                else f"{decision.reason} {pi_reason_suffix}"
            ),
        )
    )
    _apply_decision_to_state(state, effective_decision, now)

    row = ShadowDecision(
        decided_at=now,
        zone_id=zone.id,
        temperature_c=measured_c,
        setpoint_c=setpoint_c,
        setpoint_reason=setpoint_reason,
        would_heat=effective_decision.heating,
        previous_would_heat=previous_would_heat,
        outcome_code=decision.reason_code,
        reason=effective_decision.reason,
        **pi_fields,
    )
    session.add(row)
    session.flush()
    return row


def cycle(
    session: Session,
    now: datetime,
    forecast: list[HourlyForecast] | None = None,
) -> list[ShadowDecision]:
    """One shadow cycle over all zones — writes, but switches nothing.

    A zone whose processing fails does not hold up the others: each zone runs in its
    own savepoint, whose rollback on an exception only undoes its own incomplete
    changes — not the zones already processed successfully within the same call.

    `forecast` is fetched once, outside this function (`integrations.forecast`), and
    handed to every zone unchanged -- there is exactly one installation-wide location,
    so one fetch per cycle already covers every zone.
    """
    results: list[ShadowDecision] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        try:
            with session.begin_nested():
                row = _process_zone(session, zone, now, forecast)
        except Exception:
            log.exception(
                "Schattenzyklus für eine Zone gescheitert — übrige Zonen laufen weiter",
                extra={"zone_id": zone.id},
            )
            continue
        results.append(row)
    return results
