"""The control page -- operating state, arming, global defaults.

Arming is the only operation in the project that immediately moves a valve.
The tests here therefore check not only that it works, but also that it
**does not** work without the dedicated permission -- and that the way back
into dry run fails on nothing.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    arm,
    check_number,
    save_settings,
    save_solar_location,
)
from thermoctl.domain.sensor_failure_policy import (
    ProfileValues,
    migration_default_profile_id,
    save_profile,
)

ClientBuilder = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _defaults(**overrides: str) -> dict[str, str]:
    values = {field: str(LIMITS[field][0]) for field in LIMITS}
    values["timezone"] = "Europe/Berlin"
    values.update(overrides)
    return values


# --- Domain ---------------------------------------------------------------


def test_checking_a_number_accepts_the_comma() -> None:
    """On a German keyboard you type 0,5 -- that is not a mistake."""
    assert check_number("default_hysteresis_k", "0,5") == Decimal("0.5")


@pytest.mark.parametrize(
    ("field", "input_value"),
    [
        ("default_min_on_seconds", "0"),
        ("default_hysteresis_k", "0"),
        ("shadow_interval_seconds", "0"),
        ("shadow_decision_retention_days", "0"),
        ("shadow_decision_retention_days", "-1"),
        ("shadow_decision_retention_days", "3651"),
        ("default_min_on_seconds", "99999"),
        ("default_hysteresis_k", "keine Zahl"),
        ("default_min_on_seconds", "60,5"),
        ("polling_interval_seconds", ""),
        ("assumed_relay_lifetime_operations", "0"),
        ("assumed_relay_lifetime_operations", "-1"),
        ("assumed_relay_lifetime_operations", "100"),
        ("assumed_relay_lifetime_operations", "1000000000"),
    ],
)
def test_unusable_defaults_are_rejected(field: str, input_value: str) -> None:
    """Zero seconds minimum on-time and zero Kelvin hysteresis are exactly the
    legacy system's defect: cycling at the setpoint on every tick."""
    with pytest.raises(ControlError) as errors:
        check_number(field, input_value)
    assert errors.value.field == field


def test_arming_requires_a_justification(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    with pytest.raises(ControlError):
        arm(session, True, reason="   ", user_id=None)
    assert session.get(Setting, 1).control_armed is False


def test_going_back_to_dry_run_requires_no_justification(session: Session) -> None:
    """The way back is the one someone takes in a hurry. It must not fail on
    any formality."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Schattenlauf geprüft", user_id=None)
    assert arm(session, False, reason="", user_id=None) is True
    assert session.get(Setting, 1).control_armed is False


def test_the_same_thing_twice_does_not_write_a_second_entry(session: Session) -> None:
    """Otherwise the audit log would show an arming that never actually happened."""
    create_settings(session)
    source(session, "web")
    assert arm(session, True, reason="erste", user_id=None) is True
    assert arm(session, True, reason="zweite", user_id=None) is False
    entries = list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "arm"))
    )
    assert len(entries) == 1
    assert entries[0].detail == "erste"


# --- Interface -----------------------------------------------------------


























def test_the_hint_does_not_appear_in_dry_run(
    client_als: ClientBuilder, session: Session
) -> None:
    """Counter-check: without it, the test above would also be satisfied by
    a version that always shows the hint -- and then every page would carry
    a warning that nobody reads any more."""
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/control")
    assert "Der beim Start gebaute MQTT-Riegel ist noch zu." not in page.text


def test_an_empty_timezone_is_refused_with_its_field(session: Session) -> None:
    """The timezone has no sensible default -- an empty one would silently shift every
    schedule, because schedules are stored in local time."""
    create_settings(session)
    with pytest.raises(ControlError) as fehler:
        save_settings(session, {}, "   ", user_id=None)
    assert fehler.value.field == "timezone"


@pytest.mark.parametrize("eingabe", ["keine Zahl", "12,5,7", ""])
def test_a_coordinate_that_is_no_number_names_its_field(
    session: Session, eingabe: str
) -> None:
    """The coordinate arrives as text from a form, so anything can be in it.

    An empty one is the exception and means "no location" -- checked separately in the
    REST tests. Everything else has to come back naming the field, so the page can mark
    it, instead of raising a bare `InvalidOperation`.
    """
    create_settings(session)
    if eingabe == "":
        save_solar_location(
            session, enabled=False, latitude_text="", longitude_text="", user_id=None
        )
        return
    with pytest.raises(ControlError) as fehler:
        save_solar_location(
            session, enabled=True, latitude_text=eingabe, longitude_text="0", user_id=None
        )
    assert fehler.value.field == "solar_forecast_latitude"




# --- Außentemperatur und Fenster-Alarm --------------------------------------
















def test_the_window_alarm_thresholds_never_reach_limits_or_the_rest_schema() -> None:
    """The project owner's explicit instruction: nothing new about the window
    alarm reaches REST or MCP. Both adapters build their field list from
    `LIMITS` (`api/schemas.py::ControlResponse`, `mcp/server.py`), so keeping the
    two thresholds out of `LIMITS` is what keeps them out of both -- this is the
    guard that would catch an accidental merge of `WINDOW_ALARM_LIMITS` into it.
    """
    from thermoctl.api.schemas import ControlResponse
    from thermoctl.domain.control import WINDOW_ALARM_LIMITS

    for field in WINDOW_ALARM_LIMITS:
        assert field not in LIMITS
        assert field not in ControlResponse.model_fields






def test_the_window_temp_drop_thresholds_never_reach_limits_or_the_rest_schema() -> None:
    """Same guard as `test_the_window_alarm_thresholds_never_reach_limits_or_the_rest_schema`
    above, for the temperature-based window detection's own six thresholds."""
    from thermoctl.api.schemas import ControlResponse
    from thermoctl.domain.control import WINDOW_TEMP_DROP_LIMITS

    for field in WINDOW_TEMP_DROP_LIMITS:
        assert field not in LIMITS
        assert field not in ControlResponse.model_fields




# --- Notbetrieb bei Sensorausfall (Auftrag 8a) ------------------------------


def _profile_values(**overrides: object) -> ProfileValues:
    base = dict(
        name="Notbetrieb Vorgabe",
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        curve_points=(),
    )
    base.update(overrides)
    return ProfileValues(**base)  # type: ignore[arg-type]


def _set_profile(session: Session, **overrides: object) -> None:
    """Updates the one Vorgabeprofil `create_settings` already seeds (helpers.py
    mirrors the real migration) -- `save_profile` without a `profile_id` would
    create a *second*, unrelated row with the same name, and
    `migration_default_profile_id` always keeps using the oldest (the bare one
    from `create_settings`), silently ignoring whatever this test just set up."""
    profile_id = migration_default_profile_id(session)
    save_profile(session, _profile_values(**overrides), profile_id=profile_id)










def _minimal_zone_parameter_form(**overrides: str) -> dict[str, str]:
    """A parameter-page POST where every regular field is left blank (= inherit) --
    only the Notbetrieb fields under test actually carry a value."""
    values: dict[str, str] = {}
    values.update(overrides)
    return values














def _ausgleich_assignment(session: Session, zone: Zone) -> int:
    from tests.helpers import capability, create_device, role
    from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice

    device = create_device(session, f"{zone.name}-thermostat")
    thermostat_capability_id = capability(session, "thermostat").id
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=thermostat_capability_id)
    )
    assignment = ZoneDevice(
        zone_id=zone.id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        thermostat_mode="self_regulating",
    )
    session.add(assignment)
    session.flush()
    return assignment.id










