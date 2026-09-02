from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tests.helpers import (
    CONSTRAINT_ERRORS,
    capability,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    integration,
    role,
    source,
)
from thermoctl.api.schemas import WriteControlParameters, WriteZone
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.zone_settings import (
    BY_NAME,
    PARAMETERS,
    ControlParameters,
    ParameterOutOfRange,
    control_parameters,
    pi_eligibility,
    save_control_parameters,
    set_parameter,
)
from thermoctl.web.daily_views import FELDER, PI_FELDER


def _assign_switch_actuator(session: Session, zone: Zone) -> Device:
    """The minimal device assignment `pi_eligible()` accepts: one ordinary
    switch actuator, nothing self-regulating and nothing carrying `thermostat`."""
    device = Device(
        integration_id=integration(session).id,
        external_id=f"{zone.name}-relais",
        display_name=f"{zone.name}-relais",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()
    return device


def test_pi_configuration_has_the_specified_defaults_without_inheritance(
    session: Session,
) -> None:
    create_settings(session)
    zone = create_zone(session, "pi-vorgaben")

    assert (
        zone.pi_enabled,
        zone.pi_gain_per_k,
        zone.pi_integral_time_minutes,
        zone.pi_min_on_seconds,
        zone.pi_min_off_seconds,
    ) == (False, Decimal("0.25"), 180, 60, 60)
    effective = control_parameters(session, zone)
    assert isinstance(effective, ControlParameters)
    assert (
        effective.pi_enabled,
        effective.pi_gain_per_k,
        effective.pi_integral_time_minutes,
        effective.pi_min_on_seconds,
        effective.pi_min_off_seconds,
    ) == (False, Decimal("0.25"), 180, 60, 60)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pi_gain_per_k", Decimal("0.04")),
        ("pi_gain_per_k", Decimal("0.51")),
        ("pi_gain_per_k", Decimal("0.06")),
        ("pi_integral_time_minutes", 59),
        ("pi_integral_time_minutes", 721),
        ("pi_integral_time_minutes", 61),
        ("pi_min_on_seconds", 59),
        ("pi_min_on_seconds", 301),
        ("pi_min_on_seconds", 61),
        ("pi_min_off_seconds", 59),
        ("pi_min_off_seconds", 301),
        ("pi_min_off_seconds", 61),
    ],
)
def test_pi_configuration_rejects_values_outside_bounds_or_steps(
    session: Session, field: str, value: Decimal | int
) -> None:
    zone = create_zone(session, f"ungueltig-{field}-{value}")
    setattr(zone, field, value)

    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


@pytest.mark.parametrize(
    ("field", "lower", "upper"),
    [
        ("pi_gain_per_k", Decimal("0.05"), Decimal("0.50")),
        ("pi_integral_time_minutes", 60, 720),
        ("pi_min_on_seconds", 60, 300),
        ("pi_min_off_seconds", 60, 300),
    ],
)
def test_pi_configuration_accepts_both_specified_boundaries(
    session: Session, field: str, lower: Decimal | int, upper: Decimal | int
) -> None:
    low_zone = create_zone(session, f"untergrenze-{field}")
    upper_zone = create_zone(session, f"obergrenze-{field}")
    setattr(low_zone, field, lower)
    setattr(upper_zone, field, upper)

    session.flush()


def test_pi_is_now_a_shared_control_parameter_across_every_adapter(session: Session) -> None:
    """The counter-check to what this test used to prove.

    Every adapter shares one whitelist (`domain.zone_settings.PARAMETERS`/`BY_NAME`)
    and one bulk schema (`WriteControlParameters`) -- the same registry
    `hysteresis_k` and `valve_protection_enabled` already go through. `WriteZone`
    (zone *creation*) is deliberately still untouched: PI is a control parameter,
    not something a zone is created with.
    """
    assert "pi_enabled" not in WriteZone.model_fields

    for name in ("pi_enabled", *PI_FELDER):
        assert name in WriteControlParameters.model_fields, name
        assert name in BY_NAME, name
        assert name in {description.name for description in PARAMETERS}, name
    # The four numeric fields are handled exactly like `valve_protection_interval_days`
    # in the web form: parsed through `_check_parameters`, empty means "unchanged".
    # `pi_enabled` stays its own checkbox, like `valve_protection_enabled`, not in
    # `FELDER`'s "empty means inherited" pool -- PI is never inherited (section 7).
    assert "pi_enabled" not in FELDER
    assert set(PI_FELDER).isdisjoint(FELDER)


