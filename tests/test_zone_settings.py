from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.domain.zone_settings import control_parameters


def test_empty_zone_values_fall_back_to_the_default(session: Session) -> None:
    create_settings(session, hysteresis=Decimal("0.30"), min_ein=300)
    zone = create_zone(session, "bad")
    values = control_parameters(session, zone)
    assert values.hysteresis_k == Decimal("0.30")
    assert values.min_on_seconds == 300
    assert values.valve_protection_enabled is False
    assert values.valve_protection_interval_days == 30
    assert values.valve_protection_duration_minutes == 10


def test_valve_protection_duration_must_not_exceed_its_interval(session: Session) -> None:
    import pytest

    from thermoctl.domain.zone_settings import ParameterOutOfRange, save_control_parameters

    create_settings(session)
    zone = create_zone(session, "unplausibel")
    with pytest.raises(ParameterOutOfRange, match="nicht länger"):
        save_control_parameters(
            session, zone,
            {"valve_protection_interval_days": 1,
             "valve_protection_duration_minutes": 1441},
            user_id=None,
        )
    for values, message in (
        ({"valve_protection_interval_days": 0,
          "valve_protection_duration_minutes": 10}, "mindestens 1 Tag"),
        ({"valve_protection_interval_days": 30,
          "valve_protection_duration_minutes": 0}, "mindestens 1 Minute"),
    ):
        with pytest.raises(ParameterOutOfRange, match=message):
            save_control_parameters(session, zone, values, user_id=None)


def test_valve_protection_timing_rejects_values_above_the_domain_limits(
    session: Session,
) -> None:
    import pytest

    from thermoctl.domain.zone_settings import ParameterOutOfRange, save_control_parameters

    create_settings(session)
    zone = create_zone(session, "grenzschutz")
    for values, message in (
        ({"valve_protection_interval_days": 3651}, "höchstens 3650 Tage"),
        (
            {
                "valve_protection_interval_days": 3650,
                "valve_protection_duration_minutes": 5_256_001,
            },
            "höchstens 5256000 Minuten",
        ),
    ):
        with pytest.raises(ParameterOutOfRange, match=message):
            save_control_parameters(session, zone, values, user_id=None)

    save_control_parameters(
        session,
        zone,
        {
            "valve_protection_interval_days": 3650,
            "valve_protection_duration_minutes": 5_256_000,
        },
        user_id=None,
    )
    assert zone.valve_protection_interval_days == 3650
    assert zone.valve_protection_duration_minutes == 5_256_000


def test_a_zone_value_that_is_set_takes_precedence(session: Session) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    zone = create_zone(session, "kueche")
    zone.hysteresis_k = Decimal("0.80")
    session.flush()
    assert control_parameters(session, zone).hysteresis_k == Decimal("0.80")


def test_zero_is_a_valid_zone_value(session: Session) -> None:
    """0 must not be misread as 'not set'."""
    create_settings(session, min_ein=300)
    zone = create_zone(session, "flur")
    zone.min_on_seconds = 0
    session.flush()
    assert control_parameters(session, zone).min_on_seconds == 0


def test_changing_the_default_affects_zones_that_do_not_override_it(session: Session) -> None:
    e = create_settings(session, hysteresis=Decimal("0.30"))
    zone = create_zone(session, "buero")
    e.default_hysteresis_k = Decimal("0.50")
    session.flush()
    assert control_parameters(session, zone).hysteresis_k == Decimal("0.50")


def test_a_single_parameter_leaves_the_rest_inherited(session: Session) -> None:
    """`regelparameter_speichern` always takes all fields at once.

    Right for a form, wrong for a single dial in Home Assistant: that dial
    only knows its own value and would set every other field to whatever the
    caller happens to have at hand -- inherited values would turn into pinned
    ones.
    """
    from tests.helpers import source
    from thermoctl.domain.zone_settings import set_parameter

    create_settings(session, hysteresis=Decimal("0.30"), min_ein=300)
    source(session, "system")
    zone = create_zone(session, "einzelzone")

    set_parameter(session, zone, "hysteresis_k", Decimal("0.7"), user_id=None,
                     source="system")

    assert zone.hysteresis_k == Decimal("0.7")
    assert zone.min_on_seconds is None, "an inherited value was pinned"
    # And inheritance keeps working: the default still stands behind it.
    assert control_parameters(session, zone).min_on_seconds == 300


def test_integer_parameters_are_stored_as_integers(session: Session) -> None:
    """Home Assistant sends a decimal number even for seconds."""
    from tests.helpers import source
    from thermoctl.domain.zone_settings import set_parameter

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "sekundenzone")

    set_parameter(session, zone, "min_on_seconds", Decimal("600.0"), user_id=None,
                     source="system")

    assert zone.min_on_seconds == 600


def test_unknown_and_out_of_range_parameters_are_rejected(
    session: Session,
) -> None:
    import pytest

    from tests.helpers import source
    from thermoctl.domain.zone_settings import (
        ParameterOutOfRange,
        UnknownParameter,
        set_parameter,
    )

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "grenzzone")

    with pytest.raises(UnknownParameter):
        set_parameter(session, zone, "farbe", Decimal(1), user_id=None, source="system")
    with pytest.raises(ParameterOutOfRange):
        set_parameter(
            session, zone, "hysteresis_k", Decimal(99), user_id=None, source="system"
        )
    # The counter-check for the boundary: right at the edge is still allowed.
    set_parameter(
        session, zone, "hysteresis_k", Decimal("5.0"), user_id=None, source="system"
    )
    assert zone.hysteresis_k == Decimal("5.0")
