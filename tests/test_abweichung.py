from decimal import Decimal

from thermoctl.domain.abweichung import Einordnung, vergleichen


def test_beide_heizen_ist_keine_abweichung() -> None:
    ergebnis = vergleichen(
        would_heat=True,
        ist_c=Decimal("19.4"),
        soll_c=Decimal("21.0"),
        altsystem_heizt=True,
    )
    assert ergebnis.einordnung == Einordnung.UEBEREINSTIMMUNG
    assert ergebnis.text == "thermoctl und das Altsystem heizen beide."


def test_beide_heizen_nicht_ist_keine_abweichung() -> None:
    ergebnis = vergleichen(
        would_heat=False,
        ist_c=Decimal("24.5"),
        soll_c=Decimal("20.0"),
        altsystem_heizt=False,
    )
    assert ergebnis.einordnung == Einordnung.UEBEREINSTIMMUNG
    assert ergebnis.text == "thermoctl und das Altsystem heizen beide nicht."


def test_nur_thermoctl_haette_geheizt_ist_eine_abweichung() -> None:
    ergebnis = vergleichen(
        would_heat=True,
        ist_c=Decimal("19.4"),
        soll_c=Decimal("21.0"),
        altsystem_heizt=False,
    )
    assert ergebnis.einordnung == Einordnung.ABWEICHUNG
    assert ergebnis.text == (
        "thermoctl haette geheizt, das Altsystem heizte nicht — Ist 19,4 °C, Soll 21,0 °C."
    )


def test_nur_altsystem_heizte_ist_eine_abweichung() -> None:
    ergebnis = vergleichen(
        would_heat=False,
        ist_c=Decimal("21.6"),
        soll_c=Decimal("21.0"),
        altsystem_heizt=True,
    )
    assert ergebnis.einordnung == Einordnung.ABWEICHUNG
    assert ergebnis.text == (
        "thermoctl haette nicht geheizt, das Altsystem heizte — Ist 21,6 °C, Soll 21,0 °C."
    )


def test_fehlende_temperaturwerte_werden_als_unbekannt_ausgegeben() -> None:
    ergebnis = vergleichen(
        would_heat=True,
        ist_c=None,
        soll_c=None,
        altsystem_heizt=False,
    )
    assert ergebnis.einordnung == Einordnung.ABWEICHUNG
    assert ergebnis.text == (
        "thermoctl haette geheizt, das Altsystem heizte nicht — "
        "Ist unbekannt °C, Soll unbekannt °C."
    )


def test_kein_altwert_zum_vergleichszeitpunkt_ist_kein_vergleich() -> None:
    ergebnis = vergleichen(
        would_heat=True,
        ist_c=Decimal("19.4"),
        soll_c=Decimal("21.0"),
        altsystem_heizt=None,
    )
    assert ergebnis.einordnung == Einordnung.KEIN_VERGLEICH
    assert ergebnis.text == "Zum Vergleichszeitpunkt liegt kein Altsystem-Wert vor."
