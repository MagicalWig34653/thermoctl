"""Hysterese bis 0,0001 K: Untergrenze, Schritt und verlustfreie Speicherung.

Die Spalten waren `NUMERIC(4, 2)` und hätten 0,0001 K still auf 0,00 gerundet -- das
wäre Schalten am Sollwert (Defekt des Altsystems). Die Roundtrip-Tests laufen gegen die
Datenbank der Testsuite (SQLite oder MariaDB, je nach `THERMOCTL_TEST_DATABASE_URL`).
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone
from thermoctl.domain.control import ControlError, check_number
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    validate_profile,
)
from thermoctl.domain.zone_settings import (
    BY_NAME,
    ControlParameters,
    ParameterOutOfRange,
    save_control_parameters,
)


def _values(zone: Zone, **changes: Decimal | None) -> dict[str, Decimal | int | bool | None]:
    values: dict[str, Decimal | int | bool | None] = {
        name: getattr(zone, name) for name in ControlParameters.__dataclass_fields__
    }
    values.update(changes)
    return values


@pytest.mark.parametrize("text", ["0.0001", "0,0001", "0.1", "0.30", "5.0", "5"])
def test_default_hysteresis_accepts_the_new_lower_bound_and_the_old_values(text: str) -> None:
    assert check_number("default_hysteresis_k", text) == Decimal(text.replace(",", "."))


@pytest.mark.parametrize("text", ["0.00009", "0", "-0.1", "5.0001", "0.00015", "0.12345"])
def test_default_hysteresis_rejects_below_minimum_beyond_precision_and_above_maximum(
    text: str,
) -> None:
    with pytest.raises(ControlError):
        check_number("default_hysteresis_k", text)


def test_zone_parameter_description_matches_the_global_bounds() -> None:
    description = BY_NAME["hysteresis_k"]
    assert (description.minimum, description.maximum, description.step) == (
        Decimal("0.0001"),
        Decimal("5.0"),
        Decimal("0.0001"),
    )


@pytest.mark.parametrize("text", ["0.0001", "0.0002", "0.1", "0.70", "5.0"])
def test_zone_hysteresis_is_stored_without_rounding(session: Session, text: str) -> None:
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "praezise")
    save_control_parameters(
        session, zone, _values(zone, hysteresis_k=Decimal(text)), user_id=None, source="api"
    )
    session.flush()
    session.expire_all()
    stored = session.get(Zone, zone.id)
    assert stored is not None
    assert stored.hysteresis_k == Decimal(text)


@pytest.mark.parametrize("text", ["0.00009", "0", "0.00015", "-1"])
def test_zone_hysteresis_rejects_below_minimum_and_beyond_precision(
    session: Session, text: str
) -> None:
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "abgelehnt")
    with pytest.raises(ParameterOutOfRange):
        save_control_parameters(
            session, zone, _values(zone, hysteresis_k=Decimal(text)), user_id=None, source="api"
        )


def test_zone_hysteresis_none_still_means_inherit(session: Session) -> None:
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "erbt")
    save_control_parameters(
        session, zone, _values(zone, hysteresis_k=None), user_id=None, source="api"
    )
    assert zone.hysteresis_k is None


def test_default_hysteresis_column_keeps_four_places(session: Session) -> None:
    row = create_settings(session)
    row.default_hysteresis_k = Decimal("0.0001")
    session.flush()
    session.expire_all()
    stored = session.get(Setting, 1)
    assert stored is not None
    assert stored.default_hysteresis_k == Decimal("0.0001")


VALUES = ProfileValues(
    "Notbetrieb",
    600,
    1200,
    60,
    2,
    Decimal("1"),
    (
        CurvePoint(Decimal("-10"), 1200, 600),
        CurvePoint(Decimal("0"), 600, 1200),
        CurvePoint(Decimal("15"), 0, 1800),
    ),
)


@pytest.mark.parametrize("text", ["0.0001", "0", "1.50", "99.99"])
def test_warm_restart_hysteresis_accepts_fine_steps_and_old_values(text: str) -> None:
    validate_profile(replace(VALUES, warm_restart_hysteresis_k=Decimal(text)))


@pytest.mark.parametrize("text", ["0.00009", "0.00015", "-0.0001", "100"])
def test_warm_restart_hysteresis_rejects_finer_than_storage(text: str) -> None:
    with pytest.raises(PolicyError):
        validate_profile(replace(VALUES, warm_restart_hysteresis_k=Decimal(text)))
