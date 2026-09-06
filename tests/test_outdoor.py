"""The plant-wide outdoor temperature source: reading and selection."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import capability, create_device, create_settings
from thermoctl.db.models.device import DeviceCapabilityLink
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.device_assignment import CapabilityMissing
from thermoctl.domain.fault import NO_SOURCE, OK, VERALTET
from thermoctl.domain.outdoor import outdoor_reading, set_outdoor_temperature_source

NOW = datetime(2026, 9, 6, 12, 0, 0)


def _with_temperature_capability(session: Session, name: str):
    device = create_device(session, name)
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, "temperature").id
        )
    )
    session.flush()
    return device


def test_no_source_configured_reads_as_no_source(session: Session) -> None:
    row = create_settings(session)
    reading = outdoor_reading(session, row, NOW)
    assert reading.status == NO_SOURCE
    assert reading.temperature_c is None


def test_a_configured_source_without_any_reading_yet_is_also_no_source(
    session: Session,
) -> None:
    row = create_settings(session)
    device = _with_temperature_capability(session, "aussenfuehler-stumm")
    row.outdoor_temperature_source_device_id = device.id
    reading = outdoor_reading(session, row, NOW)
    assert reading.status == NO_SOURCE
    assert reading.temperature_c is None


def test_a_fresh_reading_is_ok(session: Session) -> None:
    row = create_settings(session)
    row.default_sensor_timeout_seconds = 1800
    device = _with_temperature_capability(session, "aussenfuehler-frisch")
    row.outdoor_temperature_source_device_id = device.id
    temp = capability(session, "temperature")
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=temp.id,
            value_numeric=Decimal("3.2"),
            measured_at=NOW - timedelta(minutes=5),
            received_at=NOW - timedelta(minutes=5),
        )
    )

    reading = outdoor_reading(session, row, NOW)
    assert reading.status == OK
    assert reading.temperature_c == Decimal("3.2")


def test_a_stale_reading_is_veraltet_not_confused_with_a_warm_value(
    session: Session,
) -> None:
    """Age and failure are judged exactly like a zone sensor's -- `veraltet` must
    stay distinguishable from a genuinely warm reading, not just show a number."""
    row = create_settings(session)
    row.default_sensor_timeout_seconds = 1800
    device = _with_temperature_capability(session, "aussenfuehler-veraltet")
    row.outdoor_temperature_source_device_id = device.id
    temp = capability(session, "temperature")
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=temp.id,
            value_numeric=Decimal("18.0"),
            measured_at=NOW - timedelta(hours=2),
            received_at=NOW - timedelta(hours=2),
        )
    )

    reading = outdoor_reading(session, row, NOW)
    assert reading.status == VERALTET


def test_setting_the_source_requires_a_temperature_capability(session: Session) -> None:
    row = create_settings(session)
    ventil = create_device(session, "ventil-als-aussenfuehler")
    session.add(
        DeviceCapabilityLink(
            device_id=ventil.id, capability_id=capability(session, "switch").id
        )
    )
    session.flush()
    with pytest.raises(CapabilityMissing, match="Temperatur"):
        set_outdoor_temperature_source(session, row, ventil, actor_id=None)


def test_setting_and_clearing_the_source_writes_distinguishable_audit_entries(
    session: Session,
) -> None:
    from tests.helpers import source

    source(session)
    row = create_settings(session)
    device = _with_temperature_capability(session, "aussenfuehler-audit")

    set_outdoor_temperature_source(session, row, device, actor_id=None)
    assert row.outdoor_temperature_source_device_id == device.id

    set_outdoor_temperature_source(session, row, None, actor_id=None)
    assert row.outdoor_temperature_source_device_id is None

    entries = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_type == "outdoor_temperature_source")
            .order_by(AuditEvent.id)
        )
    )
    assert [entry.action for entry in entries] == ["assign", "unassign"]
