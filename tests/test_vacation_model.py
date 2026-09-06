from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, source, zone_with_schedule
from thermoctl.db.base import utcnow
from thermoctl.db.models.vacation import Vacation
from thermoctl.domain.schedule import cancel_vacation, create_vacation, resolved_setpoint


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


def test_a_cancelled_vacation_no_longer_affects_control_but_stays_findable(
    session: Session,
) -> None:
    """Cancelling does not delete, it sets `cancelled_at` -- the same reasoning as
    `ZoneOverride`. The fachliche Aussage this must hold is two-sided: the cancelled
    period must stop influencing `resolved_setpoint()` (its whole reason for
    existing), and the row must still be there afterwards, findable by id -- a
    plain ORM check that setting a field does not delete a row would prove nothing
    about either half of that."""
    zone = zone_with_schedule(
        session,
        "urlaub-abbruch-auffindbar",
        points=[(1, 360, "tag", Decimal("21.0"))],
        frost_protection=Decimal("10.0"),
    )
    now = datetime(2026, 8, 1, 0, 0)
    vacation = create_vacation(
        session,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 10),
        setback_temperature_c=Decimal("15.0"),
        timezone_name="Europe/Berlin",
        now=now,
    )

    before_cancel = resolved_setpoint(session, zone, now)
    assert before_cancel.temperature_c == Decimal("15.0")

    cancelled = cancel_vacation(session, now=now)
    assert cancelled is not None

    after_cancel = resolved_setpoint(session, zone, now)
    assert after_cancel.temperature_c == Decimal("21.0")

    found = session.get(Vacation, vacation.id)
    assert found is not None
    assert found.cancelled_at is not None
    assert found.setback_temperature_c == Decimal("15.0")
