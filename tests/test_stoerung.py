from datetime import datetime, timedelta

import pytest

from thermoctl.domain.stoerung import (
    KEINE_QUELLE,
    OK,
    VERALTET,
    sensorzustand,
    zustandssatz,
)

JETZT = datetime(2026, 8, 29, 12, 0)


def test_sensorzustand_ohne_messung_hat_keine_quelle() -> None:
    assert sensorzustand(None, JETZT, 300) == KEINE_QUELLE


@pytest.mark.parametrize(
    ("alter_s", "erwartet"),
    [(299, OK), (300, OK), (301, VERALTET)],
)
def test_sensorzustand_timeoutgrenze_gehoert_zum_gueltigen_bereich(
    alter_s: int, erwartet: str
) -> None:
    messung = JETZT - timedelta(seconds=alter_s)

    assert sensorzustand(messung, JETZT, 300) == erwartet


def test_sensorzustand_messung_aus_der_zukunft_ist_ok() -> None:
    messung = JETZT + timedelta(seconds=10)

    assert sensorzustand(messung, JETZT, 300) == OK


@pytest.mark.parametrize(
    ("alter_s", "erwartet"),
    [(0, OK), (1, VERALTET)],
)
def test_sensorzustand_timeout_null_behaelt_nur_den_aktuellen_messwert(
    alter_s: int, erwartet: str
) -> None:
    messung = JETZT - timedelta(seconds=alter_s)

    assert sensorzustand(messung, JETZT, 0) == erwartet


def test_zustandssatz_enthaelt_konkrete_dauer_und_bewertung() -> None:
    messung = JETZT - timedelta(hours=3, minutes=12)

    satz = zustandssatz(VERALTET, messung, JETZT)

    assert "3 Stunden 12 Minuten" in satz
    assert "ausgefallen" in satz


def test_zustandssatz_benennt_fehlenden_messwert() -> None:
    assert "Noch nie" in zustandssatz(KEINE_QUELLE, None, JETZT)


def test_zustandssatz_beschreibt_zukuenftigen_messwert() -> None:
    messung = JETZT + timedelta(seconds=10)

    assert "in 10 Sekunden" in zustandssatz(OK, messung, JETZT)


def test_zustandssatz_weist_unbekannten_zustand_zurueck() -> None:
    with pytest.raises(ValueError, match="Unbekannter Sensorzustand"):
        zustandssatz("unbekannt", JETZT, JETZT)


def test_zustandssatz_verlangt_messzeitpunkt_fuer_bekannten_zustand() -> None:
    with pytest.raises(ValueError, match="erfordert einen Messzeitpunkt"):
        zustandssatz(OK, None, JETZT)
