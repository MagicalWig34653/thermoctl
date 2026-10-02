"""MCP contract for Auftrag 8a -- same four tools as REST's two endpoints:
`read/set_sensor_failure_defaults` (anlagenweit) and
`read/set_sensor_failure_policy` (je Zone, inklusive Ausgleichswerten)."""

from decimal import Decimal as D

from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_settings,
    create_zone,
    role,
    user_with_permissions,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.sensor_failure_policy import PolicyError, ProfileValues, save_profile
from thermoctl.mcp import server

POINTS = (
    {"outdoor_c": "-10", "on_seconds": 1200, "off_seconds": 600},
    {"outdoor_c": "0", "on_seconds": 600, "off_seconds": 1200},
    {"outdoor_c": "15", "on_seconds": 0, "off_seconds": 1800},
)
VALUES = ProfileValues("Notbetrieb Vorgabe", 600, 1200, 60, 2, D("1"), ())


def _token(session: Session, name: str, permissions: list[tuple[str, int | None]]) -> str:
    user_record = user_with_permissions(session, name, permissions)
    _objekt, plaintext = issue_token(session, user_record, name, permissions, None)
    return plaintext


def test_read_sensor_failure_defaults_reports_profile_and_setpoint(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    plaintext = _token(session, "nb-defaults-leser", [("zone.read", None)])

    result = server.read_sensor_failure_defaults_tool(session, plaintext)

    assert result["fixed_on_seconds"] == 600
    assert result["curve_points"] == []
    assert result["emergency_setpoint_c"] == "20.00"


def test_set_sensor_failure_defaults_round_trips(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    plaintext = _token(
        session, "nb-defaults-schreiber", [("setting.manage", None), ("zone.read", None)]
    )

    written = server.set_sensor_failure_defaults_tool(
        session,
        plaintext,
        fixed_on_seconds=900,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=D("1"),
        emergency_setpoint_c=D("17.5"),
        curve_points=list(POINTS),
    )
    assert written["fixed_on_seconds"] == 900
    assert [p["outdoor_c"] for p in written["curve_points"]] == ["-10.00", "0.00", "15.00"]

    reread = server.read_sensor_failure_defaults_tool(session, plaintext)
    assert reread["fixed_on_seconds"] == 900
    assert reread["emergency_setpoint_c"] == "17.50"


def test_set_sensor_failure_defaults_needs_setting_manage(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    plaintext = _token(session, "nb-defaults-ohne-recht", [("zone.read", None)])

    try:
        server.set_sensor_failure_defaults_tool(
            session,
            plaintext,
            fixed_on_seconds=900,
            fixed_off_seconds=1200,
            recovery_seconds=60,
            recovery_samples=2,
            warm_restart_hysteresis_k=D("1"),
            emergency_setpoint_c=D("17.5"),
        )
        raised = False
    except Forbidden:
        raised = True
    assert raised


def test_an_invalid_curve_rejects_the_whole_write_and_changes_nothing(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    plaintext = _token(
        session, "nb-defaults-ungueltig", [("setting.manage", None), ("zone.read", None)]
    )

    bad_points = [
        {"outdoor_c": "-10", "on_seconds": 1200, "off_seconds": 600},
        {"outdoor_c": "0", "on_seconds": 600, "off_seconds": 1200},
    ]
    try:
        server.set_sensor_failure_defaults_tool(
            session,
            plaintext,
            fixed_on_seconds=999,
            fixed_off_seconds=1200,
            recovery_seconds=60,
            recovery_samples=2,
            warm_restart_hysteresis_k=D("1"),
            emergency_setpoint_c=D("17.5"),
            curve_points=bad_points,
        )
        raised = False
    except PolicyError:
        raised = True
    assert raised
    assert server.read_sensor_failure_defaults_tool(session, plaintext)["fixed_on_seconds"] == 600


def test_read_set_sensor_failure_policy_round_trip_for_a_zone(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "mcp-notbetrieb-zone")
    plaintext = _token(
        session, "nb-policy-schreiber", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    before = server.read_sensor_failure_policy(session, plaintext, zone.id)
    assert before["enabled"] is False
    assert before["profile_source"] == "Vorgabe"

    written = server.set_sensor_failure_policy(
        session, plaintext, zone.id, enabled=True, emergency_setpoint_c=D("18")
    )
    assert written["enabled"] is True
    assert written["emergency_setpoint_c"] == "18"
    assert written["setpoint_source"] == "Zone"


def test_zone_isolation_hides_a_foreign_zone(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    own = create_zone(session, "mcp-eigene-zone")
    create_zone(session, "mcp-fremde-zone")
    plaintext = _token(session, "nb-policy-isoliert", [("zone.read", own.id)])

    try:
        server.read_sensor_failure_policy(session, plaintext, own.id + 999)
        raised = False
    except LookupError:
        raised = True
    assert raised


def _thermostat_assignment(session: Session, zone_id: int) -> int:
    device = create_device(session, "mcp-notbetrieb-thermostat")
    thermostat_capability_id = capability(session, "thermostat").id
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=thermostat_capability_id)
    )
    assignment = ZoneDevice(
        zone_id=zone_id,
        device_id=device.id,
        device_role_id=role(session, "actuator").id,
        self_regulating=True,
    )
    session.add(assignment)
    session.flush()
    return assignment.id


def test_backup_offsets_need_device_manage(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "mcp-ausgleich-zone")
    assignment_id = _thermostat_assignment(session, zone.id)
    plaintext = _token(
        session, "nb-ausgleich-ohne-recht", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    try:
        server.set_sensor_failure_policy(
            session,
            plaintext,
            zone.id,
            enabled=False,
            backup_offsets={assignment_id: D("1.5")},
        )
        raised = False
    except Forbidden:
        raised = True
    assert raised


def test_backup_offsets_round_trip_with_device_manage(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "mcp-ausgleich-zone-2")
    assignment_id = _thermostat_assignment(session, zone.id)
    plaintext = _token(
        session,
        "nb-ausgleich-mit-recht",
        [("zone.read", zone.id), ("zone.manage", zone.id), ("device.manage", zone.id)],
    )

    written = server.set_sensor_failure_policy(
        session,
        plaintext,
        zone.id,
        enabled=False,
        backup_offsets={assignment_id: D("1.5")},
    )
    offsets = {
        o["zone_device_id"]: o["temperature_backup_offset_k"] for o in written["backup_offsets"]
    }
    assert offsets[assignment_id] == "1.50"


def test_an_unknown_backup_offset_assignment_is_rejected(session: Session) -> None:
    create_settings(session)
    save_profile(session, VALUES)
    zone = create_zone(session, "mcp-fremder-ausgleich-zone")
    other_zone = create_zone(session, "mcp-andere-zone")
    foreign_assignment_id = _thermostat_assignment(session, other_zone.id)
    plaintext = _token(
        session,
        "nb-ausgleich-fremd",
        [("zone.read", zone.id), ("zone.manage", zone.id), ("device.manage", zone.id)],
    )

    try:
        server.set_sensor_failure_policy(
            session,
            plaintext,
            zone.id,
            enabled=False,
            backup_offsets={foreign_assignment_id: D("1")},
        )
        raised = False
    except ValueError:
        raised = True
    assert raised
