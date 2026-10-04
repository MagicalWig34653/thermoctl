"""German number formatting for physical values in decision reasons."""

from decimal import Decimal

from thermoctl.domain.number_text import difference_text, percent_text, temperature_text


def test_physical_values_round_and_use_decimal_comma_with_unit_spacing() -> None:
    assert temperature_text(Decimal("19.96")) == "20,0 °C"
    assert difference_text(Decimal("0.126")) == "0,13 K"
    assert percent_text(Decimal("3.56")) == "3,6 %"


def test_negative_values_keep_sign_except_when_the_rounded_value_is_zero() -> None:
    assert temperature_text(Decimal("-1.25")) == "-1,2 °C"
    assert difference_text(Decimal("-0.126")) == "-0,13 K"
    assert difference_text(Decimal("-0.004")) == "0,00 K"
    assert percent_text(Decimal("-0.04")) == "0,0 %"
