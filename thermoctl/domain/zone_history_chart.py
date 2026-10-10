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
    # None = unbekannt: Zeilen aus der Zeit vor der Aufzeichnung tragen keine Aussage über die
    # Absenkung. 0.0 heißt dagegen belegt: keine Absenkung.
    solar_k: float | None = 0.0


@dataclass(frozen=True)
class Point:
    """Ein Abschnitt zusammenhängender Messzeilen innerhalb eines Buckets."""

    at: datetime  # Mitte zwischen erster und letzter Messzeile
    first: datetime
    last: datetime
    temperature: float  # Mittelwert des Abschnitts
    low: float
    high: float
    joined: bool  # lückenlos (höchstens GAP) an den vorigen Abschnitt angeschlossen


# (von, bis, Stufe 1..4): Anteil der Zyklen mit Heizanforderung, in Viertel gestuft.
HeatCell = tuple[datetime, datetime, int]
# (von, bis, kleinste Absenkung in K, größte Absenkung in K)
SolarBand = tuple[datetime, datetime, float, float]


@dataclass(frozen=True)
class Chart:
    has_data: bool
    points: tuple[Point, ...]
    heat_lane: tuple[HeatCell, ...]
    solar_bands: tuple[SolarBand, ...]
    # (von, bis): Zeilen ohne Aussage über die Absenkung (vor der Aufzeichnung)
    unknown_bands: tuple[tuple[datetime, datetime], ...]
    effective_runs: tuple[Run, ...]
    scheduled_runs: tuple[Run, ...]
    start: datetime
    end: datetime
    data_end: datetime
    bucket_seconds: float
    minimum: float
    maximum: float
    summary: str
    actual_text: str
    scheduled_text: str
    effective_text: str
    bucket_text: str
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
            # Zeilen aus der Zeit vor der Spalte `scheduled_setpoint_c` kennen weder den
            # Zeitplan-Sollwert noch die Absenkung. Beides bleibt unbekannt: weder wird der
            # wirksame Soll als Zeitplan-Soll ausgegeben noch "keine Absenkung" behauptet.
            _float(scheduled),
            heat,
            None if scheduled is None and solar is None else _solar_value(solar),
        )
        for at, temp, effective, scheduled, heat, solar in rows
    ]


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _solar_value(value: Decimal | None) -> float:
    return float(value) if value is not None and value > 0 else 0.0


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


def _minutes_text(seconds: float) -> str:
    if seconds < 120:
        return f"{round(seconds)} Sekunden"
    return f"{round(seconds / 60)} Minuten"


def _k_text(low: float, high: float) -> str:
    """Betrag der Absenkung: ein Wert, oder der Bereich, wenn er sich im Band geändert hat."""
    low_text, high_text = f"{low:.1f}".replace(".", ","), f"{high:.1f}".replace(".", ",")
    return f"{low_text} K" if low_text == high_text else f"{low_text} bis {high_text} K"


def _merge_touching(
    intervals: list[tuple[datetime, datetime, float]],
) -> list[SolarBand]:
    """Fügt Zeitspannen zusammen, die sich genau berühren; ein Abstand bleibt ein Abstand."""
    merged: list[SolarBand] = []
    for first, last, k in intervals:
        if merged and merged[-1][1] == first:
            previous = merged[-1]
            merged[-1] = (previous[0], last, min(previous[2], k), max(previous[3], k))
        else:
            merged.append((first, last, k, k))
    return merged


