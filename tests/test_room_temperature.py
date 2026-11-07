from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from thermoctl.domain.fault import NO_SOURCE, OK, VERALTET, sensor_state
from thermoctl.domain.room_temperature import SensorInput, room_temperature

NOW = datetime(2026, 1, 15, 12, 0, 0)
TIMEOUT = 600


def s(device_id: int, value: str | None, age_s: int | None) -> SensorInput:
    return SensorInput(
        device_id,
        None if value is None else Decimal(value),
        None if age_s is None else NOW - timedelta(seconds=age_s),
    )


def test_no_sensors_is_no_source() -> None:
    r = room_temperature([], NOW, TIMEOUT)
    assert (r.status, r.mean, r.measured_at) == (NO_SOURCE, None, None)
    assert (r.usable_count, r.total_count, r.sensors) == (0, 0, ())
    assert "Keine Temperaturfuehler" in r.reason


def test_one_sensor_value_passes_unchanged_even_beyond_scale() -> None:
    r = room_temperature([s(1, "21.234", 10)], NOW, TIMEOUT)
    assert r.mean == Decimal("21.234")
    assert r.status == OK
    assert r.measured_at == NOW - timedelta(seconds=10)
    assert "21,2 °C von einem Fuehler" in r.reason


def test_two_sensors_mean() -> None:
    r = room_temperature([s(1, "20.00", 10), s(2, "22.00", 20)], NOW, TIMEOUT)
    assert r.mean == Decimal("21.00")
    assert (r.usable_count, r.total_count) == (2, 2)
    assert "Mittelwert von 2 von 2" in r.reason
    assert "nicht brauchbar" not in r.reason


def test_three_sensors_mean_rounds_half_up_to_hundredths() -> None:
    # 60.01 / 3 = 20.00333...
    r = room_temperature([s(1, "20.00", 1), s(2, "20.00", 1), s(3, "20.01", 1)], NOW, TIMEOUT)
    assert r.mean == Decimal("20.00")
    assert r.mean is not None
    assert r.mean.as_tuple().exponent == -2
    # 40.01 / 2 = 20.005 -> half up, not half even
    r2 = room_temperature([s(1, "20.00", 1), s(2, "20.01", 1)], NOW, TIMEOUT)
    assert r2.mean == Decimal("20.01")
    # 60.02 / 3 = 20.00666... -> 20.01
    r3 = room_temperature([s(1, "20.00", 1), s(2, "20.00", 1), s(3, "20.02", 1)], NOW, TIMEOUT)
    assert r3.mean == Decimal("20.01")


def test_one_stale_sensor_is_excluded_and_named() -> None:
    r = room_temperature(
        [s(1, "20.00", 10), s(2, "30.00", TIMEOUT + 1), s(3, "22.00", 10)],
        NOW,
        TIMEOUT,
    )
    assert r.mean == Decimal("21.00")
    assert r.status == OK
    assert (r.usable_count, r.total_count) == (2, 3)
    assert [(v.device_id, v.status) for v in r.sensors] == [
        (1, OK),
        (2, VERALTET),
        (3, OK),
    ]
    assert "nicht brauchbar: Geraet 2" in r.reason


def test_all_stale_is_veraltet_without_mean() -> None:
    r = room_temperature([s(1, "20", TIMEOUT + 1), s(2, "22", TIMEOUT + 500)], NOW, TIMEOUT)
    assert (r.status, r.mean, r.measured_at, r.usable_count) == (VERALTET, None, None, 0)
    assert "veraltet" in r.reason


def test_stale_plus_never_reported_is_veraltet() -> None:
    r = room_temperature([s(1, "20", TIMEOUT + 1), s(2, None, None)], NOW, TIMEOUT)
    assert r.status == VERALTET
    assert [v.status for v in r.sensors] == [VERALTET, NO_SOURCE]


