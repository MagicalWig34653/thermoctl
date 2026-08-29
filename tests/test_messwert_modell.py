from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tests.hilfen import CONSTRAINT_FEHLER, geraet_anlegen
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.messwert import Measurement


def _faehigkeit(session: Session) -> DeviceCapability:
    faehigkeit = DeviceCapability(code="messwert-test", label="Messwert-Test")
    session.add(faehigkeit)
    session.flush()
    return faehigkeit


@pytest.mark.parametrize(
    ("zahl", "text"),
    [(None, None), (Decimal("1.000"), "ON")],
)
def test_messwert_verlangt_genau_eine_wertspalte(
    session: Session, zahl: Decimal | None, text: str | None
) -> None:
    geraet = geraet_anlegen(session, "messwert-pruefung")
    faehigkeit = _faehigkeit(session)
    jetzt = datetime(2026, 8, 29, 8, 0)
    session.add(
        Measurement(
            device_id=geraet.id,
            capability_id=faehigkeit.id,
            value_numeric=zahl,
            value_text=text,
            measured_at=jetzt,
            received_at=jetzt,
        )
    )
    with pytest.raises(CONSTRAINT_FEHLER):
        session.flush()


def test_messwerte_verschwinden_mit_dem_geraet(session: Session) -> None:
    geraet = geraet_anlegen(session, "kaskaden-pruefung")
    faehigkeit = _faehigkeit(session)
    jetzt = datetime(2026, 8, 29, 8, 0)
    session.add(
        Measurement(
            device_id=geraet.id,
            capability_id=faehigkeit.id,
            value_numeric=Decimal("21.500"),
            measured_at=jetzt,
            received_at=jetzt,
        )
    )
    session.flush()
    session.execute(delete(Device).where(Device.id == geraet.id))
    session.flush()
    assert session.query(Measurement).count() == 0
