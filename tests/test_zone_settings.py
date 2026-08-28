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
