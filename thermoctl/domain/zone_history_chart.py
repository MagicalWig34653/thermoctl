"""Verdichteter Verlauf einer Zone; Berechnung getrennt vom Datenbankzugriff."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil, floor
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.number_text import temperature_text
from thermoctl.domain.time import local_time

MAX_POINTS = 600
WINDOWS = {"24h": timedelta(hours=24), "3d": timedelta(days=3), "7d": timedelta(days=7)}
# Ein Zyklus dauert rund eine Minute. Ein Stück, das länger ohne Zeile bleibt (Neustart,
# Ausfall), wird nicht als durchgehender Zustand gezeichnet: Linien reißen dort ab.
GAP = timedelta(minutes=10)
# Ein Eintrag gilt höchstens so lange, bis der nächste kommt -- und nie länger als dies.
CYCLE_LIMIT = timedelta(seconds=90)
# Ab diesem Abstand zwischen letztem Wert und Fensterende ist der Verlauf veraltet.
STALE_AFTER = timedelta(minutes=15)
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Ein Lauf: gleichbleibender Sollwert von-bis. Läufe berühren sich genau dann, wenn
# der Sollwert wechselt, ohne dass dazwischen Daten fehlten.
Run = tuple[datetime, datetime, float]


@dataclass(frozen=True)
class Layout:
    """Zeichenfläche einer SVG-Variante; Geometrie steht nur hier, nicht im Template."""

    name: str
    width: int
    height: int
    left: int
    right: int
    top: int
    bottom: int
    lane_top: int
    lane_bottom: int
    label_y: int
    sparse_ticks: bool
    solar_label_min_width: int


DESKTOP = Layout("desktop", 960, 280, 60, 940, 28, 212, 220, 234, 256, False, 150)
MOBILE = Layout("mobile", 360, 330, 40, 350, 22, 250, 258, 272, 294, True, 120)
# Vollbild auf einem hochformatigen Telefon: dieselbe Breite, aber die Höhe füllt den Schirm.
MOBILE_TALL = Layout("mobile-tall", 360, 640, 40, 350, 22, 550, 558, 574, 596, True, 120)
# Vollbild auf breitem Schirm: gleiche Breite, aber höher als die Karte.
DESKTOP_FULL = Layout("desktop-full", 960, 540, 60, 940, 28, 470, 478, 494, 516, False, 150)
LAYOUTS = (DESKTOP, DESKTOP_FULL, MOBILE, MOBILE_TALL)


@dataclass(frozen=True)
class Sample:
    at: datetime
    temperature: float | None
    effective: float | None
    scheduled: float | None
    heat: bool
    solar_k: float = 0.0

    @property
    def solar(self) -> bool:
        return self.solar_k > 0


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
    # (von, bis, Stufe 1..4): Anteil der Zyklen mit Heizanforderung, in Viertel gestuft.
    heat_lane: tuple[tuple[datetime, datetime, int], ...]
    # (von, bis, größte Absenkung in K)
    solar_bands: tuple[tuple[datetime, datetime, float], ...]
    effective_runs: tuple[Run, ...]
    scheduled_runs: tuple[Run, ...]
    start: datetime
    end: datetime
    data_end: datetime
    gap_seconds: float
    minimum: float
    maximum: float
    summary: str
    ticks: tuple[tuple[float, str], ...]
    sparse_ticks: tuple[tuple[float, str], ...]
    y_ticks: tuple[tuple[float, str], ...]
    stale: bool


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
            # Zeilen aus der Zeit vor der Spalte `scheduled_setpoint_c` kennen keinen
            # Zeitplan-Sollwert; dort galt der wirksame Soll, eine Absenkung gab es nicht.
            _float(scheduled) if scheduled is not None else _float(effective),
            heat,
            float(solar) if solar is not None and solar > 0 else 0.0,
        )
        for at, temp, effective, scheduled, heat, solar in rows
    ]


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _runs(samples: list[Sample], pick: Callable[[Sample], float | None]) -> tuple[Run, ...]:
    """Sollwertläufe aus den exakten Zeilen -- nichts wird gemittelt oder verschoben."""
    runs: list[Run] = []
    current: tuple[datetime, datetime, float] | None = None
    previous_at: datetime | None = None
    for sample in samples:
        value = pick(sample)
        gap = previous_at is not None and sample.at - previous_at > GAP
        if current is not None and value == current[2] and not gap:
            current = (current[0], sample.at, current[2])
        else:
            if current is not None:
                # Wechsel ohne Datenlücke: die senkrechte Kante liegt am neuen Zeitpunkt.
                end = sample.at if value is not None and not gap else current[1]
                runs.append((current[0], end, current[2]))
            current = (sample.at, sample.at, value) if value is not None else None
        previous_at = sample.at
    if current is not None:
        runs.append(current)
    return tuple(runs)


def _round_ticks(
    start: datetime, end: datetime, timezone_name: str | None, step_hours: int
) -> tuple[tuple[float, str], ...]:
    """Beschriftungen auf runden Ortszeit-Stunden; die Lage bleibt in UTC eindeutig."""
    zone = ZoneInfo(timezone_name) if timezone_name is not None else UTC
    first = local_time(start, timezone_name).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    wall = first - timedelta(hours=first.hour % step_hours)
    span = (end - start).total_seconds()
    ticks: list[tuple[float, str]] = []
    while True:
        moment = wall.replace(tzinfo=zone).astimezone(UTC).replace(tzinfo=None)
        if moment > end:
            break
        if moment >= start:
            label = (
                f"{WEEKDAYS[wall.weekday()]} {wall:%d.%m.}" if wall.hour == 0 else f"{wall:%H:%M}"
            )
            ticks.append(((moment - start).total_seconds() / span, label))
        wall += timedelta(hours=step_hours)
    return tuple(ticks)


def _tick_step(duration: timedelta) -> int:
    if duration <= timedelta(hours=6):
        return 1
    if duration <= timedelta(hours=30):
        return 3
    if duration <= timedelta(days=4):
        return 12
    return 24


def _hours_text(hours: float) -> str:
    return f"{hours:.1f}".replace(".", ",")


def _duration_text(hours: float) -> str:
    """Stunden ab einer Stunde, darunter Minuten -- 0,0 Stunden wäre keine Auskunft."""
    if hours >= 1:
        return f"{_hours_text(hours)} Stunden"
    return f"{max(1, round(hours * 60))} Minuten"


def build_chart(
    samples: list[Sample], start: datetime, end: datetime, timezone_name: str | None
) -> Chart:
    """Berechnet Buckets, exakte Sollwertläufe und Zustandsbänder ohne HTTP."""
    # Die Istkurve hat höchstens 600 Buckets; Sollwertläufe und Sonnenbänder bleiben
    # davon unberührt und werden aus den einzelnen Zeilen gebildet.
    width = (end - start).total_seconds() / MAX_POINTS
    buckets: dict[int, list[Sample]] = {}
    solar_intervals: list[tuple[datetime, datetime, float]] = []
    covered_seconds = 0.0
    heat_seconds = 0.0
    for index, sample in enumerate(samples):
        bucket_index = min(MAX_POINTS - 1, int((sample.at - start).total_seconds() / width))
        buckets.setdefault(bucket_index, []).append(sample)
        next_at = samples[index + 1].at if index + 1 < len(samples) else end
        interval_end = min(end, next_at, sample.at + CYCLE_LIMIT)
        if interval_end > sample.at:
            seconds = (interval_end - sample.at).total_seconds()
            covered_seconds += seconds
            if sample.heat:
                heat_seconds += seconds
            if sample.solar:
                solar_intervals.append((sample.at, interval_end, sample.solar_k))
    effective_runs = _runs(samples, lambda s: s.effective)
    scheduled_runs = _runs(samples, lambda s: s.scheduled)
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
        for _, items in sorted(buckets.items())
    )
    # Der Wertebereich kommt aus den Läufen, nicht aus den Buckets: Ein kurzer
    # Sollwert, der in keinem Bucket der letzte ist, darf nicht aus dem Bild laufen.
    values = [point.temperature for point in points if point.temperature is not None]
    values += [value for _, _, value in effective_runs + scheduled_runs]
    minimum = floor(min(values) - 0.4) if values else 18
    maximum = ceil(max(values) + 0.4) if values else 21
    if maximum - minimum < 2:
        maximum = minimum + 2
    # Heizspur: aufeinanderfolgende Buckets gleicher Stufe zu einem Strich zusammenfassen.
    lane: list[list[int]] = []
    for index, items in sorted(buckets.items()):
        share = sum(item.heat for item in items) / len(items)
        if share > 0:
            level = ceil(share * 4)
            if lane and lane[-1][1] == index - 1 and lane[-1][2] == level:
                lane[-1][1] = index
            else:
                lane.append([index, index, level])
    heat_lane = tuple(
        (
            start + timedelta(seconds=first * width),
            start + timedelta(seconds=(last + 1) * width),
            level,
        )
        for first, last, level in lane
    )
    # Sonnenabsenkung aus den einzelnen Zeilen: nichts geht verloren, auch keine kurze
    # Phase. Lücken von höchstens einem Bucket werden zugezogen, damit das Band
    # beschränkt bleibt (höchstens 600) und nicht pro Zyklus zerfällt.
    merged: list[tuple[datetime, datetime, float]] = []
    for first, last, k in solar_intervals:
        if merged and (first - merged[-1][1]).total_seconds() <= width:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]), max(k, merged[-1][2]))
        else:
            merged.append((first, last, k))
    data_end = samples[-1].at if samples else start
    stale = bool(samples) and end - data_end > STALE_AFTER
    if samples:
        measured = [sample.temperature for sample in samples if sample.temperature is not None]
        parts = [
            f"Ist {temperature_text(Decimal(str(min(measured))))} bis "
            f"{temperature_text(Decimal(str(max(measured))))}."
            if measured
            else "Kein Istwert."
        ]
        latest = samples[-1]
        if latest.scheduled is not None and latest.effective is not None:
            parts.append(
                f"Zuletzt Zeitplan-Soll {temperature_text(Decimal(str(latest.scheduled)))}, "
                f"wirksamer Soll {temperature_text(Decimal(str(latest.effective)))}."
            )
        if covered_seconds > 0:
            parts.append(
                f"Heizanforderung {heat_seconds / covered_seconds * 100:.0f} % der erfassten Zeit."
            )
        solar_hours = sum((b - a).total_seconds() for a, b, _ in solar_intervals) / 3600
        parts.append(
            f"Sonnenabsenkung {_duration_text(solar_hours)}."
            if solar_hours > 0
            else "Keine Sonnenabsenkung."
        )
        window_hours = (end - start).total_seconds() / 3600
        if covered_seconds / 3600 < window_hours * 0.9:
            parts.append(
                f"Daten für {_hours_text(covered_seconds / 3600)} "
                f"von {_hours_text(window_hours)} Stunden."
            )
        if stale:
            parts.append(f"Letzter Wert {local_time(data_end, timezone_name):%d.%m. %H:%M} Uhr.")
        summary = " ".join(parts)
    else:
        summary = "Keine Verlaufsdaten im gewählten Zeitraum."
    ticks = _round_ticks(start, end, timezone_name, _tick_step(end - start))
    y_step = 1 if maximum - minimum <= 8 else 2
    y_ticks = tuple((float(v), f"{v} °C") for v in range(minimum, maximum + 1, y_step))
    return Chart(
        points=points,
        heat_lane=heat_lane,
        solar_bands=tuple(merged),
        effective_runs=effective_runs,
        scheduled_runs=scheduled_runs,
        start=start,
        end=end,
        data_end=data_end,
        gap_seconds=max(GAP.total_seconds(), 3 * width),
        minimum=float(minimum),
        maximum=float(maximum),
        summary=summary,
        ticks=ticks,
        sparse_ticks=ticks[::2],
        y_ticks=y_ticks,
        stale=stale,
    )


def chart_for_zone(
    session: Session, zone_id: int, period: str, now: datetime, timezone_name: str | None
) -> Chart:
    """Lesefunktion der Domäne; die Session kommt vom aufrufenden Adapter."""
    duration = WINDOWS[period]
    start = now - duration
    return build_chart(read_samples(session, zone_id, start, now), start, now, timezone_name)


def x_position(at: datetime, chart: Chart, layout: Layout) -> float:
    fraction = (at - chart.start).total_seconds() / (chart.end - chart.start).total_seconds()
    return round(layout.left + (layout.right - layout.left) * fraction, 1)


def y_position(value: float, chart: Chart, layout: Layout) -> float:
    fraction = (value - chart.minimum) / (chart.maximum - chart.minimum)
    return round(layout.bottom - (layout.bottom - layout.top) * fraction, 1)


def tick_x(fraction: float, layout: Layout) -> float:
    return round(layout.left + (layout.right - layout.left) * fraction, 1)


def ticks_for(chart: Chart, layout: Layout) -> tuple[tuple[float, str], ...]:
    return chart.sparse_ticks if layout.sparse_ticks else chart.ticks


def line_path(chart: Chart, layout: Layout) -> str:
    """Istkurve; bricht bei fehlendem Messwert und bei Datenlücken ab."""
    segments: list[list[tuple[float, float]]] = []
    previous: Point | None = None
    for point in chart.points:
        if point.temperature is None:
            previous = None
            continue
        broken = previous is None or (point.at - previous.at).total_seconds() > chart.gap_seconds
        xy = (x_position(point.at, chart, layout), y_position(point.temperature, chart, layout))
        if broken:
            segments.append([xy])
        else:
            segments[-1].append(xy)
        previous = point
    parts: list[str] = []
    for segment in segments:
        if len(segment) == 1:  # ein einzelner Wert wird als kurzer Strich sichtbar
            segment.append((segment[0][0] + 0.4, segment[0][1]))
        parts.append(
            " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(segment))
        )
    return " ".join(parts)


def step_path(runs: tuple[Run, ...], chart: Chart, layout: Layout) -> str:
    """Sollwertstufen: waagerecht je Lauf, senkrecht nur beim Wechsel ohne Datenlücke."""
    parts: list[str] = []
    previous_end: datetime | None = None
    for first, last, value in runs:
        y = y_position(value, chart, layout)
        x2 = max(x_position(last, chart, layout), x_position(first, chart, layout) + 0.4)
        if previous_end == first:
            parts.append(f"V{y:.1f} H{x2:.1f}")
        else:
            parts.append(f"M{x_position(first, chart, layout):.1f},{y:.1f} H{x2:.1f}")
        previous_end = last
    return " ".join(parts)
