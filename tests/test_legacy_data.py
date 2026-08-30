import logging
from datetime import datetime

import pytest

from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain.legacy_data import (
    SchedulePointDraft,
    read_night_hours,
    schedule_points_from_night_hours,
)
from thermoctl.domain.schedule import current_point


def _week(**days: frozenset[int]) -> dict[int, frozenset[int]]:
    return {day: days.get(str(day), frozenset()) for day in range(1, 8)}


def test_ringuebergang_wird_nicht_an_mitternacht_geteilt() -> None:
    night_hours = _week(**{"7": frozenset({22, 23}), "1": frozenset(range(6))})

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 360, False),
        SchedulePointDraft(7, 1320, True),
    ]


def test_durchgehend_nacht_braucht_einen_punkt() -> None:
    night_hours = {day: frozenset(range(24)) for day in range(1, 8)}

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 0, True)
    ]


def test_durchgehend_tag_braucht_einen_punkt() -> None:
    assert schedule_points_from_night_hours(_week()) == [
        SchedulePointDraft(1, 0, False)
    ]


def test_loecher_erzeugen_jeden_tatsaechlichen_wechsel() -> None:
    night_hours = _week(**{"1": frozenset({0, 1, 5, 6})})

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 0, True),
        SchedulePointDraft(1, 120, False),
        SchedulePointDraft(1, 300, True),
        SchedulePointDraft(1, 420, False),
    ]


def test_vorgabewert_bedeutet_durchgehend_tag() -> None:
    read_back = read_night_hours("[[],[],[],[],[],[],[],[]]")

    assert schedule_points_from_night_hours(read_back) == [
        SchedulePointDraft(1, 0, False)
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
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours(blob)

    assert read_back == _week()
    assert warnung in caplog.text


def test_sieben_slots_uebernehmen_lesbare_tage_und_protokollieren_luecke(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],[1],[],[],[],[],[6]]')

    assert read_back[1] == frozenset({1})
    assert read_back[6] == frozenset({6})
    assert read_back[7] == frozenset()
    assert "7 statt acht Slots" in caplog.text


def test_neun_slots_verwerfen_ueberzaehligen_slot_und_protokollieren_ihn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],[],[],[],[],[],[],[7],[8]]')

    assert read_back[7] == frozenset({7})
    assert all(8 not in hours for hours in read_back.values())
    assert "9 statt acht Slots" in caplog.text


def test_stunde_als_zahl_wird_uebernommen() -> None:
    assert read_night_hours('[[],[3],[],[],[],[],[],[]]')[1] == frozenset({3})


def test_boolescher_wert_ist_keine_stundenzahl() -> None:
    assert read_night_hours('[[],[true],[],[],[],[],[],[]]')[1] == frozenset()


@pytest.mark.parametrize("value", ['"24"', "24"])
def test_stunde_24_wird_verworfen(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours(f"[[],[{value}],[],[],[],[],[],[]]")

    assert read_back[1] == frozenset()
    assert "verworfen" in caplog.text


def test_doppelte_stunde_wird_nur_einmal_uebernommen(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],["4",4],[],[],[],[],[],[]]')

    assert read_back[1] == frozenset({4})
    assert "doppelte Nachtstunde" in caplog.text


def test_objekt_statt_slotliste_gilt_als_leer(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],{"0": true},[],[],[],[],[],[]]')

    assert read_back[1] == frozenset()
    assert "keine Liste" in caplog.text


@pytest.mark.parametrize(
    "night_hours",
    [
        _week(),
        {day: frozenset(range(24)) for day in range(1, 8)},
        _week(**{"1": frozenset({0, 1, 5, 6})}),
        _week(**{"7": frozenset({22, 23}), "1": frozenset(range(6))}),
        {day: frozenset({day, day + 8, day + 16}) for day in range(1, 8)},
    ],
)
def test_rueckprobe_gegen_echte_auswertungsregel(
    night_hours: dict[int, frozenset[int]],
) -> None:
    entwuerfe = schedule_points_from_night_hours(night_hours)
    points = [
        SchedulePoint(
            weekday=entwurf.weekday,
            minute_of_day=entwurf.minute_of_day,
            setpoint_mode_id=int(entwurf.night),
            zone_id=1,
        )
        for entwurf in entwuerfe
    ]

    for day in range(1, 8):
        for stunde in range(24):
            moment = datetime(2026, 8, 24 + day - 1, stunde)
            point = current_point(points, moment)
            assert point is not None
            old_hours = {str(stunde) for stunde in night_hours[day]}
            assert bool(point.setpoint_mode_id) == (str(stunde) in old_hours)
