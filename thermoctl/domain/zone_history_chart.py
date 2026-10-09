"""Verdichteter Verlauf einer Zone; Berechnung getrennt vom Datenbankzugriff."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil, floor

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.time import local_time

MAX_POINTS = 600
WINDOWS = {"24h": timedelta(hours=24), "3d": timedelta(days=3), "7d": timedelta(days=7)}


@dataclass(frozen=True)
class Sample:
    at: datetime
    temperature: float | None
    effective: float | None
    scheduled: float | None
    heat: bool
    solar: bool


@dataclass(frozen=True)
class Point:
    at: datetime
    temperature: float | None
    effective: float | None
    scheduled: float | None
    heat_share: float
    solar: bool


@dataclass(frozen=True)
class Chart:
    points: tuple[Point, ...]
    heat_bands: tuple[tuple[datetime, datetime, float], ...]
    solar_bands: tuple[tuple[datetime, datetime], ...]
    effective_steps: tuple[tuple[datetime, float], ...]
    scheduled_steps: tuple[tuple[datetime, float], ...]
    start: datetime
    end: datetime
    minimum: float
    maximum: float
    summary: str
    ticks: tuple[tuple[float, str], ...]
    y_ticks: tuple[tuple[float, str], ...]


def read_samples(session: Session, zone_id: int, start: datetime, end: datetime) -> list[Sample]:
    """Liest ausschließlich die indizierten Spalten einer Zone im Zeitfenster."""
    rows = session.execute(
        select(
            ShadowDecision.decided_at,
            ShadowDecision.temperature_c,
            ShadowDecision.setpoint_c,
            ShadowDecision.scheduled_setpoint_c,
            ShadowDecision.would_heat,
            ShadowDecision.solar_setback_k,
        )
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.decided_at >= start,
            ShadowDecision.decided_at <= end,
        )
        .order_by(ShadowDecision.decided_at, ShadowDecision.id)
    )
    return [
        Sample(
            at,
            _float(temp),
            _float(effective),
            _float(scheduled) if scheduled is not None else _float(effective),
            heat,
            solar is not None and solar > 0,
        )
        for at, temp, effective, scheduled, heat, solar in rows
    ]


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _merge_bands(bands: list[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in bands:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


def build_chart(
    samples: list[Sample], start: datetime, end: datetime, timezone_name: str | None
) -> Chart:
    """Berechnet Buckets, exakte Sollwertwechsel und Zustandsbänder ohne HTTP."""
    # Die Istkurve hat höchstens 600 Buckets; Sollwertwechsel bleiben separat
    # als exakte Stufen an ihren UTC-Zeitpunkten erhalten.
    width = (end - start).total_seconds() / MAX_POINTS
    buckets: dict[int, list[Sample]] = {}
    previous_effective: float | None = None
    previous_scheduled: float | None = None
    effective_steps: list[tuple[datetime, float]] = []
    scheduled_steps: list[tuple[datetime, float]] = []
    solar_intervals: list[tuple[datetime, datetime]] = []
    heat_intervals: list[tuple[datetime, datetime]] = []
    for index, sample in enumerate(samples):
        if sample.effective != previous_effective:
            if sample.effective is not None:
                effective_steps.append((sample.at, sample.effective))
            previous_effective = sample.effective
        if sample.scheduled != previous_scheduled:
            if sample.scheduled is not None:
                scheduled_steps.append((sample.at, sample.scheduled))
            previous_scheduled = sample.scheduled
        bucket_index = min(MAX_POINTS - 1, int((sample.at - start).total_seconds() / width))
        buckets.setdefault(bucket_index, []).append(sample)
        # Fehlende Zyklen werden nicht als durchgehende Heiz- oder Sonnenphase ausgegeben.
        next_at = samples[index + 1].at if index + 1 < len(samples) else end
        interval_end = min(end, next_at, sample.at + timedelta(seconds=90))
        if interval_end > sample.at:
            if sample.solar:
                solar_intervals.append((sample.at, interval_end))
            if sample.heat:
                heat_intervals.append((sample.at, interval_end))
    points = tuple(
        Point(
            at=items[-1].at,
            temperature=(sum(temps) / len(temps))
            if (temps := [item.temperature for item in items if item.temperature is not None])
            else None,
            effective=items[-1].effective,
            scheduled=items[-1].scheduled,
            heat_share=sum(item.heat for item in items) / len(items),
            solar=any(item.solar for item in items),
        )
        for items in buckets.values()
    )
    values = [
        value
        for point in points
        for value in (point.temperature, point.effective, point.scheduled)
        if value is not None
    ]
    minimum = floor(min(values) - 0.4) if values else 18
    maximum = ceil(max(values) + 0.4) if values else 21
    if maximum - minimum < 2:
        maximum = minimum + 2
    heat_bands = tuple(
        (
            start + timedelta(seconds=index * width),
            start + timedelta(seconds=(index + 1) * width),
            sum(item.heat for item in items) / len(items),
        )
        for index, items in buckets.items()
        if any(item.heat for item in items)
    )
    solar_bands = _merge_bands(
        [
            (
                start + timedelta(seconds=index * width),
                start + timedelta(seconds=(index + 1) * width),
            )
            for index, items in buckets.items()
            if any(item.solar for item in items)
        ]
    )
    heat_hours = sum((b - a).total_seconds() for a, b in heat_intervals) / 3600
    solar_hours = sum((b - a).total_seconds() for a, b in solar_intervals) / 3600
    if samples:
        measured = [sample.temperature for sample in samples if sample.temperature is not None]
        measured_text = (
            f"Ist {min(measured):.1f} bis {max(measured):.1f} °C. "
            if measured
            else "Kein Istwert. "
        )
        summary = (
            measured_text
            + f"Heizzeit {heat_hours / ((end - start).total_seconds() / 3600) * 100:.0f} %. "
            f"Sonnenabsenkung {solar_hours:.1f} Stunden."
        )
    else:
        summary = "Keine Verlaufsdaten im gewählten Zeitraum."
    # UTC-Positionen bleiben auch bei der doppelten Stunde der Winterzeit eindeutig.
    ticks = tuple(
        (i / 4, local_time(start + (end - start) * (i / 4), timezone_name).strftime("%d.%m. %H:%M"))
        for i in range(5)
    )
    y_ticks = tuple((float(v), f"{v} °C") for v in range(minimum, maximum + 1))
    return Chart(
        points,
        heat_bands,
        solar_bands,
        tuple(effective_steps),
        tuple(scheduled_steps),
        start,
        end,
        float(minimum),
        float(maximum),
        summary,
        ticks,
        y_ticks,
    )


def chart_for_zone(
    session: Session, zone_id: int, period: str, now: datetime, timezone_name: str | None
) -> Chart:
    """Lesefunktion der Domäne; die Session kommt vom aufrufenden Adapter."""
    duration = WINDOWS[period]
    start = now - duration
    return build_chart(read_samples(session, zone_id, start, now), start, now, timezone_name)


def x_position(at: datetime, chart: Chart) -> float:
    return 60 + 880 * (at - chart.start).total_seconds() / (chart.end - chart.start).total_seconds()


def y_position(value: float, chart: Chart) -> float:
    return 240 - 210 * (value - chart.minimum) / (chart.maximum - chart.minimum)


def line_path(points: tuple[Point, ...], chart: Chart) -> str:
    parts: list[str] = []
    connected = False
    for point in points:
        if point.temperature is None:
            connected = False
            continue
        command = "L" if connected else "M"
        x, y = x_position(point.at, chart), y_position(point.temperature, chart)
        parts.append(f"{command}{x:.1f},{y:.1f}")
        connected = True
    return " ".join(parts)


def step_path(steps: tuple[tuple[datetime, float], ...], chart: Chart) -> str:
    if not steps:
        return ""
    parts = [f"M{x_position(steps[0][0], chart):.1f},{y_position(steps[0][1], chart):.1f}"]
    for at, value in steps[1:]:
        x = x_position(at, chart)
        parts.append(f"H{x:.1f} V{y_position(value, chart):.1f}")
    parts.append("H940")
    return " ".join(parts)


def mobile_x_position(at: datetime, chart: Chart) -> float:
    return 45 + 305 * (at - chart.start).total_seconds() / (chart.end - chart.start).total_seconds()


def mobile_y_position(value: float, chart: Chart) -> float:
    return 220 - 190 * (value - chart.minimum) / (chart.maximum - chart.minimum)


def mobile_line_path(points: tuple[Point, ...], chart: Chart) -> str:
    parts: list[str] = []
    connected = False
    for point in points:
        if point.temperature is None:
            connected = False
            continue
        command = "L" if connected else "M"
        x, y = mobile_x_position(point.at, chart), mobile_y_position(point.temperature, chart)
        parts.append(f"{command}{x:.1f},{y:.1f}")
        connected = True
    return " ".join(parts)


def mobile_step_path(steps: tuple[tuple[datetime, float], ...], chart: Chart) -> str:
    if not steps:
        return ""
    parts = [
        f"M{mobile_x_position(steps[0][0], chart):.1f},{mobile_y_position(steps[0][1], chart):.1f}"
    ]
    for at, value in steps[1:]:
        x = mobile_x_position(at, chart)
        parts.append(f"H{x:.1f} V{mobile_y_position(value, chart):.1f}")
    parts.append("H350")
    return " ".join(parts)
