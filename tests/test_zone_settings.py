from decimal import Decimal

from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, zone_anlegen
from thermoctl.domain.zone_settings import regelparameter


def test_leere_zonenwerte_fallen_auf_den_standard(session: Session) -> None:
    einstellungen_anlegen(session, hysterese=Decimal("0.30"), min_ein=300)
    zone = zone_anlegen(session, "bad")
    werte = regelparameter(session, zone)
    assert werte.hysteresis_k == Decimal("0.30")
    assert werte.min_on_seconds == 300


def test_gesetzter_zonenwert_hat_vorrang(session: Session) -> None:
    einstellungen_anlegen(session, hysterese=Decimal("0.30"))
    zone = zone_anlegen(session, "kueche")
    zone.hysteresis_k = Decimal("0.80")
    session.flush()
    assert regelparameter(session, zone).hysteresis_k == Decimal("0.80")


def test_null_ist_ein_gueltiger_zonenwert(session: Session) -> None:
    """0 darf nicht als 'nicht gesetzt' missverstanden werden."""
    einstellungen_anlegen(session, min_ein=300)
    zone = zone_anlegen(session, "flur")
    zone.min_on_seconds = 0
    session.flush()
    assert regelparameter(session, zone).min_on_seconds == 0


def test_standardaenderung_wirkt_auf_nicht_ueberschriebene_zonen(session: Session) -> None:
    e = einstellungen_anlegen(session, hysterese=Decimal("0.30"))
    zone = zone_anlegen(session, "buero")
    e.default_hysteresis_k = Decimal("0.50")
    session.flush()
    assert regelparameter(session, zone).hysteresis_k == Decimal("0.50")


def test_ein_einzelner_parameter_laesst_die_uebrigen_geerbt(session: Session) -> None:
    """`regelparameter_speichern` nimmt immer alle Felder auf einmal.

    Richtig fuer ein Formular, falsch fuer einen einzelnen Drehregler in Home Assistant:
    Der kennt nur seinen eigenen Wert und wuerde alle anderen auf das setzen, was der
    Aufrufer gerade zur Hand hat -- aus geerbten Werten wuerden festgeschriebene.
    """
    from tests.hilfen import quelle
    from thermoctl.domain.zone_settings import parameter_setzen

    einstellungen_anlegen(session, hysterese=Decimal("0.30"), min_ein=300)
    quelle(session, "system")
    zone = zone_anlegen(session, "einzelzone")

    parameter_setzen(session, zone, "hysteresis_k", Decimal("0.7"), user_id=None,
                     quelle="system")

    assert zone.hysteresis_k == Decimal("0.7")
    assert zone.min_on_seconds is None, "ein geerbter Wert wurde festgeschrieben"
    # Und die Vererbung wirkt weiter: der Standard steht nach wie vor dahinter.
    assert regelparameter(session, zone).min_on_seconds == 300


def test_ganzzahlige_parameter_werden_ganzzahlig_gespeichert(session: Session) -> None:
    """Home Assistant schickt auch fuer Sekunden eine Kommazahl."""
    from tests.hilfen import quelle
    from thermoctl.domain.zone_settings import parameter_setzen

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "sekundenzone")

    parameter_setzen(session, zone, "min_on_seconds", Decimal("600.0"), user_id=None,
                     quelle="system")

    assert zone.min_on_seconds == 600


def test_unbekannte_und_ausserhalb_liegende_parameter_werden_abgewiesen(
    session: Session,
) -> None:
    import pytest

    from tests.hilfen import quelle
    from thermoctl.domain.zone_settings import (
        Parametergrenze,
        Parameterunbekannt,
        parameter_setzen,
    )

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "grenzzone")

    with pytest.raises(Parameterunbekannt):
        parameter_setzen(session, zone, "farbe", Decimal(1), user_id=None, quelle="system")
    with pytest.raises(Parametergrenze):
        parameter_setzen(
            session, zone, "hysteresis_k", Decimal(99), user_id=None, quelle="system"
        )
    # Die Gegenprobe zur Grenze: genau am Rand ist noch erlaubt.
    parameter_setzen(
        session, zone, "hysteresis_k", Decimal("5.0"), user_id=None, quelle="system"
    )
    assert zone.hysteresis_k == Decimal("5.0")
