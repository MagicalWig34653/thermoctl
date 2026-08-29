import logging
from datetime import datetime

import pytest

from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain.altbestand import (
    Schaltpunktentwurf,
    nachtstunden_lesen,
    schaltpunkte_aus_nachtstunden,
)
from thermoctl.domain.schedule import geltender_punkt


def _woche(**tage: frozenset[int]) -> dict[int, frozenset[int]]:
    return {tag: tage.get(str(tag), frozenset()) for tag in range(1, 8)}


def test_ringuebergang_wird_nicht_an_mitternacht_geteilt() -> None:
    nachtstunden = _woche(**{"7": frozenset({22, 23}), "1": frozenset(range(6))})

    assert schaltpunkte_aus_nachtstunden(nachtstunden) == [
        Schaltpunktentwurf(1, 360, False),
        Schaltpunktentwurf(7, 1320, True),
    ]


def test_durchgehend_nacht_braucht_einen_punkt() -> None:
    nachtstunden = {tag: frozenset(range(24)) for tag in range(1, 8)}

    assert schaltpunkte_aus_nachtstunden(nachtstunden) == [
        Schaltpunktentwurf(1, 0, True)
    ]


def test_durchgehend_tag_braucht_einen_punkt() -> None:
    assert schaltpunkte_aus_nachtstunden(_woche()) == [
        Schaltpunktentwurf(1, 0, False)
    ]


def test_loecher_erzeugen_jeden_tatsaechlichen_wechsel() -> None:
    nachtstunden = _woche(**{"1": frozenset({0, 1, 5, 6})})

    assert schaltpunkte_aus_nachtstunden(nachtstunden) == [
        Schaltpunktentwurf(1, 0, True),
        Schaltpunktentwurf(1, 120, False),
        Schaltpunktentwurf(1, 300, True),
        Schaltpunktentwurf(1, 420, False),
    ]


def test_vorgabewert_bedeutet_durchgehend_tag() -> None:
    gelesen = nachtstunden_lesen("[[],[],[],[],[],[],[],[]]")

    assert schaltpunkte_aus_nachtstunden(gelesen) == [
        Schaltpunktentwurf(1, 0, False)
    ]


@pytest.mark.parametrize(
    ("blob", "warnung"),
    [
        ("kein JSON", "kein gueltiges JSON"),
        ('{"1": [2]}', "kein Array"),
    ],
)
def test_unlesbarer_blob_wird_als_leere_woche_protokolliert(
    blob: str, warnung: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen(blob)

    assert gelesen == _woche()
    assert warnung in caplog.text


def test_sieben_slots_uebernehmen_lesbare_tage_und_protokollieren_luecke(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen('[[],[1],[],[],[],[],[6]]')

    assert gelesen[1] == frozenset({1})
    assert gelesen[6] == frozenset({6})
    assert gelesen[7] == frozenset()
    assert "7 statt acht Slots" in caplog.text


def test_neun_slots_verwerfen_ueberzaehligen_slot_und_protokollieren_ihn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen('[[],[],[],[],[],[],[],[7],[8]]')

    assert gelesen[7] == frozenset({7})
    assert all(8 not in stunden for stunden in gelesen.values())
    assert "9 statt acht Slots" in caplog.text


def test_stunde_als_zahl_wird_uebernommen() -> None:
    assert nachtstunden_lesen('[[],[3],[],[],[],[],[],[]]')[1] == frozenset({3})


def test_boolescher_wert_ist_keine_stundenzahl() -> None:
    assert nachtstunden_lesen('[[],[true],[],[],[],[],[],[]]')[1] == frozenset()


@pytest.mark.parametrize("wert", ['"24"', "24"])
def test_stunde_24_wird_verworfen(
    wert: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen(f"[[],[{wert}],[],[],[],[],[],[]]")

    assert gelesen[1] == frozenset()
    assert "verworfen" in caplog.text


def test_doppelte_stunde_wird_nur_einmal_uebernommen(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen('[[],["4",4],[],[],[],[],[],[]]')

    assert gelesen[1] == frozenset({4})
    assert "doppelte Nachtstunde" in caplog.text


def test_objekt_statt_slotliste_gilt_als_leer(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.altbestand"):
        gelesen = nachtstunden_lesen('[[],{"0": true},[],[],[],[],[],[]]')

    assert gelesen[1] == frozenset()
    assert "keine Liste" in caplog.text


@pytest.mark.parametrize(
    "nachtstunden",
    [
        _woche(),
        {tag: frozenset(range(24)) for tag in range(1, 8)},
        _woche(**{"1": frozenset({0, 1, 5, 6})}),
        _woche(**{"7": frozenset({22, 23}), "1": frozenset(range(6))}),
        {tag: frozenset({tag, tag + 8, tag + 16}) for tag in range(1, 8)},
    ],
)
def test_rueckprobe_gegen_echte_auswertungsregel(
    nachtstunden: dict[int, frozenset[int]],
) -> None:
    entwuerfe = schaltpunkte_aus_nachtstunden(nachtstunden)
    punkte = [
        SchedulePoint(
            weekday=entwurf.weekday,
            minute_of_day=entwurf.minute_of_day,
            setpoint_mode_id=int(entwurf.nacht),
            zone_id=1,
        )
        for entwurf in entwuerfe
    ]

    for tag in range(1, 8):
        for stunde in range(24):
            zeitpunkt = datetime(2026, 8, 24 + tag - 1, stunde)
            punkt = geltender_punkt(punkte, zeitpunkt)
            assert punkt is not None
            alte_stunden = {str(stunde) for stunde in nachtstunden[tag]}
            assert bool(punkt.setpoint_mode_id) == (str(stunde) in alte_stunden)
