"""`services.temperature_source_health` -- the DB-backed lookups.

Not the rule (that is `domain.temperature_source_health`, tested purely in
`tests/test_temperature_source_health.py`): this covers only the two impure
things the rule needs from the database -- the moment of the last successful
external-temperature write, and which of a zone's device assignments are
actually eligible candidates at all (same rule as
`domain.sensor_failure_policy._assignment`).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_device_command, create_zone, role
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.measurement import Measurement
from thermoctl.services.temperature_source_health import (
    last_external_temperature_write_at,
    zone_candidates,
)

NOW = datetime(2026, 9, 29, 12, 0, 0)


def _link(session: Session, device_id: int, code: str) -> None:
    session.add(
        DeviceCapabilityLink(device_id=device_id, capability_id=capability(session, code).id)
    )
    session.flush()


def _measurement(session: Session, device_id: int, *, value: Decimal, at: datetime) -> None:
    temperature = capability(session, "temperature")
    session.add(
        Measurement(
            device_id=device_id,
            capability_id=temperature.id,
            value_numeric=value,
            measured_at=at,
            received_at=at,
        )
    )
    session.flush()


# --- last_external_temperature_write_at -------------------------------------


def test_no_command_history_means_no_write(session: Session) -> None:
    device = create_device(session, "trv-1")

    assert last_external_temperature_write_at(session, device.id) is None


def test_a_setpoint_without_a_temperature_key_is_not_counted(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-2")
    create_device_command(
        session,
        zone,
        device,
        at=NOW,
        payload='{"occupied_heating_setpoint": 20.0}',
    )

    assert last_external_temperature_write_at(session, device.id) is None


def test_an_executed_write_with_remote_temperature_is_found(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-3")
    create_device_command(
        session,
        zone,
        device,
        at=NOW,
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    assert last_external_temperature_write_at(session, device.id) == NOW


def test_a_suppressed_dry_run_attempt_does_not_count_as_a_write(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-4")
    create_device_command(
        session,
        zone,
        device,
        at=NOW,
        outcome_code="suppressed",
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    assert last_external_temperature_write_at(session, device.id) is None


def test_a_failed_attempt_does_not_count_as_a_write(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-5")
    create_device_command(
        session,
        zone,
        device,
        at=NOW,
        outcome_code="failed",
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    assert last_external_temperature_write_at(session, device.id) is None


def test_the_most_recent_matching_write_wins(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-6")
    earlier = datetime(2026, 9, 29, 10, 0, 0)
    create_device_command(
        session,
        zone,
        device,
        at=earlier,
        payload='{"occupied_heating_setpoint":19.0,"remote_temperature":22.0}',
    )
    create_device_command(
        session,
        zone,
        device,
        at=NOW,
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    assert last_external_temperature_write_at(session, device.id) == NOW


def test_a_write_to_a_different_device_does_not_count(session: Session) -> None:
    zone = create_zone(session, "bad")
    device = create_device(session, "trv-7")
    other = create_device(session, "trv-8")
    create_device_command(
        session,
        zone,
        other,
        at=NOW,
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    assert last_external_temperature_write_at(session, device.id) is None


# --- zone_candidates ----------------------------------------------------------


def test_a_switching_actuator_is_never_a_candidate(session: Session) -> None:
    zone = create_zone(session, "linos-zimmer")
    switch = create_device(session, "relais")
    _link(session, switch.id, "switch")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=switch.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()

    assert zone_candidates(session, zone, NOW) == []


def test_a_thermostat_actuator_with_both_capabilities_is_excluded_like_the_offset_check(
    session: Session,
) -> None:
    # Same exclusion `domain.sensor_failure_policy._assignment` applies for the
    # backup-offset field -- kept in lockstep on purpose (module docstring).
    zone = create_zone(session, "misch")
    mixed = create_device(session, "misch-geraet")
    _link(session, mixed.id, "thermostat")
    _link(session, mixed.id, "switch")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=mixed.id,
            device_role_id=role(session, "actuator").id,
        )
    )
    session.flush()

    assert zone_candidates(session, zone, NOW) == []


def test_a_thermostat_actuator_becomes_a_candidate_with_its_latest_measurement(
    session: Session,
) -> None:
    zone = create_zone(session, "bad")
    trv = create_device(session, "bad-trv")
    trv.display_name = "Bad Thermostat Heizköper"
    _link(session, trv.id, "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=True,
            temperature_backup_offset_k=Decimal("1.5"),
        )
    )
    session.flush()
    older = datetime(2026, 9, 29, 11, 0, 0)
    _measurement(session, trv.id, value=Decimal("22.0"), at=older)
    _measurement(session, trv.id, value=Decimal("23.4"), at=NOW)

    result = zone_candidates(session, zone, NOW)

    assert len(result) == 1
    candidate = result[0]
    assert candidate.device_id == trv.id
    assert candidate.device_name == "Bad Thermostat Heizköper"
    assert candidate.local_temperature_c == Decimal("23.4")
    assert candidate.measured_at == NOW
    assert candidate.offset_k == Decimal("1.5")
    assert candidate.last_external_write_at is None


def test_candidate_without_an_offset_defaults_to_zero(session: Session) -> None:
    zone = create_zone(session, "bad")
    trv = create_device(session, "bad-trv-2")
    _link(session, trv.id, "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
            temperature_backup_offset_k=None,
        )
    )
    session.flush()

    result = zone_candidates(session, zone, NOW)

    assert result[0].offset_k == Decimal("0")


def test_candidate_carries_its_last_external_write_moment(session: Session) -> None:
    zone = create_zone(session, "bad")
    trv = create_device(session, "bad-trv-3")
    _link(session, trv.id, "thermostat")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=trv.id,
            device_role_id=role(session, "actuator").id,
        )
    )
    session.flush()
    create_device_command(
        session,
        zone,
        trv,
        at=NOW,
        payload='{"occupied_heating_setpoint":20.0,"remote_temperature":23.4}',
    )

    result = zone_candidates(session, zone, NOW)

    assert result[0].last_external_write_at == NOW


def test_a_device_not_assigned_as_an_actuator_at_all_is_not_a_candidate(session: Session) -> None:
    zone = create_zone(session, "lillys-zimmer")
    trv = create_device(session, "lillys-trv")
    _link(session, trv.id, "thermostat")
    # `zone.temperature_source_device_id` would point at this device, but it
    # has no `zone_device` row at all -- exactly the real "Lillys Zimmer" case
    # documented in `lokal/plaene/0.11.0-geraetevertrag.md` (e).
    session.flush()

    assert zone_candidates(session, zone, NOW) == []
