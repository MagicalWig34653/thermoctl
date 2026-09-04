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


def test_the_wraparound_is_not_split_at_midnight() -> None:
    night_hours = _week(**{"7": frozenset({22, 23}), "1": frozenset(range(6))})

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 360, False),
        SchedulePointDraft(7, 1320, True),
    ]


def test_continuous_night_needs_one_point() -> None:
    night_hours = {day: frozenset(range(24)) for day in range(1, 8)}

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 0, True)
    ]


def test_continuous_day_needs_one_point() -> None:
    assert schedule_points_from_night_hours(_week()) == [
        SchedulePointDraft(1, 0, False)
    ]


def test_gaps_create_every_actual_change() -> None:
    night_hours = _week(**{"1": frozenset({0, 1, 5, 6})})

    assert schedule_points_from_night_hours(night_hours) == [
        SchedulePointDraft(1, 0, True),
        SchedulePointDraft(1, 120, False),
        SchedulePointDraft(1, 300, True),
        SchedulePointDraft(1, 420, False),
    ]


def test_the_default_value_means_continuous_day() -> None:
    read_back = read_night_hours("[[],[],[],[],[],[],[],[]]")

    assert schedule_points_from_night_hours(read_back) == [
        SchedulePointDraft(1, 0, False)
    ]


@pytest.mark.parametrize(
    ("blob", "warning"),
    [
        ("kein JSON", "kein gültiges JSON"),
        ('{"1": [2]}', "kein Array"),
    ],
)
def test_an_unreadable_blob_is_logged_as_an_empty_week(
    blob: str, warning: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours(blob)

    assert read_back == _week()
    assert warning in caplog.text


def test_seven_slots_keep_the_readable_days_and_log_the_gap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],[1],[],[],[],[],[6]]')

    assert read_back[1] == frozenset({1})
    assert read_back[6] == frozenset({6})
    assert read_back[7] == frozenset()
    assert "7 statt acht Slots" in caplog.text


def test_nine_slots_discard_the_extra_slot_and_log_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],[],[],[],[],[],[],[7],[8]]')

    assert read_back[7] == frozenset({7})
    assert all(8 not in hours for hours in read_back.values())
    assert "9 statt acht Slots" in caplog.text


def test_an_hour_given_as_a_number_is_accepted() -> None:
    assert read_night_hours('[[],[3],[],[],[],[],[],[]]')[1] == frozenset({3})


def test_a_boolean_value_is_not_an_hour_number() -> None:
    assert read_night_hours('[[],[true],[],[],[],[],[],[]]')[1] == frozenset()


@pytest.mark.parametrize("value", ['"24"', "24"])
def test_hour_24_is_discarded(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours(f"[[],[{value}],[],[],[],[],[],[]]")

    assert read_back[1] == frozenset()
    assert "verworfen" in caplog.text


def test_a_duplicate_hour_is_kept_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.legacy_data"):
        read_back = read_night_hours('[[],["4",4],[],[],[],[],[],[]]')

    assert read_back[1] == frozenset({4})
    assert "doppelte Nachtstunde" in caplog.text


def test_an_object_instead_of_a_slot_list_counts_as_empty(caplog: pytest.LogCaptureFixture) -> None:
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
def test_a_counter_check_against_the_real_evaluation_rule(
    night_hours: dict[int, frozenset[int]],
) -> None:
    drafts = schedule_points_from_night_hours(night_hours)
    points = [
        SchedulePoint(
            weekday=draft.weekday,
            minute_of_day=draft.minute_of_day,
            setpoint_mode_id=int(draft.night),
            zone_id=1,
        )
        for draft in drafts
    ]

    for day in range(1, 8):
        for hour in range(24):
            moment = datetime(2026, 8, 24 + day - 1, hour)
            point = current_point(points, moment)
            assert point is not None
            old_hours = {str(hour) for hour in night_hours[day]}
            assert bool(point.setpoint_mode_id) == (str(hour) in old_hours)