def build_chart(
    samples: list[Sample], start: datetime, end: datetime, timezone_name: str | None
) -> Chart:
    """Berechnet Abschnitte, exakte Sollwertläufe und Zustandsbänder ohne HTTP.

    Gezeichnet wird nur, was Zeilen belegen. Eine Zeile gilt für ihren Zyklus (bis zur
    nächsten Zeile, höchstens CYCLE_LIMIT); alles, was Zeitspannen färbt -- Sonnenband,
    unbekannte Absenkung, Heizspur --, besteht aus solchen Zeitspannen und nie aus mehr.
    Verdichtet wird nur die Istkurve (Mittelwert je Abschnitt), und das ist beschriftet.
    """
    width = (end - start).total_seconds() / MAX_POINTS
    buckets: dict[int, list[int]] = {}
    solar_intervals: list[tuple[datetime, datetime, float]] = []
    unknown_intervals: list[tuple[datetime, datetime, float]] = []
    ends: list[datetime] = []
    covered_seconds = 0.0
    heat_seconds = 0.0
    unknown_seconds = 0.0
    for index, sample in enumerate(samples):
        bucket_index = min(MAX_POINTS - 1, int((sample.at - start).total_seconds() / width))
        buckets.setdefault(bucket_index, []).append(index)
        next_at = samples[index + 1].at if index + 1 < len(samples) else end
        interval_end = max(sample.at, min(end, next_at, sample.at + CYCLE_LIMIT))
        ends.append(interval_end)
        if interval_end > sample.at:
            seconds = (interval_end - sample.at).total_seconds()
            covered_seconds += seconds
            if sample.heat:
                heat_seconds += seconds
            if sample.solar_k is None:
                unknown_seconds += seconds
                unknown_intervals.append((sample.at, interval_end, 0.0))
            elif sample.solar_k > 0:
                solar_intervals.append((sample.at, interval_end, sample.solar_k))
    effective_runs = _runs(samples, lambda s: s.effective)
    scheduled_runs = _runs(samples, lambda s: s.scheduled)
    points = _points(samples, buckets)
    # Der Wertebereich kommt aus den Extremwerten und den Läufen, nicht aus den Mittelwerten:
    # Ein kurzer Ausschlag oder Sollwert darf nicht aus dem Bild laufen.
    values = [point.low for point in points] + [point.high for point in points]
    values += [value for _, _, value in effective_runs + scheduled_runs]
    minimum = floor(min(values) - 0.4) if values else 18
    maximum = ceil(max(values) + 0.4) if values else 21
    if maximum - minimum < 2:
        maximum = minimum + 2
    heat_lane = _heat_lane(samples, ends, buckets)
    solar_bands = tuple(_merge_touching(solar_intervals))
    unknown_bands = tuple((a, b) for a, b, _, _ in _merge_touching(unknown_intervals))
    data_end = samples[-1].at if samples else start
    stale = bool(samples) and end - data_end > STALE_AFTER
    measured = [(s.at, s.temperature) for s in samples if s.temperature is not None]
    latest = samples[-1] if samples else None
    if samples and latest is not None:
        parts = [
            f"Ist {temperature_text(Decimal(str(min(t for _, t in measured))))} bis "
            f"{temperature_text(Decimal(str(max(t for _, t in measured))))}."
            if measured
            else "Kein Istwert."
        ]
        if latest.scheduled is not None and latest.effective is not None:
            parts.append(
                f"Zuletzt Zeitplan-Soll {temperature_text(Decimal(str(latest.scheduled)))}, "
                f"wirksamer Soll {temperature_text(Decimal(str(latest.effective)))}."
            )
        elif latest.effective is not None:
            parts.append(
                f"Zuletzt wirksamer Soll {temperature_text(Decimal(str(latest.effective)))} "
                "(Zeitplan-Soll unbekannt)."
            )
        if covered_seconds > 0:
            parts.append(
                f"Heizanforderung {heat_seconds / covered_seconds * 100:.0f} % der erfassten Zeit."
            )
        solar_hours = sum((b - a).total_seconds() for a, b, *_ in solar_bands) / 3600
        known_seconds = covered_seconds - unknown_seconds
        if solar_hours > 0:
            parts.append(f"Sonnenabsenkung {_duration_text(solar_hours)}.")
        elif known_seconds > 0 and unknown_seconds > 0:
            # "Keine" gilt nur für die Zeilen, die die Absenkung aufgezeichnet haben.
            parts.append(
                f"In den aufgezeichneten {_duration_text(known_seconds / 3600)} "
                "keine Sonnenabsenkung."
            )
        elif known_seconds > 0:
            parts.append("Keine Sonnenabsenkung.")
        if unknown_seconds > 0:
            parts.append(
                f"Absenkung für {_duration_text(unknown_seconds / 3600)} "
                "unbekannt (vor der Aufzeichnung)."
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
    if measured:
        measured_at, measured_value = measured[-1]
        local_at = local_time(measured_at, timezone_name)
        same_day = local_at.date() == local_time(end, timezone_name).date()
        stamp = f"{local_at:%H:%M}" if same_day else f"{local_at:%d.%m. %H:%M}"
        actual_text = (
            f"{temperature_text(Decimal(str(measured_value)))} (letzte Messung {stamp} Uhr)"
        )
    else:
        actual_text = "keine Messung"
    ticks = _round_ticks(start, end, timezone_name, _tick_step(end - start))
    y_step = 1 if maximum - minimum <= 8 else 2
    y_ticks = tuple((float(v), f"{v} °C") for v in range(minimum, maximum + 1, y_step))
    return Chart(
        has_data=bool(samples),
        points=points,
        heat_lane=heat_lane,
        solar_bands=solar_bands,
        unknown_bands=unknown_bands,
        effective_runs=effective_runs,
        scheduled_runs=scheduled_runs,
        start=start,
        end=end,
        data_end=data_end,
        bucket_seconds=width,
        minimum=float(minimum),
        maximum=float(maximum),
        summary=summary,
        actual_text=actual_text,
        scheduled_text=_setpoint_text(latest.scheduled if latest else None),
        effective_text=_setpoint_text(latest.effective if latest else None),
        bucket_text=_minutes_text(width),
        ticks=ticks,
        sparse_ticks=ticks[::2],
        y_ticks=y_ticks,
        stale=stale,
    )


def _setpoint_text(value: float | None) -> str:
    return temperature_text(Decimal(str(value))) if value is not None else "unbekannt"


def _points(samples: list[Sample], buckets: dict[int, list[int]]) -> tuple[Point, ...]:
    """Mittelwert, kleinster und größter Wert je Abschnitt zusammenhängender Messzeilen.

    Ein Bucket kann zwei Abschnitte enthalten, wenn mitten darin Daten fehlten: sie werden
    nicht zu einem Wert zusammengezogen, und die Kurve verbindet nur Abschnitte, deren
    Zeilen höchstens GAP auseinanderliegen.
    """
    points: list[Point] = []
    for _, indices in sorted(buckets.items()):
        sections: list[list[Sample]] = []
        for index in indices:
            sample = samples[index]
            if sample.temperature is None:
                continue
            if sections and sample.at - sections[-1][-1].at <= GAP:
                sections[-1].append(sample)
            else:
                sections.append([sample])
        for section in sections:
            temperatures = [s.temperature for s in section if s.temperature is not None]
            first, last = section[0].at, section[-1].at
            joined = bool(points) and first - points[-1].last <= GAP
            points.append(
                Point(
                    at=first + (last - first) / 2,
                    first=first,
                    last=last,
                    temperature=sum(temperatures) / len(temperatures),
                    low=min(temperatures),
                    high=max(temperatures),
                    joined=joined,
                )
            )
    return tuple(points)


@dataclass
class _Cell:
    first: datetime
    last: datetime
    cycles: int = 0
    heating: int = 0


def _heat_lane(
    samples: list[Sample], ends: list[datetime], buckets: dict[int, list[int]]
) -> tuple[HeatCell, ...]:
    """Heizspur: Zeitspannen der Zeilen, gestuft nach dem Anteil der Zyklen mit Anforderung.

    Eine Zelle reicht von der ersten Zeile bis zum Ende der letzten Zeile eines
    zusammenhängenden Stücks innerhalb eines Buckets -- nicht über den ganzen Bucket.
    """
    cells: list[_Cell] = []
    for _, indices in sorted(buckets.items()):
        current: _Cell | None = None
        for index in indices:
            sample = samples[index]
            if ends[index] <= sample.at:
                continue
            if current is not None and current.last == sample.at:
                current.last = ends[index]
            else:
                current = _Cell(sample.at, ends[index])
                cells.append(current)
            current.cycles += 1
            current.heating += 1 if sample.heat else 0
    lane: list[HeatCell] = []
    for cell in cells:
        if cell.heating == 0:
            continue
        level = ceil(cell.heating / cell.cycles * 4)
        if lane and lane[-1][1] == cell.first and lane[-1][2] == level:
            lane[-1] = (lane[-1][0], cell.last, level)
        else:
            lane.append((cell.first, cell.last, level))
    return tuple(lane)


def chart_for_zone(
    session: Session, zone_id: int, period: str, now: datetime, timezone_name: str | None
) -> Chart:
    """Lesefunktion der Domäne; die Session kommt vom aufrufenden Adapter."""
    duration = WINDOWS[period]
    start = now - duration
    return build_chart(read_samples(session, zone_id, start, now), start, now, timezone_name)


def _x_exact(at: datetime, chart: Chart, layout: Layout) -> float:
    fraction = (at - chart.start).total_seconds() / (chart.end - chart.start).total_seconds()
    return layout.left + (layout.right - layout.left) * fraction


def x_position(at: datetime, chart: Chart, layout: Layout) -> float:
    return round(_x_exact(at, chart, layout), 1)


def _span(first: datetime, last: datetime, chart: Chart, layout: Layout) -> tuple[float, float]:
    """Lage und Breite einer Zeitspanne, ungerundet genug, dass kurze Spannen nicht wachsen."""
    x = _x_exact(first, chart, layout)
    return round(x, 2), round(_x_exact(last, chart, layout) - x, 2)


def y_position(value: float, chart: Chart, layout: Layout) -> float:
    fraction = (value - chart.minimum) / (chart.maximum - chart.minimum)
    return round(layout.bottom - (layout.bottom - layout.top) * fraction, 1)


def tick_x(fraction: float, layout: Layout) -> float:
    return round(layout.left + (layout.right - layout.left) * fraction, 1)


def ticks_for(chart: Chart, layout: Layout) -> tuple[tuple[float, str], ...]:
    return chart.sparse_ticks if layout.sparse_ticks else chart.ticks


def line_path(chart: Chart, layout: Layout) -> str:
    """Istkurve (Mittelwert je Abschnitt); bricht bei jeder Lücke über GAP ab."""
    segments: list[list[tuple[float, float]]] = []
    for point in chart.points:
        xy = (x_position(point.at, chart, layout), y_position(point.temperature, chart, layout))
        if point.joined and segments:
            segments[-1].append(xy)
        else:
            segments.append([xy])
    parts: list[str] = []
    for segment in segments:
        if len(segment) == 1:  # ein einzelner Wert: Punkt (runde Linienenden), ohne Zeitbreite
            segment.append(segment[0])
        parts.append(
            " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(segment))
        )
    return " ".join(parts)


def _sections(chart: Chart) -> list[list[Point]]:
    """Ketten lückenlos verbundener Abschnitte (höchstens GAP zwischen den Zeilen)."""
    chains: list[list[Point]] = []
    for point in chart.points:
        if point.joined and chains:
            chains[-1].append(point)
        else:
            chains.append([point])
    return chains


def range_path(chart: Chart, layout: Layout) -> str:
    """Kleinster bis größter Wert je Abschnitt als Fläche zwischen verbundenen Abschnitten.

    Ein einzelner Abschnitt ohne verbundenen Nachbarn hat keine Fläche; ihn zeichnet `range_bars`.
    """
    parts: list[str] = []
    for chain in _sections(chart):
        if len(chain) < 2 or all(point.low == point.high for point in chain):
            continue
        xs = [x_position(p.at, chart, layout) for p in chain]
        upper = [(x, y_position(p.high, chart, layout)) for x, p in zip(xs, chain, strict=True)]
        lower = [(x, y_position(p.low, chart, layout)) for x, p in zip(xs, chain, strict=True)]
        outline = upper + lower[::-1]
        parts.append(
            " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(outline))
            + " Z"
        )
    return " ".join(parts)


# Breite des Balkens für einen einzelnen Abschnitt, in Zeichnungseinheiten. Das ist Darstellung
# (ein Strich, der gerade sichtbar ist) und behauptet keine Zeit -- wie das Dreieck der Absenkung.
RANGE_BAR_WIDTH = 1.6


def range_bars(chart: Chart, layout: Layout) -> str:
    """Kleinster bis größter Wert eines Abschnitts ohne verbundenen Nachbarn, als schmaler Balken.

    Ohne diesen Balken verschwände ein kurzer Ausschlag, wenn das ganze Fenster nur aus einem
    verdichteten Abschnitt oder aus voneinander getrennten Abschnitten besteht.
    """
    parts: list[str] = []
    for chain in _sections(chart):
        if len(chain) != 1 or chain[0].low == chain[0].high:
            continue
        point = chain[0]
        x = x_position(point.at, chart, layout)
        left, right = x - RANGE_BAR_WIDTH / 2, x + RANGE_BAR_WIDTH / 2
        top, bottom = y_position(point.high, chart, layout), y_position(point.low, chart, layout)
        parts.append(
            f"M{left:.1f},{top:.1f} L{right:.1f},{top:.1f} "
            f"L{right:.1f},{bottom:.1f} L{left:.1f},{bottom:.1f} Z"
        )
    return " ".join(parts)


def step_path(runs: tuple[Run, ...], chart: Chart, layout: Layout) -> str:
    """Sollwertstufen: waagerecht je Lauf, senkrecht nur beim Wechsel ohne Datenlücke.

    Ein Lauf aus einer einzigen Zeile ist ein Punkt; er wird nicht verbreitert.
    """
    parts: list[str] = []
    previous_end: datetime | None = None
    for first, last, value in runs:
        y = y_position(value, chart, layout)
        x2 = x_position(last, chart, layout)
        if previous_end == first:
            parts.append(f"V{y:.1f} H{x2:.1f}")
        else:
            parts.append(f"M{x_position(first, chart, layout):.1f},{y:.1f} H{x2:.1f}")
        previous_end = last
    return " ".join(parts)


# Ein Band, das schmaler ist, bekommt zusätzlich eine Markierung über der Zeichnung; ein Band,
# das schmaler als MIN_DRAWN ist, wird nur durch diese Markierung angezeigt. Beides ist
# Darstellung (Hinweis, dass hier etwas liegt) und behauptet keine Zeit.
MARK_BELOW = 3.0
MIN_DRAWN = 0.25


@dataclass(frozen=True)
class Band:
    x: float
    width: float
    label: str


def _fits(label: str, width: float, layout: Layout) -> bool:
    return width >= layout.solar_label_min_width * len(label) / 21


def solar_bands_for(chart: Chart, layout: Layout) -> tuple[Band, ...]:
    """Sonnenbänder dieser Zeichnung: exakt die Zeitspannen der Zeilen mit Absenkung."""
    bands: list[Band] = []
    for first, last, low, high in chart.solar_bands:
        x, width = _span(first, last, chart, layout)
        if width < MIN_DRAWN:
            continue
        label = f"Sonnenabsenkung {_k_text(low, high)}"
        bands.append(Band(x, width, label if _fits(label, width, layout) else ""))
    return tuple(bands)


def solar_marks_for(chart: Chart, layout: Layout) -> tuple[float, ...]:
    """Lagen der Markierungen für Bänder, die als Fläche zu schmal wären (je Bildpunkt eine)."""
    marks: set[int] = set()
    for first, last, _, _ in chart.solar_bands:
        x, width = _span(first, last, chart, layout)
        if width < MARK_BELOW:
            marks.add(round(x + width / 2))
    return tuple(float(x) for x in sorted(marks))


def has_short_bands(chart: Chart) -> bool:
    """Ob in irgendeiner Zeichnung ein Band nur als Markierung erscheint (für die Legende)."""
    coarsest = min(layout.right - layout.left for layout in LAYOUTS)
    limit = MARK_BELOW * (chart.end - chart.start).total_seconds() / coarsest
    return any((last - first).total_seconds() < limit for first, last, _, _ in chart.solar_bands)


def unknown_bands_for(chart: Chart, layout: Layout) -> tuple[Band, ...]:
    """Bereiche ohne Aussage über die Absenkung; sie bleiben sichtbar, auch wenn sie schmal sind."""
    label = "Absenkung unbekannt"
    bands: list[Band] = []
    for first, last in chart.unknown_bands:
        x, width = _span(first, last, chart, layout)
        bands.append(Band(x, width, label if _fits(label, width, layout) else ""))
    return tuple(bands)


def heat_cells_for(chart: Chart, layout: Layout) -> tuple[tuple[float, float, int], ...]:
    """Heizspur dieser Zeichnung als (x, Breite, Stufe); die Breite ist die der Zeilen."""
    return tuple(
        (*_span(first, last, chart, layout), level) for first, last, level in chart.heat_lane
    )
