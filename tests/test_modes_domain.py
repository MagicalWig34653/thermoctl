"""Eingabeprüfung der Modi in der Domäne (die Oberfläche reicht Fehler nur weiter)."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import source, user_with_permissions
from thermoctl.db.models.zone import SetpointMode
from thermoctl.domain.modes import DomainError, check_temperature, create_mode


@pytest.mark.parametrize(
    ("code", "name", "field"),
    [
        ("", "Name", "code"),
        ("c" * 33, "Name", "code"),
        ("kurz", "", "name"),
        ("kurz", "n" * 65, "name"),
    ],
)
def test_an_unusable_code_or_name_names_its_field_and_stores_nothing(
    session: Session, code: str, name: str, field: str
) -> None:
    source(session, "web")
    user = user_with_permissions(session, "modi", [("mode.manage", None)])
    before = session.query(SetpointMode).count()
    with pytest.raises(DomainError) as refused:
        create_mode(session, code=code, name=name, sort_order=9, user_id=user.id)
    assert refused.value.field == field
    assert session.query(SetpointMode).count() == before


def test_a_setpoint_that_is_not_a_finite_number_is_refused() -> None:
    for value in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(DomainError) as refused:
            check_temperature(value)
        assert refused.value.field == "temperature_c"
