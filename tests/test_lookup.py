import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode, Permission


def test_code_ist_eindeutig(session: Session) -> None:
    session.add(OperatingMode(code="auto", label="Automatik"))
    session.flush()
    session.add(OperatingMode(code="auto", label="Nochmal"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_berechtigung_kennt_ihren_geltungsbereich(session: Session) -> None:
    p = Permission(code="zone.read", description="Zonen sehen", is_zone_scoped=True)
    session.add(p)
    session.flush()
    assert p.is_zone_scoped is True
