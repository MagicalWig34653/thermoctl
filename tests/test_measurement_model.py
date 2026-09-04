from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_device
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.measurement import Measurement


def _capability(session: Session) -> DeviceCapability:
    capability = DeviceCapability(code="messwert-test", label="Messwert-Test")
    session.add(capability)
    session.flush()
    return capability


@pytest.mark.parametrize(
    ("number", "text"),
    [(None, None), (Decimal("1.000"), "ON")],
)
def test_measurement_requires_exactly_one_value_column(
    session: Session, number: Decimal | None, text: str | None
) -> None:
    device = create_device(session, "messwert-prüfung")
    capability = _capability(session)
    now = datetime(2026, 8, 29, 8, 0)
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=capability.id,
            value_numeric=number,
            value_text=text,
            measured_at=now,
            received_at=now,
        )
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_measurements_disappear_with_the_device(session: Session) -> None:
    device = create_device(session, "kaskaden-prüfung")
    capability = _capability(session)
    now = datetime(2026, 8, 29, 8, 0)
    session.add(
        Measurement(
            device_id=device.id,
            capability_id=capability.id,
            value_numeric=Decimal("21.500"),
            measured_at=now,
            received_at=now,
        )
    )
    session.flush()
    session.execute(delete(Device).where(Device.id == device.id))
    session.flush()
    assert session.query(Measurement).count() == 0
