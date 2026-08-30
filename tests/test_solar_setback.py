"""Tests for the pure solar-setback correction (`thermoctl.domain.solar_setback`).

Mirrors the discipline of `test_control_loop.py`: no clock, no database, no network
-- everything the functions need arrives as a plain value. The five cases the task
explicitly calls out are each their own test: no forecast means no setback, a source
failure must not disturb control, the frost-protection floor is never crossed, a zone
factor of zero leaves the setpoint untouched, and the configured cap holds.
"""

from datetime import datetime
from decimal import Decimal

from thermoctl.domain.solar_setback import HourlyForecast, apply, sun_expected

NOW = datetime(2026, 8, 30, 8, 0)


def _point(hour: int, radiation: Decimal | None) -> HourlyForecast:
    return HourlyForecast(
        time=datetime(2026, 8, 30, hour, 0),
        cloud_cover_percent=None,
        shortwave_radiation_w_m2=radiation,
    )


# --- sun_expected -----------------------------------------------------------------


def test_sun_expected_is_true_when_a_bright_hour_falls_inside_the_window() -> None:
    forecast = [_point(9, Decimal(200))]
    assert sun_expected(forecast, NOW, lookahead_hours=3) is True


def test_sun_expected_is_false_without_any_bright_hour() -> None:
    forecast = [_point(9, Decimal(50)), _point(10, Decimal(80))]
    assert sun_expected(forecast, NOW, lookahead_hours=3) is False


def test_sun_expected_ignores_a_bright_hour_outside_the_lookahead_window() -> None:
    """The sun does come out -- just not soon enough to matter for a setback now."""
    forecast = [_point(14, Decimal(500))]
    assert sun_expected(forecast, NOW, lookahead_hours=3) is False


def test_an_hour_with_no_radiation_value_counts_as_no_sun() -> None:
    """Missing data must not be read as a promise of sun -- the safe direction is to
    under-predict, not over-predict."""
    forecast = [_point(9, None)]
    assert sun_expected(forecast, NOW, lookahead_hours=3) is False


def test_sun_expected_is_false_for_an_empty_forecast() -> None:
    assert sun_expected([], NOW, lookahead_hours=3) is False


def test_the_window_boundary_is_exclusive() -> None:
    """A bright hour exactly at the edge of the lookahead window is not 'soon'."""
    forecast = [_point(11, Decimal(500))]  # NOW + 3h exactly
    assert sun_expected(forecast, NOW, lookahead_hours=3) is False


# --- apply --------------------------------------------------------------------------


def test_without_an_expected_sun_there_is_no_setback() -> None:
    """The task's first required case: no forecasted sun, no correction."""
    result = apply(
        Decimal("21.0"), Decimal("16.0"),
        factor=Decimal("1.0"), max_reduction_k=Decimal("2.0"), expects_sun=False,
    )
    assert result is None


def test_a_zone_factor_of_zero_leaves_the_setpoint_untouched() -> None:
    """A room that does not profit from sun at all -- the documented default."""
    result = apply(
        Decimal("21.0"), Decimal("16.0"),
        factor=Decimal("0"), max_reduction_k=Decimal("2.0"), expects_sun=True,
    )
    assert result is None


def test_a_configured_maximum_of_zero_also_yields_no_setback() -> None:
    result = apply(
        Decimal("21.0"), Decimal("16.0"),
        factor=Decimal("1.0"), max_reduction_k=Decimal("0"), expects_sun=True,
    )
    assert result is None


def test_the_reduction_scales_with_the_zone_factor() -> None:
    result = apply(
        Decimal("21.0"), Decimal("10.0"),
        factor=Decimal("0.5"), max_reduction_k=Decimal("2.0"), expects_sun=True,
    )
    assert result is not None
    assert result.reduction_k == Decimal("1.0")
    assert result.setpoint_c == Decimal("20.0")


def test_the_configured_maximum_bounds_the_reduction() -> None:
    """A full-strength zone (factor 1) never exceeds the configured cap, even though
    plenty of room remains above frost protection."""
    result = apply(
        Decimal("30.0"), Decimal("10.0"),
        factor=Decimal("1.0"), max_reduction_k=Decimal("2.0"), expects_sun=True,
    )
    assert result is not None
    assert result.reduction_k == Decimal("2.0")
    assert result.setpoint_c == Decimal("28.0")


def test_the_setback_never_pushes_the_setpoint_below_frost_protection() -> None:
    """The task's explicit requirement: frost protection is a floor the setback must
    not cross, even with a strong zone factor and a generous cap."""
    result = apply(
        Decimal("17.0"), Decimal("16.0"),
        factor=Decimal("1.0"), max_reduction_k=Decimal("5.0"), expects_sun=True,
    )
    assert result is not None
    assert result.setpoint_c == Decimal("16.0")
    assert result.reduction_k == Decimal("1.0")  # capped at the room above frost, not 5.0


def test_a_setpoint_already_at_frost_protection_gets_no_setback_at_all() -> None:
    """Operating mode 'off' or a failed sensor already resolve to the frost setpoint
    -- there is no room left to subtract from, so `apply` must say so explicitly
    rather than returning a zero-sized 'reduction'."""
    result = apply(
        Decimal("16.0"), Decimal("16.0"),
        factor=Decimal("1.0"), max_reduction_k=Decimal("2.0"), expects_sun=True,
    )
    assert result is None
