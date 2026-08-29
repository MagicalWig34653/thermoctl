import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, geraet_anlegen
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.messwert import Measurement
from thermoctl.services.aufbewahrung import alte_messwerte_loeschen

JETZT = datetime(2026, 8, 29, 12, 0)
DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"


def _bestand(session: Session, alter_tage: list[int]) -> None:
    geraetename = json.loads(DATENPFAD.read_text(encoding="utf-8"))["geraete"][0]
    geraet = geraet_anlegen(session, geraetename)
    faehigkeit = DeviceCapability(code="aufbewahrung", label="Aufbewahrung")
    session.add(faehigkeit)
    session.flush()
    for alter in alter_tage:
        zeitpunkt = JETZT - timedelta(days=alter)
        session.add(
            Measurement(
                device_id=geraet.id,
                capability_id=faehigkeit.id,
                value_numeric=Decimal("1"),
                measured_at=zeitpunkt,
                received_at=zeitpunkt,
            )
        )
    session.flush()


def test_alte_messwerte_werden_blockweise_vollstaendig_geloescht(session: Session) -> None:
    einstellungen = einstellungen_anlegen(session)
    einstellungen.measurement_retention_days = 30
    _bestand(session, [31, 32, 33, 29])

    assert alte_messwerte_loeschen(session, JETZT, blockgroesse=2) == 3
    assert [m.measured_at for m in session.query(Measurement)] == [JETZT - timedelta(days=29)]


def test_aufbewahrungswert_null_behaelt_die_gesamte_historie(session: Session) -> None:
    einstellungen = einstellungen_anlegen(session)
    einstellungen.measurement_retention_days = 0
    _bestand(session, [100])

    assert alte_messwerte_loeschen(session, JETZT) == 0
    assert session.query(Measurement).count() == 1


def test_blockgroesse_muss_positiv_sein(session: Session) -> None:
    with pytest.raises(ValueError, match="groesser als null"):
        alte_messwerte_loeschen(session, JETZT, blockgroesse=0)
