"""Die CheckConstraints der Zone gegen die Parametertabelle (L1, Befund B3).

Eine Datenbank kann `PARAMETERS` nicht lesen; die PI-Bereiche und -Schritte stehen deshalb
zusätzlich als `CheckConstraint` in `db/models/zone.py`. Dieser Test hält beide gegeneinander:
Jeder Wert, den die Tabelle erlaubt, muss die Datenbank annehmen, und jeder, den sie
verbietet (knapp darunter, knapp darüber, neben dem Schrittraster), muss sie ablehnen. Wer
eine Grenze nur an einer der beiden Stellen ändert, bricht hier.
"""

import re
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.db.models.zone import Zone
from thermoctl.domain.zone_settings import BY_NAME, GROUP_PI, PARAMETERS

PI_NAMES = [p.name for p in PARAMETERS if p.group == GROUP_PI]


def _stored(name: str, value: Decimal) -> Decimal | int:
    return int(value) if BY_NAME[name].integral else value


def _accepted(session: Session, zone: Zone, name: str, value: Decimal) -> bool:
    try:
        with session.begin_nested():
            setattr(zone, name, _stored(name, value))
            session.flush()
    except DBAPIError:
        # SQLite meldet eine verletzte CHECK-Bedingung als IntegrityError, MariaDB (11.4,
        # PyMySQL) als OperationalError -- beides ist die Ablehnung. Der Savepoint
        # (`begin_nested`) rollt zurück; die Sitzung bleibt für den nächsten Wert nutzbar.
        return False
    return True


def _constrained_parameter_columns() -> set[str]:
    columns: set[str] = set()
    for constraint in cast(Table, Zone.__table__).constraints:
        if isinstance(constraint, CheckConstraint):
            words = set(re.findall(r"[a-z_]+", str(constraint.sqltext)))
            columns |= words & set(BY_NAME)
    return columns


def test_every_zone_constraint_on_a_described_parameter_is_covered_here() -> None:
    """Kommt eine Constraint auf einem weiteren beschriebenen Parameter hinzu, muss sie
    hier mitgeprüft werden -- sonst wüchse die zweite Fassung der Grenze unbemerkt."""
    assert _constrained_parameter_columns() == set(PI_NAMES)


@pytest.mark.parametrize("name", PI_NAMES)
def test_database_accepts_exactly_what_the_table_allows(session: Session, name: str) -> None:
    create_settings(session)
    zone = create_zone(session, "constraint")
    description = BY_NAME[name]
    lo, hi, step = description.minimum, description.maximum, description.step

    on_grid = [lo, lo + step, hi - step, hi]
    for value in on_grid:
        assert _accepted(session, zone, name, value), f"{name}={value} muss gelten"

    outside = [lo - step, hi + step]
    if step != 1:  # neben einem Raster von 1 gibt es keinen speicherbaren ganzzahligen Wert
        outside.append(lo + step * Decimal("0.4"))
    for value in outside:
        assert not _accepted(session, zone, name, value), f"{name}={value} muss scheitern"