def test_set_parameter_switches_pi_on_and_off_for_an_eligible_zone_with_audit_entry(
    session: Session,
) -> None:
    """`set_parameter` is the function REST's single-value route and MCP's
    `set_control_parameters` tool both call -- so this alone proves the operating
    path exists for both, not just for the web form.
    """
    create_settings(session)
    source(session, "mcp")
    zone = create_zone(session, "pi-eignet-sich")
    _assign_switch_actuator(session, zone)

    set_parameter(session, zone, "pi_enabled", Decimal(1), user_id=None, source="mcp")

    assert zone.pi_enabled is True
    entry = session.scalar(
        select(AuditEvent).where(AuditEvent.object_type == "zone_settings")
    )
    assert entry is not None
    assert zone.display_name in entry.summary

    # The switch is fully reversible through the same shared function.
    set_parameter(session, zone, "pi_enabled", Decimal(0), user_id=None, source="mcp")
    assert zone.pi_enabled is False


def test_set_parameter_refuses_pi_for_an_ineligible_zone(session: Session) -> None:
    """No actuator assigned at all -- `pi_eligible()`'s first, strictest rejection."""
    create_settings(session)
    source(session, "mcp")
    zone = create_zone(session, "pi-ungeeignet")

    with pytest.raises(ParameterOutOfRange, match="PI-Regelung \\(Beta\\)"):
        set_parameter(session, zone, "pi_enabled", Decimal(1), user_id=None, source="mcp")
    assert zone.pi_enabled is False


def test_save_control_parameters_rejects_a_pi_value_outside_its_bounds(
    session: Session,
) -> None:
    """The bulk write (web form and REST's combined PUT) shares the same bound as
    the single-value route -- checked here through `save_control_parameters`
    directly, the function both of those call."""
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "pi-schrittfehler")

    values = {
        field: getattr(zone, field) for field in ControlParameters.__dataclass_fields__
    }
    values["pi_gain_per_k"] = Decimal("0.32")  # not a multiple of the 0.05 step

    with pytest.raises(ParameterOutOfRange, match="Schritten"):
        save_control_parameters(session, zone, values, user_id=None, source="api")


def test_save_control_parameters_rejects_a_pi_value_outside_its_range(
    session: Session,
) -> None:
    """The plain range check, not just the step check above -- both raise the same
    `ParameterOutOfRange`, but from different lines of `validate_pi_parameters`."""
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "pi-ausserhalb")

    values = {
        field: getattr(zone, field) for field in ControlParameters.__dataclass_fields__
    }
    values["pi_gain_per_k"] = Decimal("0.60")  # above the 0.50 maximum

    with pytest.raises(ParameterOutOfRange, match="muss zwischen"):
        save_control_parameters(session, zone, values, user_id=None, source="api")


def test_pi_eligibility_names_the_reason_before_anything_is_switched_on(
    session: Session,
) -> None:
    """Shown to the operator *before* the switch (specification section 6) -- this
    is the same function `web.daily_views` calls to decide whether to grey out the
    checkbox."""
    create_settings(session)
    zone = create_zone(session, "pi-diagnose")

    without_actuator = pi_eligibility(
        session, zone, pi_min_on_seconds=60, pi_min_off_seconds=60
    )
    assert without_actuator.eligible is False
    assert "Schaltaktor" in without_actuator.reason

    _assign_switch_actuator(session, zone)
    with_actuator = pi_eligibility(
        session, zone, pi_min_on_seconds=60, pi_min_off_seconds=60
    )
    assert with_actuator.eligible is True


