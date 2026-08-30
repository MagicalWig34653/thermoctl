from decimal import Decimal

from thermoctl.domain.deviation import Classification, compare


def test_both_heating_is_not_a_deviation() -> None:
    result = compare(
        would_heat=True,
        measured_c=Decimal("19.4"),
        setpoint_c=Decimal("21.0"),
        legacy_system_heating=True,
    )
    assert result.einordnung == Classification.UEBEREINSTIMMUNG
    assert result.text == "thermoctl und das Altsystem heizen beide."


def test_both_not_heating_is_not_a_deviation() -> None:
    result = compare(
        would_heat=False,
        measured_c=Decimal("24.5"),
        setpoint_c=Decimal("20.0"),
        legacy_system_heating=False,
    )
    assert result.einordnung == Classification.UEBEREINSTIMMUNG
    assert result.text == "thermoctl und das Altsystem heizen beide nicht."


def test_only_thermoctl_would_have_heated_is_a_deviation() -> None:
    result = compare(
        would_heat=True,
        measured_c=Decimal("19.4"),
        setpoint_c=Decimal("21.0"),
        legacy_system_heating=False,
    )
    assert result.einordnung == Classification.ABWEICHUNG
    assert result.text == (
        "thermoctl haette geheizt, das Altsystem heizte nicht — Ist 19,4 °C, Soll 21,0 °C."
    )


def test_only_the_legacy_system_heated_is_a_deviation() -> None:
    result = compare(
        would_heat=False,
        measured_c=Decimal("21.6"),
        setpoint_c=Decimal("21.0"),
        legacy_system_heating=True,
    )
    assert result.einordnung == Classification.ABWEICHUNG
    assert result.text == (
        "thermoctl haette nicht geheizt, das Altsystem heizte — Ist 21,6 °C, Soll 21,0 °C."
    )


def test_missing_temperature_values_are_reported_as_unknown() -> None:
    result = compare(
        would_heat=True,
        measured_c=None,
        setpoint_c=None,
        legacy_system_heating=False,
    )
    assert result.einordnung == Classification.ABWEICHUNG
    assert result.text == (
        "thermoctl haette geheizt, das Altsystem heizte nicht — "
        "Ist unbekannt °C, Soll unbekannt °C."
    )


def test_no_legacy_value_at_comparison_time_is_not_a_comparison() -> None:
    result = compare(
        would_heat=True,
        measured_c=Decimal("19.4"),
        setpoint_c=Decimal("21.0"),
        legacy_system_heating=None,
    )
    assert result.einordnung == Classification.KEIN_VERGLEICH
    assert result.text == "Zum Vergleichszeitpunkt liegt kein Altsystem-Wert vor."
