"""Der Raum-Schalter "Ausfallbenachrichtigungen" (`zone.fault_notifications_enabled`).

Domänenbaustein (`domain/fault_notice.py`) und seine Auflösung von Meldung zu Zonen
(`services/notice_routing.py`). Der Schalter wirkt nur auf die Zustellung; im Zweifel
(keine Zone auffindbar) wird zugestellt.
"""

import itertools

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_zone
from tests.helpers import role as device_role
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device, ZoneDevice
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.sensor_failure import SensorFailureEpisode
from thermoctl.db.models.zone import Zone
from thermoctl.domain.fault_notice import (
    AUDIT_ACTION_NOTIFICATION_SENT,
    AUDIT_ACTION_NOTIFICATION_SUPPRESSED,
    NOTICE_KIND_BRIDGE_FAULT,
    NOTICE_KIND_COMMAND_FAILURE,
    NOTICE_KIND_SENSOR_FAULT,
    NOTICE_KIND_STUCK_SENSOR,
    NOTICE_KIND_TENANT_REPORT,
    NOTICE_KIND_WINDOW_ALARM,
    FaultNotice,
    notice_enabled,
    notification_audit_action,
    zones_allow_notice,
)
from thermoctl.services.notice_routing import (
    notice_deliverable,
    notice_zone_flags,
    notification_audit_action_for,
)

ZONE_KINDS = (
    NOTICE_KIND_SENSOR_FAULT,
    NOTICE_KIND_STUCK_SENSOR,
    NOTICE_KIND_WINDOW_ALARM,
    NOTICE_KIND_COMMAND_FAILURE,
)
SETTING_FIELD = {
    NOTICE_KIND_SENSOR_FAULT: "notify_sensor_faults",
    NOTICE_KIND_BRIDGE_FAULT: "notify_bridge_faults",
    NOTICE_KIND_COMMAND_FAILURE: "notify_command_failures",
    NOTICE_KIND_STUCK_SENSOR: "notify_stuck_sensor",
    NOTICE_KIND_WINDOW_ALARM: "notify_window_alarm",
    NOTICE_KIND_TENANT_REPORT: "notify_tenant_reports",
}


def _setting(**overrides: bool) -> Setting:
    row = Setting()
    for field in SETTING_FIELD.values():
        setattr(row, field, True)
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


@pytest.mark.parametrize("kind", ZONE_KINDS)
def test_zone_kinds_follow_the_zone_flags(kind: str) -> None:
    assert zones_allow_notice(kind, [True])
    assert not zones_allow_notice(kind, [False])
    # Mehrere Zonen: nur unterdrückt, wenn ALLE abgeschaltet sind.
    assert zones_allow_notice(kind, [False, True])
    assert not zones_allow_notice(kind, [False, False])
    # Keine Zone auffindbar: zustellen (fail-open).
    assert zones_allow_notice(kind, [])


@pytest.mark.parametrize("kind", [NOTICE_KIND_BRIDGE_FAULT, NOTICE_KIND_TENANT_REPORT])
def test_plant_wide_and_tenant_notices_ignore_zone_flags(kind: str) -> None:
    assert zones_allow_notice(kind, [False])
    assert notice_enabled(kind, _setting(), [False])


@pytest.mark.parametrize(
    ("kind", "plant_on", "zone_on"),
    list(itertools.product(SETTING_FIELD, (True, False), (True, False))),
)
def test_matrix_plant_switch_and_zone_switch_both_have_to_be_on(
    kind: str, plant_on: bool, zone_on: bool
) -> None:
    row = _setting(**{SETTING_FIELD[kind]: plant_on})
    zone_scoped = kind in ZONE_KINDS
    expected = plant_on and (zone_on or not zone_scoped)
    assert notice_enabled(kind, row, [zone_on]) is expected
    assert notification_audit_action(kind, row, [zone_on]) == (
        AUDIT_ACTION_NOTIFICATION_SENT if expected else AUDIT_ACTION_NOTIFICATION_SUPPRESSED
    )


def test_notice_enabled_without_flags_is_unchanged() -> None:
    assert notice_enabled(NOTICE_KIND_SENSOR_FAULT, _setting())
    assert not notice_enabled(NOTICE_KIND_SENSOR_FAULT, _setting(notify_sensor_faults=False))


def test_audit_action_without_setting_row_still_follows_zone_switch() -> None:
    assert (
        notification_audit_action(NOTICE_KIND_WINDOW_ALARM, None, [False])
        == AUDIT_ACTION_NOTIFICATION_SUPPRESSED
    )
    assert (
        notification_audit_action(NOTICE_KIND_WINDOW_ALARM, None, [])
        == AUDIT_ACTION_NOTIFICATION_SENT
    )


# -- Auflösung Meldung -> Zonen ----------------------------------------------------------


def _notice(key: str, kind: str) -> FaultNotice:
    return FaultNotice(key=key, severity="stoerung", title="t", text="x", kind=kind)


def _zone(session: Session, name: str, *, enabled: bool = True) -> Zone:
    zone = create_zone(session, name)
    zone.fault_notifications_enabled = enabled
    session.flush()
    return zone


def _actuator(session: Session, name: str, zones: list[Zone], role: str = "actuator") -> Device:
    device = create_device(session, name)
    role_row = device_role(session, role)
    for zone in zones:
        session.add(ZoneDevice(zone_id=zone.id, device_id=device.id, device_role_id=role_row.id))
    session.flush()
    return device


