from datetime import datetime, timedelta

import pytest

from thermoctl.domain.fault import (
    NO_SOURCE,
    OK,
    VERALTET,
    sensor_state,
    state_row,
)

NOW = datetime(2026, 8, 29, 12, 0)


def test_sensorzustand_ohne_messung_hat_keine_quelle() -> None:
    assert sensor_state(None, NOW, 300) == NO_SOURCE


@pytest.mark.parametrize(
    ("age_s", "expected"),
    [(299, OK), (300, OK), (301, VERALTET)],
)
def test_sensorzustand_timeoutgrenze_gehoert_zum_gueltigen_bereich(
    age_s: int, expected: str
) -> None:
    messung = NOW - timedelta(seconds=age_s)

    assert sensor_state(messung, NOW, 300) == expected


def test_sensorzustand_messung_aus_der_zukunft_ist_ok() -> None:
    messung = NOW + timedelta(seconds=10)

    assert sensor_state(messung, NOW, 300) == OK


@pytest.mark.parametrize(
    ("age_s", "expected"),
    [(0, OK), (1, VERALTET)],
)
def test_sensorzustand_timeout_null_behaelt_nur_den_aktuellen_messwert(
    age_s: int, expected: str
) -> None:
    messung = NOW - timedelta(seconds=age_s)

    assert sensor_state(messung, NOW, 0) == expected


def test_zustandssatz_enthaelt_konkrete_dauer_und_bewertung() -> None:
    messung = NOW - timedelta(hours=3, minutes=12)

    satz = state_row(VERALTET, messung, NOW)

    assert "3 Stunden 12 Minuten" in satz
    assert "ausgefallen" in satz


def test_zustandssatz_benennt_fehlenden_messwert() -> None:
    assert "Noch nie" in state_row(NO_SOURCE, None, NOW)


def test_zustandssatz_beschreibt_zukuenftigen_messwert() -> None:
    messung = NOW + timedelta(seconds=10)

    assert "in 10 Sekunden" in state_row(OK, messung, NOW)


def test_zustandssatz_weist_unbekannten_zustand_zurueck() -> None:
    with pytest.raises(ValueError, match="Unbekannter Sensorzustand"):
        state_row("unbekannt", NOW, NOW)


def test_zustandssatz_verlangt_messzeitpunkt_fuer_bekannten_zustand() -> None:
    with pytest.raises(ValueError, match="erfordert einen Messzeitpunkt"):
        state_row(OK, None, NOW)
