from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.domain.zone_settings import control_parameters


def test_leere_zonenwerte_fallen_auf_den_standard(session: Session) -> None:
    create_settings(session, hysteresis=Decimal("0.30"), min_ein=300)
    zone = create_zone(session, "bad")
    values = control_parameters(session, zone)
    assert values.hysteresis_k == Decimal("0.30")
    assert values.min_on_seconds == 300


def test_gesetzter_zonenwert_hat_vorrang(session: Session) -> None:
    create_settings(session, hysteresis=Decimal("0.30"))
    zone = create_zone(session, "kueche")
    zone.hysteresis_k = Decimal("0.80")
    session.flush()
    assert control_parameters(session, zone).hysteresis_k == Decimal("0.80")


def test_null_ist_ein_gueltiger_zonenwert(session: Session) -> None:
    """0 darf nicht als 'nicht gesetzt' missverstanden werden."""
    create_settings(session, min_ein=300)
    zone = create_zone(session, "flur")
    zone.min_on_seconds = 0
    session.flush()
    assert control_parameters(session, zone).min_on_seconds == 0


def test_standardaenderung_wirkt_auf_nicht_ueberschriebene_zonen(session: Session) -> None:
    e = create_settings(session, hysteresis=Decimal("0.30"))
    zone = create_zone(session, "buero")
    e.default_hysteresis_k = Decimal("0.50")
    session.flush()
    assert control_parameters(session, zone).hysteresis_k == Decimal("0.50")


def test_ein_einzelner_parameter_laesst_die_uebrigen_geerbt(session: Session) -> None:
    """`regelparameter_speichern` nimmt immer alle Felder auf einmal.

    Richtig fuer ein Formular, falsch fuer einen einzelnen Drehregler in Home Assistant:
    Der kennt nur seinen eigenen Wert und wuerde alle anderen auf das setzen, was der
    Aufrufer gerade zur Hand hat -- aus geerbten Werten wuerden festgeschriebene.
    """
    from tests.helpers import source
    from thermoctl.domain.zone_settings import set_parameter

    create_settings(session, hysteresis=Decimal("0.30"), min_ein=300)
    source(session, "system")
    zone = create_zone(session, "einzelzone")

    set_parameter(session, zone, "hysteresis_k", Decimal("0.7"), user_id=None,
                     source="system")

    assert zone.hysteresis_k == Decimal("0.7")
    assert zone.min_on_seconds is None, "ein geerbter Wert wurde festgeschrieben"
    # Und die Vererbung wirkt weiter: der Standard steht nach wie vor dahinter.
    assert control_parameters(session, zone).min_on_seconds == 300


def test_ganzzahlige_parameter_werden_ganzzahlig_gespeichert(session: Session) -> None:
    """Home Assistant schickt auch fuer Sekunden eine Kommazahl."""
    from tests.helpers import source
    from thermoctl.domain.zone_settings import set_parameter

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "sekundenzone")

    set_parameter(session, zone, "min_on_seconds", Decimal("600.0"), user_id=None,
                     source="system")

    assert zone.min_on_seconds == 600


def test_unbekannte_und_ausserhalb_liegende_parameter_werden_abgewiesen(
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
    # Die Gegenprobe zur Grenze: genau am Rand ist noch erlaubt.
    set_parameter(
        session, zone, "hysteresis_k", Decimal("5.0"), user_id=None, source="system"
    )
    assert zone.hysteresis_k == Decimal("5.0")