def test_sensor_window_and_stuck_keys_resolve_to_their_zone(session: Session) -> None:
    off = _zone(session, "aus", enabled=False)
    on = _zone(session, "an")
    for prefix in ("sensor", "fenster"):
        muted = _notice(f"{prefix}:{off.id}", NOTICE_KIND_SENSOR_FAULT)
        unmuted = _notice(f"{prefix}:{on.id}", NOTICE_KIND_SENSOR_FAULT)
        assert notice_zone_flags(session, muted) == [False]
        assert notice_zone_flags(session, unmuted) == [True]


def test_emergency_key_resolves_through_the_episode(session: Session) -> None:
    off = _zone(session, "aus", enabled=False)
    episode = SensorFailureEpisode(
        zone_id=off.id,
        zone_name=off.name,
        started_at=utcnow(),
        trigger_kind="wandfuehler",
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=1,
        emergency_setpoint_c=20,
        sensor_timeout_seconds=1800,
        notification_state="offen",
    )
    session.add(episode)
    session.flush()
    notice = _notice(f"notbetrieb:{episode.id}", NOTICE_KIND_SENSOR_FAULT)
    assert notice_zone_flags(session, notice) == [False]
    assert not notice_deliverable(session, notice, _setting())


def test_emergency_episode_without_zone_is_delivered(session: Session) -> None:
    episode = SensorFailureEpisode(
        zone_id=None,
        zone_name="weg",
        started_at=utcnow(),
        trigger_kind="wandfuehler",
        profile_version=1,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=1,
        emergency_setpoint_c=20,
        sensor_timeout_seconds=1800,
        notification_state="offen",
    )
    session.add(episode)
    session.flush()
    notice = _notice(f"notbetrieb:{episode.id}", NOTICE_KIND_SENSOR_FAULT)
    assert notice_zone_flags(session, notice) == []
    assert notice_deliverable(session, notice, _setting())


def test_command_failure_device_without_zone_is_delivered(session: Session) -> None:
    device = _actuator(session, "frei", [])
    notice = _notice(f"schaltbefehl:{device.id}", NOTICE_KIND_COMMAND_FAILURE)
    assert notice_zone_flags(session, notice) == []
    assert notice_deliverable(session, notice, _setting())


def test_command_failure_device_in_one_zone_follows_that_zone(session: Session) -> None:
    off = _zone(session, "aus", enabled=False)
    device = _actuator(session, "relais", [off])
    notice = _notice(f"schaltbefehl:{device.id}", NOTICE_KIND_COMMAND_FAILURE)
    assert notice_zone_flags(session, notice) == [False]
    assert not notice_deliverable(session, notice, _setting())
    off.fault_notifications_enabled = True
    session.flush()
    assert notice_deliverable(session, notice, _setting())


def test_command_failure_device_in_several_zones_is_muted_only_if_all_are_off(
    session: Session,
) -> None:
    first = _zone(session, "eins", enabled=False)
    second = _zone(session, "zwei", enabled=False)
    device = _actuator(session, "gemeinsam", [first, second])
    notice = _notice(f"schaltbefehl:{device.id}", NOTICE_KIND_COMMAND_FAILURE)
    assert sorted(notice_zone_flags(session, notice)) == [False, False]
    assert not notice_deliverable(session, notice, _setting())
    second.fault_notifications_enabled = True
    session.flush()
    assert notice_deliverable(session, notice, _setting())


def test_sensor_role_assignment_does_not_count_for_command_failures(session: Session) -> None:
    off = _zone(session, "aus", enabled=False)
    device = _actuator(session, "fuehler", [off], role="window_contact")
    notice = _notice(f"schaltbefehl:{device.id}", NOTICE_KIND_COMMAND_FAILURE)
    assert notice_zone_flags(session, notice) == []


@pytest.mark.parametrize(
    "key", ["sensor:abc", "sensor:", "sensor:99999", "fenster:99999", "schaltbefehl:99999",
            "schaltbefehl:abc", "notbetrieb:abc", "notbetrieb:99999", "unbekannt:1", "zigbee2mqtt:brücke", ""]
)
def test_unresolvable_keys_fail_open(session: Session, key: str) -> None:
    notice = _notice(key, NOTICE_KIND_SENSOR_FAULT)
    assert notice_zone_flags(session, notice) == []
    assert notice_deliverable(session, notice, _setting())


def test_plant_switch_still_applies_with_zone_on(session: Session) -> None:
    on = _zone(session, "an")
    notice = _notice(f"sensor:{on.id}", NOTICE_KIND_SENSOR_FAULT)
    assert not notice_deliverable(session, notice, _setting(notify_sensor_faults=False))
    assert notice_deliverable(session, notice, None)


def test_audit_action_for_uses_zone_switch(session: Session) -> None:
    off = _zone(session, "aus", enabled=False)
    notice = _notice(f"sensor:{off.id}", NOTICE_KIND_SENSOR_FAULT)
    assert notification_audit_action_for(session, notice, _setting()) == (
        AUDIT_ACTION_NOTIFICATION_SUPPRESSED
    )
    off.fault_notifications_enabled = True
    session.flush()
    assert notification_audit_action_for(session, notice, _setting()) == (
        AUDIT_ACTION_NOTIFICATION_SENT
    )
