"""Konfigurationsverträge: fehlerhafte Eingaben dürfen nie Teiländerungen hinterlassen."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select

from tests.helpers import capability, create_device, create_settings, create_zone, role
from tests.test_migrations import _alembic
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import SensorFailureProfile
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    effective_policy,
    migration_default_profile_id,
    read_backup_offset,
    read_defaults,
    read_profile,
    read_zone_policy,
    save_backup_offset,
    save_defaults,
    save_profile,
    save_zone_policy,
)
from thermoctl.domain.zone_settings import save_sensor_failure_parameters, sensor_failure_parameters
from thermoctl.setup import create_setup_token, run_setup

POINTS = (
    CurvePoint(D("-10"), 1200, 600),
    CurvePoint(D("0"), 600, 1200),
    CurvePoint(D("15"), 0, 1800),
)
VALUES = ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, D("1"), POINTS)


@pytest.fixture
def policy(session):
    profile = save_profile(session, VALUES)
    settings = create_settings(session)
    zone = create_zone(session, "zimmer")
    return profile, settings, zone


def test_inheritance_and_sources_are_independent(session, policy):
    profile, settings, zone = policy
    result = effective_policy(session, zone)
    assert result.profile.id == profile.id
    assert result.profile_source == "Vorgabe"
    assert result.emergency_setpoint_c == D("20")
    assert result.setpoint_source == "Anlage"
    assert result.enabled is False and result.enabled_source == "Zone"
    assert result.profile.values.curve_points == POINTS
    save_defaults(session, profile_id=profile.id, emergency_setpoint_c=D("21.5"))
    assert effective_policy(session, zone).profile_source == "Anlage"
    assert effective_policy(session, zone).emergency_setpoint_c == D("21.5")
    own = save_profile(session, replace(VALUES, name="Eigen", recovery_samples=3))
    save_zone_policy(session, zone, enabled=True, profile_id=own.id, emergency_setpoint_c=D("18"))
    result = sensor_failure_parameters(session, zone)
    assert result.profile.values.recovery_samples == 3
    assert result.profile_source == result.setpoint_source == "Zone"
    assert result.emergency_setpoint_c == D("18") and result.enabled
    save_zone_policy(session, zone, enabled=False, profile_id=None, emergency_setpoint_c=None)
    assert effective_policy(session, zone).emergency_setpoint_c == D("21.5")
    assert read_zone_policy(session, zone).profile_id is None
    assert read_defaults(session).profile_id == profile.id
    with pytest.raises(FrozenInstanceError):
        result.enabled = False
    with pytest.raises(FrozenInstanceError):
        result.profile.values.curve_points[0].on_seconds = 0


def test_fallback_uses_oldest_named_profile_and_never_an_arbitrary_one(session):
    unrelated = save_profile(session, replace(VALUES, name="Früher"))
    with pytest.raises(PolicyError, match="Vorgabeprofil") as error:
        migration_default_profile_id(session)
    assert error.value.field == "sensor_failure_default_profile_id"
    first = save_profile(session, VALUES)
    save_profile(session, VALUES)
    assert unrelated.id < first.id
    assert migration_default_profile_id(session) == first.id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixed_on_seconds", 0),
        ("fixed_on_seconds", -1),
        ("fixed_on_seconds", 59),
        ("fixed_on_seconds", 7201),
        ("fixed_on_seconds", 60.5),
        ("fixed_off_seconds", -1),
        ("fixed_off_seconds", 0),
        ("fixed_off_seconds", 59),
        ("fixed_off_seconds", 7201),
        ("recovery_seconds", -1),
        ("recovery_seconds", 0.5),
        ("recovery_samples", 0),
        ("recovery_samples", -1),
        ("recovery_samples", 1.5),
        ("warm_restart_hysteresis_k", D("-0.01")),
        ("warm_restart_hysteresis_k", D("NaN")),
        ("warm_restart_hysteresis_k", D("100")),
        ("warm_restart_hysteresis_k", D("0.001")),
        ("name", ""),
        ("name", "x" * 129),
    ],
)
def test_invalid_profile_is_atomic_even_when_caller_commits(session, policy, field, value):
    profile, _, _ = policy
    changed = (
        replace(VALUES, name="Geändert", **{field: value})
        if field != "name"
        else replace(VALUES, name=value)
    )
    with pytest.raises(PolicyError) as error:
        save_profile(session, changed, profile_id=profile.id)
    assert error.value.field == field
    assert str(error.value)
    session.commit()
    session.expire_all()
    assert read_profile(session, profile.id) == profile


@pytest.mark.parametrize(
    "points",
    [
        POINTS[:1],
        POINTS + (POINTS[0],),
        POINTS[:2],
        (CurvePoint(D("-10"), 0, 600), POINTS[-1]),
        (CurvePoint(D("-10"), 600, 1200), CurvePoint(D("0"), 1200, 600), POINTS[-1]),
    ],
)
def test_invalid_curve_shape_preserves_profile_and_points(session, policy, points):
    profile, _, _ = policy
    with pytest.raises(PolicyError) as error:
        save_profile(session, replace(VALUES, curve_points=points), profile_id=profile.id)
    assert error.value.field.startswith("curve_points")
    session.commit()
    assert read_profile(session, profile.id) == profile


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("on_seconds", -1),
        ("on_seconds", 59),
        ("on_seconds", 7201),
        ("off_seconds", -1),
        ("off_seconds", 0),
        ("off_seconds", 59),
        ("off_seconds", 7201),
        ("outdoor_c", D("NaN")),
        ("outdoor_c", D("Infinity")),
        ("outdoor_c", D("1000")),
        ("outdoor_c", D("0.001")),
    ],
)
def test_invalid_curve_point_names_field(session, field, value):
    point = replace(POINTS[0], **{field: value})
    with pytest.raises(PolicyError) as error:
        save_profile(session, replace(VALUES, curve_points=(point, POINTS[-1])))
    assert error.value.field == f"curve_points[0].{field}"
    assert session.scalar(select(SensorFailureProfile.id)) is None


def test_time_boundaries_empty_curve_and_equal_duty_cycle(session):
    points = (
        CurvePoint(D("15"), 0, 60),
        CurvePoint(D("0"), 7200, 7200),
        CurvePoint(D("-10"), 60, 60),
    )
    profile = save_profile(
        session,
        replace(
            VALUES,
            fixed_on_seconds=60,
            fixed_off_seconds=7200,
            recovery_seconds=0,
            recovery_samples=1,
            warm_restart_hysteresis_k=D("0"),
            curve_points=points,
        ),
    )
    session.expire_all()
    assert read_profile(session, profile.id).values.curve_points == tuple(reversed(points))
    updated = save_profile(
        session,
        replace(VALUES, fixed_on_seconds=7200, fixed_off_seconds=60, curve_points=()),
        profile_id=profile.id,
    )
    assert updated.version == profile.version + 1
    assert read_profile(session, profile.id).values.curve_points == ()
    assert profile.values.curve_points == tuple(reversed(points))


@pytest.mark.parametrize("value", [D("5"), D("30"), D("20.5")])
def test_setpoint_boundaries_and_steps(session, policy, value):
    profile, _, zone = policy
    save_defaults(session, profile_id=profile.id, emergency_setpoint_c=value)
    assert effective_policy(session, zone).emergency_setpoint_c == value
    save_zone_policy(session, zone, enabled=True, profile_id=None, emergency_setpoint_c=value)
    assert effective_policy(session, zone).emergency_setpoint_c == value


@pytest.mark.parametrize("value", [D("4.5"), D("30.5"), D("20.1"), D("NaN"), D("Infinity")])
def test_invalid_setpoint_keeps_all_other_changes_out(session, policy, value):
    profile, settings, zone = policy
    with pytest.raises(PolicyError) as error:
        save_defaults(session, profile_id=profile.id, emergency_setpoint_c=value)
    assert error.value.field == "emergency_setpoint_c"
    with pytest.raises(PolicyError):
        save_zone_policy(
            session, zone, enabled=True, profile_id=profile.id, emergency_setpoint_c=value
        )
    session.commit()
    session.expire_all()
    assert settings.sensor_failure_default_profile_id is None
    assert settings.sensor_failure_default_emergency_setpoint_c == D("20")
    assert zone.sensor_failure_enabled is False
    assert zone.sensor_failure_profile_id is None


def test_missing_references_are_domain_errors(session, policy):
    profile, _, zone = policy
    missing = profile.id + 1000
    for action in (
        lambda: read_profile(session, missing),
        lambda: save_profile(session, VALUES, profile_id=missing),
        lambda: save_defaults(session, profile_id=missing, emergency_setpoint_c=D("20")),
        lambda: save_zone_policy(
            session, zone, enabled=True, profile_id=missing, emergency_setpoint_c=None
        ),
    ):
        with pytest.raises(PolicyError) as error:
            action()
        assert error.value.field == "profile_id"
    assert not zone.sensor_failure_enabled


def test_missing_settings_is_explained(session):
    with pytest.raises(PolicyError, match="Einrichtung"):
        read_defaults(session)


def assignment(session, *, capabilities=("thermostat",), role_code="actuator"):
    zone = create_zone(session, "offset")
    device = create_device(session, "trv")
    for code in capabilities:
        session.add(
            DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, code).id)
        )
    row = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=role(session, role_code).id
    )
    session.add(row)
    session.flush()
    return row


def test_offset_belongs_to_assignment_and_none_means_zero(session):
    first = assignment(session)
    second = ZoneDevice(
        zone_id=create_zone(session, "zweites").id,
        device_id=first.device_id,
        device_role_id=first.device_role_id,
    )
    session.add(second)
    session.flush()
    assert read_backup_offset(session, first.id) == D("0")
    save_backup_offset(session, first.id, D("-2.35"))
    session.flush()
    session.expire_all()
    assert read_backup_offset(session, first.id) == D("-2.35")
    assert read_backup_offset(session, second.id) == D("0")
    save_backup_offset(session, first.id, None)
    assert read_backup_offset(session, first.id) == D("0")


@pytest.mark.parametrize(
    ("capabilities", "role_code"),
    [
        (("switch",), "actuator"),
        (("temperature",), "temperature_source"),
        (("thermostat",), "temperature_source"),
        (("thermostat", "switch"), "actuator"),
    ],
)
def test_offset_rejects_non_thermostat_assignment(session, capabilities, role_code):
    row = assignment(session, capabilities=capabilities, role_code=role_code)
    with pytest.raises(PolicyError, match="Thermostat"):
        save_backup_offset(session, row.id, D("1"))
    assert row.temperature_backup_offset_k == D("0")


@pytest.mark.parametrize("value", [D("NaN"), D("100"), D("-100"), D("0.001")])
def test_offset_rejects_unrepresentable_values_without_rounding(session, value):
    row = assignment(session)
    with pytest.raises(PolicyError) as error:
        save_backup_offset(session, row.id, value)
    assert error.value.field == "temperature_backup_offset_k"
    assert read_backup_offset(session, row.id) == D("0")


def test_missing_assignment(session):
    with pytest.raises(PolicyError, match="Zuordnung"):
        read_backup_offset(session, 9999)


def test_zone_settings_domain_connection(session, policy):
    profile, _, zone = policy
    save_sensor_failure_parameters(
        session,
        zone,
        enabled=True,
        profile_id=profile.id,
        emergency_setpoint_c=D("19"),
        user_id=None,
    )
    assert sensor_failure_parameters(session, zone).emergency_setpoint_c == D("19")


@pytest.mark.migration
def test_new_installation_setup_links_real_migration_profile(migrations_database_url):
    from sqlalchemy.orm import Session

    for args in [("downgrade", "base"), ("upgrade", "head")]:
        result = _alembic(migrations_database_url, *args)
        assert result.returncode == 0, result.stderr
    engine = create_engine(migrations_database_url)
    try:
        with Session(engine) as session:
            assert session.get(Setting, 1) is None
            profile_id = migration_default_profile_id(session)
            run_setup(
                session,
                username="admin",
                display_name="Admin",
                password="lang-genug-passwort",
                timezone_name="Europe/Berlin",
                token=create_setup_token(session),
            )
            session.flush()
            session.expire_all()
            assert session.get(Setting, 1).sensor_failure_default_profile_id == profile_id
            result = effective_policy(session, create_zone(session, "neu"))
            assert result.profile_source == "Anlage"
            assert result.profile.values.curve_points == POINTS
            assert result.enabled is False
    finally:
        engine.dispose()


@pytest.mark.parametrize(("field", "value"), [("fixed_on_seconds", 60), ("fixed_off_seconds", 60)])
def test_enabled_zone_rejects_fixed_cycle_shorter_than_minimum(session, policy, field, value):
    _, _, zone = policy
    short = save_profile(session, replace(VALUES, **{field: value}))
    with pytest.raises(PolicyError, match="Mindest"):
        save_zone_policy(
            session, zone, enabled=True, profile_id=short.id, emergency_setpoint_c=None
        )
    assert not zone.sensor_failure_enabled


def test_active_profile_edit_and_default_change_are_checked_before_mutation(session, policy):
    profile, settings, zone = policy
    save_zone_policy(session, zone, enabled=True, profile_id=None, emergency_setpoint_c=None)
    with pytest.raises(PolicyError, match="Mindest"):
        save_profile(session, replace(VALUES, fixed_on_seconds=60), profile_id=profile.id)
    assert read_profile(session, profile.id) == profile
    short = save_profile(session, replace(VALUES, fixed_off_seconds=60))
    with pytest.raises(PolicyError, match="Mindest"):
        save_defaults(session, profile_id=short.id, emergency_setpoint_c=D("18"))
    assert settings.sensor_failure_default_profile_id is None
    assert settings.sensor_failure_default_emergency_setpoint_c == D("20")


def test_minimum_and_interval_changes_revalidate_active_profiles(session, policy):
    from thermoctl.domain.control import LIMITS, ControlError, save_settings
    from thermoctl.domain.zone_settings import ParameterOutOfRange, save_control_parameters

    _, settings, zone = policy
    save_zone_policy(session, zone, enabled=True, profile_id=None, emergency_setpoint_c=None)
    with pytest.raises(ParameterOutOfRange, match="Mindest"):
        save_control_parameters(session, zone, {"min_on_seconds": 700}, user_id=None)
    assert zone.min_on_seconds is None
    original_interval = settings.shadow_interval_seconds
    values = {name: str(getattr(settings, name)) for name in LIMITS}
    values["shadow_interval_seconds"] = "700"
    with pytest.raises(ControlError, match="Mindest"):
        save_settings(session, values, "Europe/Berlin", user_id=None)
    assert settings.shadow_interval_seconds == original_interval
    values["shadow_interval_seconds"] = str(original_interval)
    values["default_min_off_seconds"] = "1300"
    with pytest.raises(ControlError, match="Mindest"):
        save_settings(session, values, "Europe/Berlin", user_id=None)
    save_control_parameters(session, zone, {"min_on_seconds": 600}, user_id=None)
    assert zone.min_on_seconds == 600


def test_pi_minimum_is_respected_even_if_hysteresis_minimum_is_shorter(session, policy):
    profile, _, zone = policy
    # Bereits aktivierte PI-Konfiguration; deren Eignung prüft der bestehende PI-Dienst.
    profile = save_profile(session, replace(VALUES, fixed_on_seconds=120))
    zone.min_on_seconds = 60
    zone.pi_enabled = True
    zone.pi_min_on_seconds = 180
    with pytest.raises(PolicyError, match='180'):
        save_zone_policy(session, zone, enabled=True, profile_id=profile.id,
                         emergency_setpoint_c=None)
    zone.pi_min_on_seconds = 120
    save_zone_policy(session, zone, enabled=True, profile_id=profile.id,
                     emergency_setpoint_c=None)
    assert effective_policy(session, zone).enabled


def test_curve_replacement_reuses_temperatures_and_updates_inheriting_zones(session, policy):
    profile, _, zone = policy
    save_defaults(session, profile_id=None, emergency_setpoint_c=D('22'))
    save_zone_policy(session, zone, enabled=True, profile_id=None, emergency_setpoint_c=D('19'))
    points = (CurvePoint(D('-10'), 600, 600), CurvePoint(D('15'), 0, 1200))
    updated = save_profile(session, replace(VALUES, curve_points=points), profile_id=profile.id)
    session.flush()
    session.expire_all()
    result = effective_policy(session, zone)
    assert result.profile.version == updated.version == profile.version + 1
    assert result.profile.values.curve_points == points
    assert result.profile_source == 'Vorgabe'
    assert result.setpoint_source == 'Zone' and result.emergency_setpoint_c == D('19')


def test_invalid_zone_wrapper_does_not_write_audit_or_enable_zone(session, policy):
    from thermoctl.db.models.operations import AuditEvent

    profile, _, zone = policy
    before = list(session.scalars(select(AuditEvent.id)))
    with pytest.raises(PolicyError):
        save_sensor_failure_parameters(session, zone, enabled=True, profile_id=profile.id,
                                       emergency_setpoint_c=D('20.2'), user_id=None)
    session.commit()
    assert list(session.scalars(select(AuditEvent.id))) == before
    assert not zone.sensor_failure_enabled
