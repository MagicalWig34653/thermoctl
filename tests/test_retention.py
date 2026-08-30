import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_device, create_settings
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.services.retention import delete_old_measurements

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

    assert delete_old_measurements(session, NOW, blockgroesse=2) == 3
    assert [m.measured_at for m in session.query(Measurement)] == [NOW - timedelta(days=29)]


def test_a_retention_value_of_zero_keeps_the_entire_history(session: Session) -> None:
    settings = create_settings(session)
    settings.measurement_retention_days = 0
    _inventory(session, [100])

    assert delete_old_measurements(session, NOW) == 0
    assert session.query(Measurement).count() == 1


def test_block_size_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError, match="groesser als null"):
        delete_old_measurements(session, NOW, blockgroesse=0)
