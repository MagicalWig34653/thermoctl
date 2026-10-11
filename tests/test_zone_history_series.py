"""Verlauf als Zahlenreihe: Fachbreite, Verdichtung, Lücken, Unbekanntes, Zonengrenze."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_zone
from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.zone_history_series import (
    HistoryRangeError,
    history_series,
    resolve_window,
)

START = datetime(2026, 8, 29, 8, 0)  # naive UTC, wie in der Datenbank


def _row(
    session: Session,
    zone_id: int,
    at: datetime,
    *,
    temperature: str | None = "20.0",
    setpoint: str | None = "21.0",
    scheduled: str | None = "21.0",
    solar: str | None = "0.0",
    heat: bool = False,
) -> None:
    session.add(
        ShadowDecision(
            decided_at=at,
            zone_id=zone_id,
            temperature_c=None if temperature is None else Decimal(temperature),
            setpoint_c=None if setpoint is None else Decimal(setpoint),
            scheduled_setpoint_c=None if scheduled is None else Decimal(scheduled),
            solar_setback_k=None if solar is None else Decimal(solar),
            setpoint_reason="Zeitplan",
            would_heat=heat,
            outcome_code="an" if heat else "aus",
            reason="Test",
        )
    )
    session.flush()


@pytest.mark.parametrize(
    ("span", "max_points", "minutes"),
    [
        (timedelta(hours=24), 300, 5),  # 1440 / 300 = 4,8 -> aufgerundet
        (timedelta(hours=1), 300, 1),  # nie unter einer Minute
        (timedelta(days=7), 300, 34),  # 10080 / 300 = 33,6
        (timedelta(minutes=100), 10, 10),  # glatt teilbar
        (timedelta(seconds=30), 10, 1),  # kürzer als eine Minute
    ],
)
def test_bucket_width_is_whole_minutes_rounded_up(
    session: Session, span: timedelta, max_points: int, minutes: int
) -> None:
    zone = create_zone(session, "fachbreite")

    series = history_series(session, zone.id, START, START + span, max_points)

    assert series.bucket_minutes == minutes


def test_samples_in_one_bucket_are_averaged_and_the_last_setpoint_wins(session: Session) -> None:
    zone = create_zone(session, "verdichtet")
    _row(
        session,
        zone.id,
        START,
        temperature="20.0",
        setpoint="21.0",
        scheduled="21.0",
        solar="0.0",
        heat=True,
    )
    _row(
        session,
        zone.id,
        START + timedelta(minutes=1),
        temperature="20.5",
        setpoint="20.0",
        scheduled="21.0",
        solar="1.0",
        heat=True,
    )
    _row(
        session,
        zone.id,
        START + timedelta(minutes=2),
        temperature="21.0",
        setpoint="19.5",
        scheduled="20.5",
        solar="0.5",
        heat=False,
    )

    # 50 Minuten, 10 Punkte -> Fächer à 5 Minuten; alle drei Messungen liegen im ersten
    series = history_series(session, zone.id, START, START + timedelta(minutes=50), 10)
    assert series.bucket_minutes == 5
    (point,) = series.points

    assert point.at == datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    assert (point.measured_c, point.measured_min_c, point.measured_max_c) == (20.5, 20.0, 21.0)
    assert point.setpoint_c == 19.5  # letzter Wert, nicht Mittel
    assert point.scheduled_setpoint_c == 20.5
    assert point.solar_setback_k == 1.0  # größter Wert im Fach
    assert point.heat_share == 0.67  # 2 von 3


def test_mean_is_rounded_to_a_tenth(session: Session) -> None:
    zone = create_zone(session, "gerundet")
    for minute, value in enumerate(["20.00", "20.10", "20.10"]):
        _row(session, zone.id, START + timedelta(minutes=minute), temperature=value)

    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)

    assert series.points[0].measured_c == 20.1  # 20,0667
    assert series.summary.measured_avg_c == 20.1


def test_empty_buckets_are_missing_not_interpolated_and_are_counted(session: Session) -> None:
    zone = create_zone(session, "luecke")
    _row(session, zone.id, START + timedelta(minutes=1), temperature="19.0")
    _row(session, zone.id, START + timedelta(minutes=57), temperature="22.0")

    # 60 Minuten, 10 Punkte -> zehn Fächer à 6 Minuten; belegt sind das erste und das letzte
    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)

    assert [p.at.minute for p in series.points] == [0, 54]
    assert [p.measured_c for p in series.points] == [19.0, 22.0]
    assert series.summary.buckets_without_data == 8


def test_unknown_values_stay_none_and_zero_setback_stays_zero(session: Session) -> None:
    zone = create_zone(session, "unbekannt")
    # Altzeile: weder Sollwert vor Absenkung noch Absenkung aufgezeichnet, kein Messwert.
    _row(session, zone.id, START, temperature=None, scheduled=None, solar=None)
    # Neue Zeile in einem anderen Fach: belegt "keine Absenkung".
    _row(session, zone.id, START + timedelta(minutes=30), temperature="20.0", solar="0.0")

    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)
    old, new = series.points

    assert old.measured_c is None
    assert old.measured_min_c is None and old.measured_max_c is None
    assert old.scheduled_setpoint_c is None
    assert old.solar_setback_k is None
    assert new.solar_setback_k == 0.0
    assert new.scheduled_setpoint_c == 21.0
    assert series.summary.measured_avg_c == 20.0  # die unbekannte Messung zählt nicht als 0


def test_empty_window_has_no_points_and_unknown_summary(session: Session) -> None:
    zone = create_zone(session, "leer")

    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)

    assert series.points == ()
    summary = series.summary
    assert summary.sample_count == 0
    assert summary.first_at is None and summary.last_at is None
    assert summary.measured_min_c is None and summary.measured_avg_c is None
    assert summary.measured_max_c is None
    assert summary.heat_share is None
    assert summary.buckets_without_data == 10


def test_samples_of_other_zones_and_outside_the_window_never_appear(session: Session) -> None:
    zone = create_zone(session, "eigene")
    other = create_zone(session, "fremde")
    _row(session, zone.id, START + timedelta(minutes=5), temperature="20.0")
    _row(session, other.id, START + timedelta(minutes=5), temperature="30.0", heat=True)
    _row(session, zone.id, START - timedelta(minutes=1), temperature="10.0")
    _row(session, zone.id, START + timedelta(minutes=61), temperature="10.0")

    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)

    assert series.summary.sample_count == 1
    assert series.summary.measured_max_c == 20.0
    assert series.summary.heat_share == 0.0


def test_summary_covers_all_samples_and_reports_utc_bounds(session: Session) -> None:
    zone = create_zone(session, "gesamt")
    _row(session, zone.id, START, temperature="18.0", heat=True)
    _row(session, zone.id, START + timedelta(minutes=30), temperature="22.0", heat=False)
    _row(session, zone.id, START + timedelta(minutes=59), temperature="20.0", heat=False)
    _row(session, zone.id, START + timedelta(minutes=60), temperature="20.0", heat=False)

    series = history_series(session, zone.id, START, START + timedelta(minutes=60), 10)

    summary = series.summary
    assert summary.sample_count == 4
    assert summary.first_at == datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    assert summary.last_at == datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    assert (summary.measured_min_c, summary.measured_max_c) == (18.0, 22.0)
    assert summary.measured_avg_c == 20.0
    assert summary.heat_share == 0.25
    # Die Messung genau am Fensterende gehört ins letzte Fach, nicht in ein elftes.
    assert series.points[-1].at == datetime(2026, 8, 29, 8, 54, tzinfo=UTC)
    assert series.start == datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    assert series.end == datetime(2026, 8, 29, 9, 0, tzinfo=UTC)


def test_aware_bounds_are_converted_to_utc(session: Session) -> None:
    zone = create_zone(session, "zeitzone")
    _row(session, zone.id, START + timedelta(minutes=5))
    plus_two = datetime(2026, 8, 29, 10, 0, tzinfo=timezone(timedelta(hours=2)))  # = 08:00 UTC

    series = history_series(session, zone.id, plus_two, plus_two + timedelta(minutes=60), 10)

    assert series.summary.sample_count == 1
    assert series.start == datetime(2026, 8, 29, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize("max_points", [0, 9, 1001])
def test_max_points_outside_its_range_is_refused(session: Session, max_points: int) -> None:
    zone = create_zone(session, "punkte")

    with pytest.raises(HistoryRangeError, match="max_points muss zwischen 10 und 1000"):
        history_series(session, zone.id, START, START + timedelta(hours=1), max_points)


def test_max_points_bounds_are_accepted(session: Session) -> None:
    zone = create_zone(session, "punktegrenze")
    for value in (10, 1000):
        history_series(session, zone.id, START, START + timedelta(hours=1), value)


def test_series_refuses_a_backwards_window(session: Session) -> None:
    zone = create_zone(session, "rueckwaerts")

    with pytest.raises(HistoryRangeError, match="vor"):
        history_series(session, zone.id, START, START)


NOW = datetime(2026, 8, 29, 12, 0)


def test_window_defaults_to_the_last_hours() -> None:
    assert resolve_window(hours=6, now=NOW) == (NOW - timedelta(hours=6), NOW)
    assert resolve_window(now=NOW) == (NOW - timedelta(hours=24), NOW)


def test_explicit_bounds_win_over_hours_and_naive_means_utc() -> None:
    aware_from = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)

    start, end = resolve_window(
        hours=1, from_at=aware_from, to_at=datetime(2026, 8, 29, 11, 0), now=NOW
    )

    assert (start, end) == (datetime(2026, 8, 29, 10, 0), datetime(2026, 8, 29, 11, 0))


def test_only_from_at_runs_until_now_and_only_to_at_goes_back_hours() -> None:
    assert resolve_window(from_at=NOW - timedelta(hours=3), now=NOW) == (
        NOW - timedelta(hours=3),
        NOW,
    )
    earlier = NOW - timedelta(hours=5)
    assert resolve_window(hours=2, to_at=earlier, now=NOW) == (
        earlier - timedelta(hours=2),
        earlier,
    )


@pytest.mark.parametrize("hours", [0, -1, 169])
def test_hours_outside_its_range_is_refused(hours: int) -> None:
    with pytest.raises(HistoryRangeError, match="hours muss zwischen 1 und 168"):
        resolve_window(hours=hours, now=NOW)


def test_hours_bounds_are_accepted() -> None:
    assert resolve_window(hours=1, now=NOW)[0] == NOW - timedelta(hours=1)
    assert resolve_window(hours=168, now=NOW)[0] == NOW - timedelta(days=7)


def test_window_must_run_forward() -> None:
    with pytest.raises(HistoryRangeError, match="from_at muss vor to_at"):
        resolve_window(from_at=NOW, to_at=NOW, now=NOW)
    with pytest.raises(HistoryRangeError, match="from_at muss vor to_at"):
        resolve_window(from_at=NOW, to_at=NOW - timedelta(hours=1), now=NOW)


def test_window_longer_than_seven_days_is_refused() -> None:
    with pytest.raises(HistoryRangeError, match="höchstens 7 Tage"):
        resolve_window(from_at=NOW - timedelta(days=7, minutes=1), to_at=NOW, now=NOW)
    # genau sieben Tage ist erlaubt
    assert resolve_window(from_at=NOW - timedelta(days=7), to_at=NOW, now=NOW)
