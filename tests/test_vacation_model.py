from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, source
from thermoctl.db.base import utcnow
from thermoctl.db.models.vacation import Vacation


def test_end_must_be_after_start(session: Session) -> None:
    now = utcnow()
    session.add(
        Vacation(
            starts_at=now,
            ends_at=now,
            setback_temperature_c=Decimal("15.0"),
            source_id=source(session, "web").id,
        )
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_an_end_before_the_start_is_rejected(session: Session) -> None:
    now = utcnow()
    session.add(
        Vacation(
            starts_at=now,
            ends_at=now.replace(year=now.year - 1),
            setback_temperature_c=Decimal("15.0"),
            source_id=source(session, "web").id,
        )
    )
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_a_vacation_is_kept_as_history(session: Session) -> None:
    """Cancelling does not delete, it sets cancelled_at -- the same reasoning as
    `ZoneOverride`."""
    now = utcnow()
    vacation = Vacation(
        starts_at=now,
        ends_at=now.replace(year=now.year + 1),
        setback_temperature_c=Decimal("15.0"),
        source_id=source(session, "web").id,
    )
    session.add(vacation)
    session.flush()
    vacation.cancelled_at = utcnow()
    session.flush()
    assert session.query(Vacation).count() == 1
