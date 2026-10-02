"""Audited wrappers in `domain.control` for Auftrag 8a: plant-wide Notbetrieb
profile/defaults and a thermostat assignment's backup offset.

`sensor_failure_policy.py` itself stays pure (no `user_id`, no audit -- it is
already covered by `tests/test_sensor_failure_policy.py`); this file checks
only the thin audited layer that REST and MCP actually call, the same split
`zone_settings.save_sensor_failure_parameters` already has for the zone side.
"""

from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_settings, create_zone, source, user_with_permissions
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.control import (
    save_sensor_failure_backup_offset,
    save_sensor_failure_defaults,
    save_sensor_failure_profile,
)
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    migration_default_profile_id,
    read_backup_offset,
    read_defaults,
    read_profile,
)

POINTS = (
    CurvePoint(D("-10"), 1200, 600),
    CurvePoint(D("0"), 600, 1200),
    CurvePoint(D("15"), 0, 1800),
)
VALUES = ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, D("1"), POINTS)


def _profile_id(session: Session) -> int:
    save_sensor_failure_profile(session, VALUES, user_id=None)
    return migration_default_profile_id(session)


def test_save_sensor_failure_profile_writes_an_audit_entry(session: Session) -> None:
    source(session, "web")
    user = user_with_permissions(session, "profil-audit", [("setting.manage", None)])
    profile_id = _profile_id(session)
    updated = save_sensor_failure_profile(
        session,
        replace_fixed_on(VALUES, 900),
        profile_id=profile_id,
        user_id=user.id,
    )
    assert updated.values.fixed_on_seconds == 900
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.object_type == "sensor_failure_profile")
        .order_by(AuditEvent.id)
    ).all()
    # Zwei Einträge: `_profile_id` legt das Profil selbst schon über denselben
    # auditierten Pfad an (user_id=None), nur der zweite ist der hier geprüfte.
    assert len(events) == 2
    event = events[-1]
    assert event.object_id == str(profile_id)
    assert event.actor_user_id == user.id
    assert "Notbetriebsprofil" in event.summary


def replace_fixed_on(values: ProfileValues, seconds: int) -> ProfileValues:
    from dataclasses import replace

    return replace(values, fixed_on_seconds=seconds)


def test_an_invalid_profile_is_rejected_and_writes_no_audit_entry(session: Session) -> None:
    source(session, "web")
    profile_id = _profile_id(session)
    before = session.query(AuditEvent).count()
    with pytest.raises(PolicyError, match="fixed_on_seconds"):
        save_sensor_failure_profile(
            session,
            replace_fixed_on(VALUES, -1),
            profile_id=profile_id,
            user_id=None,
        )
    assert session.query(AuditEvent).count() == before
    assert read_profile(session, profile_id).values.fixed_on_seconds == 600


def test_save_sensor_failure_defaults_writes_an_audit_entry_and_applies_values(
    session: Session,
) -> None:
    source(session, "web")
    settings = create_settings(session)
    profile_id = _profile_id(session)
    user = user_with_permissions(session, "vorgaben-audit", [("setting.manage", None)])

    save_sensor_failure_defaults(
        session,
        profile_id=profile_id,
        emergency_setpoint_c=D("18.5"),
        user_id=user.id,
    )

    assert settings.sensor_failure_default_profile_id == profile_id
    assert read_defaults(session).emergency_setpoint_c == D("18.5")
    event = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "setting")
    ).one()
    assert event.actor_user_id == user.id
    assert "Notbetriebsvorgaben" in event.summary


def test_an_unusable_setpoint_is_rejected_and_writes_no_audit_entry(session: Session) -> None:
    source(session, "web")
    create_settings(session)
    profile_id = _profile_id(session)
    before = session.query(AuditEvent).count()

    with pytest.raises(PolicyError, match="emergency_setpoint_c"):
        save_sensor_failure_defaults(
            session, profile_id=profile_id, emergency_setpoint_c=D("3"), user_id=None
        )
    assert session.query(AuditEvent).count() == before


def _thermostat_assignment(session: Session) -> int:
    device = create_device(session, "notbetrieb-thermostat")
    thermostat = DeviceCapability(code="thermostat", label="Thermostat")
    session.add(thermostat)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=thermostat.id))
    actuator_role = session.scalar(select(DeviceRole.id).where(DeviceRole.code == "actuator"))
    if actuator_role is None:
        role_row = DeviceRole(code="actuator", label="Aktor")
        session.add(role_row)
        session.flush()
        actuator_role = role_row.id
    zone = create_zone(session, "notbetrieb-ausgleich")
    assignment = ZoneDevice(
        zone_id=zone.id, device_id=device.id, device_role_id=actuator_role, self_regulating=True
    )
    session.add(assignment)
    session.flush()
    return assignment.id


def test_save_sensor_failure_backup_offset_writes_an_audit_entry(session: Session) -> None:
    source(session, "web")
    assignment_id = _thermostat_assignment(session)
    user = user_with_permissions(session, "ausgleich-audit", [("device.manage", None)])

    save_sensor_failure_backup_offset(session, assignment_id, D("1.5"), user_id=user.id)

    assert read_backup_offset(session, assignment_id) == D("1.5")
    event = session.scalars(
        select(AuditEvent).where(AuditEvent.object_type == "zone_device")
    ).one()
    assert event.object_id == str(assignment_id)
    assert event.actor_user_id == user.id


def test_clearing_a_backup_offset_with_none_is_audited_too(session: Session) -> None:
    source(session, "web")
    assignment_id = _thermostat_assignment(session)
    save_sensor_failure_backup_offset(session, assignment_id, D("2"), user_id=None)

    save_sensor_failure_backup_offset(session, assignment_id, None, user_id=None)

    assert read_backup_offset(session, assignment_id) == D("0")
    assert session.query(AuditEvent).count() == 2
