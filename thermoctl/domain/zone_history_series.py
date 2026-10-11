"""Verlauf einer Zone als Zahlenreihe -- für Adapter, die nicht zeichnen (MCP).

Die Oberfläche zeichnet denselben Verlauf (`zone_history_chart`); hier steht dieselbe
Messreihe als verdichtete Liste, damit ein Modell oder ein Entwickler die Frage „warum war
es gestern Abend kalt" mit Zahlen beantworten kann. Gelesen wird mit
`zone_history_chart.read_samples` -- dieselben indizierten Spalten, kein zweiter Weg zu
den Messwerten. Eine Woche sind rund zehntausend Zeilen; sie werden in Python verdichtet,
nicht in SQL, damit die Auswertung datenbankagnostisch bleibt.

**Lücken bleiben Lücken.** Ein Zeitfach ohne Messung erscheint nicht in `points`, wird
nicht interpoliert und nicht mit 0 gefüllt; `buckets_without_data` zählt sie, damit ein
Aufrufer sie erkennt, ohne die Zeitachse nachzurechnen. Unbekanntes bleibt `None`:
Zeilen aus der Zeit vor den Spalten für Sollwert vor Absenkung und Absenkung tragen dazu
keine Aussage, und `0.0` heißt dort belegt „keine Absenkung".

**`heat_share`** ist der Anteil der Messungen, bei denen die Regelung eine Heizanforderung
ausgab (`would_heat`). Es ist eine Aussage über die Entscheidung der Regelung, keine über
den Raum; ob und wie ein Gerät daraufhin schaltete, steht im Schaltprotokoll
(`domain/device_commands.py`).

Keine Rechteprüfung hier: Die Adapter prüfen, wie bei `list_commands`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from math import ceil, floor

from sqlalchemy.orm import Session

from thermoctl.domain.device_commands import naive_utc
from thermoctl.domain.zone_history_chart import Sample, read_samples

MAX_SPAN = timedelta(days=7)
MIN_HOURS, MAX_HOURS = 1, 168
DEFAULT_HOURS = 24
MIN_POINTS, MAX_POINTS = 10, 1000
DEFAULT_POINTS = 300


class HistoryRangeError(ValueError):
    """Eine Angabe zum Verlauf liegt außerhalb dessen, was zulässig ist.

    Eigene Klasse, damit Adapter die deutsche Meldung weitergeben können, ohne jeden
    `ValueError` (auch fremde, mit Eingaben des Aufrufers im Text) preiszugeben.
    """


@dataclass(frozen=True)
class HistoryPoint:
    at: datetime  # Beginn des Zeitfachs, UTC mit Zeitzone
    measured_c: float | None
    measured_min_c: float | None
    measured_max_c: float | None
    setpoint_c: float | None
    scheduled_setpoint_c: float | None
    solar_setback_k: float | None
    heat_share: float


@dataclass(frozen=True)
class HistorySummary:
    sample_count: int
    first_at: datetime | None
    last_at: datetime | None
    measured_min_c: float | None
    measured_avg_c: float | None
    measured_max_c: float | None
    heat_share: float | None
    buckets_without_data: int


@dataclass(frozen=True)
class HistorySeries:
    start: datetime  # UTC mit Zeitzone
    end: datetime
    bucket_minutes: int
    summary: HistorySummary
    points: tuple[HistoryPoint, ...]


def resolve_window(
    *,
    hours: int = DEFAULT_HOURS,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Zeitfenster als naive UTC: `from_at`/`to_at`, sonst die letzten `hours` bis `now`.

    Fehlt nur eines der beiden, ergänzt `hours` es (Ende `now`, wenn `to_at` fehlt).
    Naive Angaben gelten als UTC (wie beim Schaltprotokoll).
    """
    if hours < MIN_HOURS or hours > MAX_HOURS:
        raise HistoryRangeError(f"hours muss zwischen {MIN_HOURS} und {MAX_HOURS} liegen")
    end = naive_utc(to_at) if to_at is not None else now
    assert end is not None
    start = naive_utc(from_at) if from_at is not None else end - timedelta(hours=hours)
    assert start is not None
    if start >= end:
        raise HistoryRangeError("from_at muss vor to_at liegen")
    if end - start > MAX_SPAN:
        raise HistoryRangeError("Das Zeitfenster darf höchstens 7 Tage umfassen")
    return start, end


def history_series(
    session: Session,
    zone_id: int,
    start: datetime,
    end: datetime,
    max_points: int = DEFAULT_POINTS,
) -> HistorySeries:
    """Messreihe einer Zone in `[start, end]`, in gleich breite Zeitfächer verdichtet."""
    if max_points < MIN_POINTS or max_points > MAX_POINTS:
        raise HistoryRangeError(f"max_points muss zwischen {MIN_POINTS} und {MAX_POINTS} liegen")
    start_utc, end_utc = naive_utc(start), naive_utc(end)
    assert start_utc is not None and end_utc is not None
    if start_utc >= end_utc:
        raise HistoryRangeError("start muss vor end liegen")

    span_seconds = (end_utc - start_utc).total_seconds()
    width_minutes = max(1, ceil(ceil(span_seconds / 60) / max_points))
    width_seconds = width_minutes * 60
    bucket_count = max(1, ceil(span_seconds / width_seconds))

    buckets: dict[int, list[Sample]] = {}
    samples = read_samples(session, zone_id, start_utc, end_utc)
    for sample in samples:
        # Eine Messung genau auf `end` gehört noch ins letzte Fach.
        index = min(
            floor((sample.at - start_utc).total_seconds() / width_seconds), bucket_count - 1
        )
        buckets.setdefault(index, []).append(sample)

    points = tuple(
        _point(start_utc + timedelta(seconds=index * width_seconds), buckets[index])
        for index in sorted(buckets)
    )
    temperatures = [s.temperature for s in samples if s.temperature is not None]
    summary = HistorySummary(
        sample_count=len(samples),
        first_at=_aware(samples[0].at) if samples else None,
        last_at=_aware(samples[-1].at) if samples else None,
        measured_min_c=min(temperatures) if temperatures else None,
        measured_avg_c=_mean(temperatures, 1),
        measured_max_c=max(temperatures) if temperatures else None,
        heat_share=_share(samples),
        buckets_without_data=bucket_count - len(buckets),
    )
    return HistorySeries(
        start=_aware(start_utc),
        end=_aware(end_utc),
        bucket_minutes=width_minutes,
        summary=summary,
        points=points,
    )


def _point(at: datetime, samples: list[Sample]) -> HistoryPoint:
    temperatures = [s.temperature for s in samples if s.temperature is not None]
    setbacks = [s.solar_k for s in samples if s.solar_k is not None]
    share = _share(samples)
    assert share is not None  # ein Fach entsteht nur aus mindestens einer Messung
    return HistoryPoint(
        at=_aware(at),
        measured_c=_mean(temperatures, 1),
        measured_min_c=min(temperatures) if temperatures else None,
        measured_max_c=max(temperatures) if temperatures else None,
        setpoint_c=samples[-1].effective,
        scheduled_setpoint_c=samples[-1].scheduled,
        solar_setback_k=max(setbacks) if setbacks else None,
        heat_share=share,
    )


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


def _round(value: float, places: int) -> float:
    exact = Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return float(exact)


def _mean(values: list[float], places: int) -> float | None:
    return _round(sum(values) / len(values), places) if values else None


def _share(samples: list[Sample]) -> float | None:
    if not samples:
        return None
    return _round(sum(1 for s in samples if s.heat) / len(samples), 2)
