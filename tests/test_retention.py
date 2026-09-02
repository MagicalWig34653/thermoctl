import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_settings,
    create_zone,
    create_zone_state,
)
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.state import ShadowDecision
from thermoctl.services.retention import delete_old_measurements, delete_old_shadow_decisions
from thermoctl.services.shadow_run import cycle

NOW = datetime(2026, 8, 29, 12, 0)
DATA_PATH = Path(__file__).parent / "daten" / "anlage-beispiele.json"


def _inventory(session: Session, age_days: list[int]) -> None:
    device_name = json.loads(DATA_PATH.read_text(encoding="utf-8"))["geraete"][0]
    device = create_device(session, device_name)
    capability = DeviceCapability(code="aufbewahrung", label="Aufbewahrung")
    session.add(capability)
    session.flush()
    for age in age_days:
        moment = NOW - timedelta(days=age)
        session.add(
            Measurement(
                device_id=device.id,
                capability_id=capability.id,
                value_numeric=Decimal("1"),
                measured_at=moment,
                received_at=moment,
            )
        )
    session.flush()


def test_old_measurements_are_deleted_completely_in_blocks(session: Session) -> None:
    settings = create_settings(session)
    settings.measurement_retention_days = 30
    _inventory(session, [31, 32, 33, 29])

    assert delete_old_measurements(session, NOW, batch_size=2) == 3
    assert [m.measured_at for m in session.query(Measurement)] == [NOW - timedelta(days=29)]


def test_a_retention_value_of_zero_keeps_the_entire_history(session: Session) -> None:
    settings = create_settings(session)
    settings.measurement_retention_days = 0
    _inventory(session, [100])

    assert delete_old_measurements(session, NOW) == 0
    assert session.query(Measurement).count() == 1


def test_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="groesser als null"):
        delete_old_measurements(session, NOW, batch_size=0)


def _shadow_inventory(session: Session, zone_id: int, age_days: list[int]) -> None:
    for age in age_days:
        session.add(
            ShadowDecision(
                decided_at=NOW - timedelta(days=age),
                zone_id=zone_id,
                temperature_c=Decimal("20.0"),
                setpoint_c=Decimal("21.0"),
                setpoint_reason="Zeitplan",
                would_heat=False,
                previous_would_heat=False,
                outcome_code="aus",
                reason="Test",
            )
        )
    session.flush()


def test_old_shadow_decisions_are_deleted_in_real_blocks_at_an_exclusive_boundary(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    zone = create_zone(session, "schatten-aufbewahrung")
    _shadow_inventory(session, zone.id, [367, 366, 365, 364, 368])

    deletes = 0

    def count_deletes(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal deletes
        if statement.lstrip().upper().startswith("DELETE FROM SHADOW_DECISION"):
            deletes += 1

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count_deletes)
    try:
        assert delete_old_shadow_decisions(session, NOW, batch_size=2) == 3
    finally:
        event.remove(engine, "before_cursor_execute", count_deletes)

    assert deletes == 2
    remaining = session.query(ShadowDecision).order_by(ShadowDecision.id)
    assert [row.decided_at for row in remaining] == [
        NOW - timedelta(days=365),
        NOW - timedelta(days=364),
    ]


def test_shadow_retention_keeps_the_valve_protection_marker_authoritative(
    session: Session,
) -> None:
    settings = create_settings(session)
    settings.shadow_decision_retention_days = 365
    zone = create_zone(session, "ventilschutz-marker")
    zone.created_at = NOW - timedelta(days=90)
    zone.valve_protection_enabled = True
    zone.min_on_seconds = 0
    zone.min_off_seconds = 0
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("21.0")
    state.measured_at = NOW
    state.updated_at = NOW
    state.last_regular_heat_at = NOW - timedelta(days=29)
    state.regular_heat_history_compacted = True
    _shadow_inventory(session, zone.id, [366])

    before = cycle(session, NOW)[0]
    assert before.outcome_code != "ventilschutz"
    assert delete_old_shadow_decisions(session, NOW) == 1
    assert state.last_regular_heat_at == NOW - timedelta(days=29)

    after = cycle(session, NOW + timedelta(minutes=1))[0]
    assert after.outcome_code != "ventilschutz"
    assert state.last_regular_heat_at == NOW - timedelta(days=29)


def test_shadow_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="groesser als null"):
        delete_old_shadow_decisions(session, NOW, batch_size=-1)