def test_pi_state_is_neutral_and_disappears_with_its_zone(session: Session) -> None:
    zone = create_zone(session, "pi-zustand-kaskade")
    state = create_zone_state(session, zone)

    assert state.pi_integral == Decimal("0")
    assert state.pi_time_balance_seconds == Decimal("0")
    assert state.pi_last_evaluated_at is None
    assert state.pi_setpoint_context_key is None
    assert state.pi_last_control_armed is None
    assert state.pi_window_started_at is None
    assert state.pi_window_duty is None
    assert state.pi_last_switch_at is None
    assert state.pi_last_switch_heating is None
    assert state.pi_awaiting_boundary_until is None
    assert state.pi_last_reset_reason is None

    state.pi_integral = Decimal("0.375")
    state.pi_time_balance_seconds = Decimal("42.5")
    state.pi_last_reset_reason = "testzustand"
    session.flush()
    session.execute(delete(Zone).where(Zone.id == zone.id))
    session.flush()

    assert session.scalar(select(func.count()).select_from(ZoneState)) == 0


def test_shadow_diagnostics_are_a_snapshot_not_controller_storage(
    session: Session,
) -> None:
    zone = create_zone(session, "pi-momentaufnahme")
    state = create_zone_state(session, zone)
    state.pi_integral = Decimal("0.4")
    state.pi_time_balance_seconds = Decimal("15")
    decision = create_shadow_decision(session, zone)
    moment = datetime(2026, 9, 2, 12, 15, 0, 123456)
    decision.requested_controller = "pi"
    decision.effective_controller = "pi"
    decision.controller_fallback_reason = None
    decision.pi_error_k = Decimal("0.8")
    decision.pi_proportional_term = Decimal("0.2")
    decision.pi_integral_before = Decimal("0.35")
    decision.pi_integral_after = Decimal("0.4")
    decision.pi_raw_duty = Decimal("0.6")
    decision.pi_frozen_duty = Decimal("0.55")
    decision.pi_window_started_at = moment
    decision.pi_time_balance_before_seconds = Decimal("10")
    decision.pi_time_balance_after_seconds = Decimal("15")
    decision.pi_state_runtime_seconds = Decimal("60")
    decision.pi_integrator_action = "weiter"
    decision.pi_min_duration_decision = "pi_tastgrad_vorrang"
    decision.pi_reset_reason = "sollwertwechsel"
    decision.pi_candidate_would_heat = True
    session.flush()
    decision_id = decision.id
    session.expire(decision)

    loaded = session.get(ShadowDecision, decision_id)
    assert loaded is not None
    assert (
        loaded.requested_controller,
        loaded.effective_controller,
        loaded.controller_fallback_reason,
        loaded.pi_error_k,
        loaded.pi_proportional_term,
        loaded.pi_integral_before,
        loaded.pi_integral_after,
        loaded.pi_raw_duty,
        loaded.pi_frozen_duty,
        loaded.pi_window_started_at,
        loaded.pi_time_balance_before_seconds,
        loaded.pi_time_balance_after_seconds,
        loaded.pi_state_runtime_seconds,
        loaded.pi_integrator_action,
        loaded.pi_min_duration_decision,
        loaded.pi_reset_reason,
        loaded.pi_candidate_would_heat,
    ) == (
        "pi",
        "pi",
        None,
        Decimal("0.8"),
        Decimal("0.2"),
        Decimal("0.35"),
        Decimal("0.4"),
        Decimal("0.6"),
        Decimal("0.55"),
        moment,
        Decimal("10"),
        Decimal("15"),
        Decimal("60"),
        "weiter",
        "pi_tastgrad_vorrang",
        "sollwertwechsel",
        True,
    )

    session.delete(loaded)
    session.flush()
    session.expire(state)
    assert state.pi_integral == Decimal("0.4")
    assert state.pi_time_balance_seconds == Decimal("15")