def test_none_ever_reported_is_no_source() -> None:
    r = room_temperature([s(1, None, None), s(2, None, None)], NOW, TIMEOUT)
    assert r.status == NO_SOURCE
    assert "noch nie" in r.reason


def test_value_missing_counts_as_no_source_even_with_timestamp() -> None:
    r = room_temperature([s(1, None, 5), s(2, "21", 5)], NOW, TIMEOUT)
    assert [v.status for v in r.sensors] == [NO_SOURCE, OK]
    assert r.mean == Decimal("21")


def test_timeout_boundary_both_sides() -> None:
    on = room_temperature([s(1, "20", TIMEOUT), s(2, "22", TIMEOUT)], NOW, TIMEOUT)
    assert on.status == OK
    assert on.usable_count == 2
    mixed = room_temperature([s(1, "20", TIMEOUT), s(2, "30", TIMEOUT + 1)], NOW, TIMEOUT)
    assert mixed.mean == Decimal("20")
    assert mixed.usable_count == 1
    over = room_temperature([s(1, "20", TIMEOUT + 1)], NOW, TIMEOUT)
    assert over.status == VERALTET


def test_measured_at_is_newest_usable_not_newest_overall() -> None:
    r = room_temperature(
        [s(1, "20", 300), s(2, "22", 100), s(3, "40", 2000)],
        NOW,
        TIMEOUT,
    )
    assert r.measured_at == NOW - timedelta(seconds=100)
    # A sensor without a value but with a fresh timestamp is not usable and must not
    # set the room's measurement time.
    r2 = room_temperature([s(1, "20", 500), s(2, "22", 100), s(3, None, 0)], NOW, TIMEOUT)
    assert r2.measured_at == NOW - timedelta(seconds=100)


def test_future_timestamp_is_still_ok() -> None:
    r = room_temperature([s(1, "20", -30)], NOW, TIMEOUT)
    assert r.status == OK
    assert r.measured_at == NOW + timedelta(seconds=30)


def test_order_independent() -> None:
    items = [s(1, "20.10", 5), s(2, "21.20", 400), s(3, "30", 9999), s(4, "22.35", 50)]
    base = room_temperature(items, NOW, TIMEOUT)
    assert base.mean == Decimal("21.22")  # 63.65 / 3 = 21.2166..
    for perm in (items[::-1], [items[2], items[0], items[3], items[1]]):
        r = room_temperature(perm, NOW, TIMEOUT)
        assert (r.mean, r.measured_at, r.status, r.usable_count) == (
            base.mean,
            base.measured_at,
            base.status,
            base.usable_count,
        )
        assert sorted((v.device_id, v.status) for v in r.sensors) == sorted(
            (v.device_id, v.status) for v in base.sensors
        )


@pytest.mark.parametrize(
    "age_s",
    [None, -3600, -1, 0, 1, TIMEOUT - 1, TIMEOUT, TIMEOUT + 1, 3600, 10**6],
)
@pytest.mark.parametrize("timeout_s", [0, 1, TIMEOUT])
def test_single_sensor_matches_sensor_state_exactly(age_s: int | None, timeout_s: int) -> None:
    measured_at = None if age_s is None else NOW - timedelta(seconds=age_s)
    value = None if age_s is None else Decimal("21.5")
    r = room_temperature([SensorInput(7, value, measured_at)], NOW, timeout_s)
    expected = sensor_state(measured_at, NOW, timeout_s)
    assert r.status == expected
    assert r.sensors[0].status == expected
    if expected == OK:
        assert (r.mean, r.measured_at) == (value, measured_at)
    else:
        assert (r.mean, r.measured_at) == (None, None)


def test_result_types_are_immutable() -> None:
    inp = s(1, "20", 1)
    r = room_temperature([inp], NOW, TIMEOUT)
    with pytest.raises(FrozenInstanceError):
        inp.value = Decimal("1")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.mean = Decimal("1")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.sensors[0].status = "x"  # type: ignore[misc]
