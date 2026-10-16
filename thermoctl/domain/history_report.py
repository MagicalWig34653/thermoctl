"""Rohe Verlaufsreihe einer Zone für die Auswertung (Svelte-Oberfläche, CSV).

Anders als `zone_history_chart` (zeichnet, höchstens 7 Tage) und `zone_history_series`
(MCP, höchstens 7 Tage, nur belegte Fächer) liefert dies die Messreihe **ohne Darstellung**:
ein dichtes Raster gleich breiter Zeitfächer, in dem jedes Fach entweder Zahlen trägt oder
ausdrücklich leer ist.

Regeln (jede ist eine Zusicherung an den Aufrufer):

* **Obergrenzen.** Zeitraum höchstens `MAX_SPAN` (31 Tage), höchstens `MAX_BUCKETS` Fächer,
  höchstens `MAX_ROWS` gelesene Zeilen. Die Fachbreite ist `max(60 s, Zeitraum / MAX_BUCKETS)`
  aufgerundet auf ganze Sekunden; eine gewünschte Breite darunter wird auf diesen Wert
  angehoben, eine größere gilt. Wird `MAX_ROWS` überschritten, gibt es einen Fehler statt
  einer stillschweigend abgeschnittenen Reihe.
* **Lücken bleiben Lücken.** Ein Fach ohne Zeile hat `rows == 0` und lauter `None`. Nichts
  wird interpoliert, gefüllt oder geglättet. Zusätzlich nennt `gaps` jede Spanne zwischen zwei
  aufeinanderfolgenden Zeilen (oder Fensteranfang bzw. -ende und der nächsten Zeile), die
  länger als `GAP` ist -- auch wenn sie innerhalb eines breiten Fachs liegt. Die Zukunft
  (hinter `now`) ist keine Lücke.
* **Verdichtet wird nur, was beschriftet ist.** Messwert: Mittel, Minimum und Maximum der
  Zeilen des Fachs. Sollwerte: kleinster und größter Wert im Fach (kein Mittel eines
  Sprungs). Heizanforderung: Anteil der Zeilen mit `would_heat`. Sonnenabsenkung: größte
  Absenkung; `None` heißt unbekannt (Zeilen vor der Aufzeichnung), `0.0` belegt keine.

Keine Rechteprüfung hier: der Adapter prüft die Zone vorher.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil, floor

from sqlalchemy.orm import Session

from thermoctl.domain.zone_history_chart import GAP, Sample, read_samples
from thermoctl.domain.zone_history_series import _mean

MAX_SPAN = timedelta(days=31)
MAX_BUCKETS = 720
MIN_RESOLUTION_SECONDS = 60
MAX_ROWS = 400_000


class ReportRangeError(ValueError):
    """Zeitraum, Auflösung oder Datenmenge liegen außerhalb des Zulässigen."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Bucket:
    start: datetime  # UTC mit Zeitzone
    rows: int
    measurements: int  # Zeilen mit Temperaturwert
    temperature_mean_c: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    scheduled_min_c: float | None
    scheduled_max_c: float | None
    effective_min_c: float | None
    effective_max_c: float | None
    heat_percent: int | None
    solar_setback_k: float | None


@dataclass(frozen=True)
class Gap:
    start: datetime  # UTC mit Zeitzone
    end: datetime


@dataclass(frozen=True)
class HistoryReport:
    start: datetime
    end: datetime
    resolution_seconds: int
    rows: int
    buckets: tuple[Bucket, ...]
    gaps: tuple[Gap, ...]


def resolve_resolution(span: timedelta, requested: int | None) -> int:
    """Fachbreite in Sekunden: nie unter der Mindestbreite, nie mehr als `MAX_BUCKETS` Fächer."""
    minimum = max(MIN_RESOLUTION_SECONDS, ceil(span.total_seconds() / MAX_BUCKETS))
    return minimum if requested is None else max(requested, minimum)


def check_window(start: datetime, end: datetime) -> None:
    if start >= end:
        raise ReportRangeError("„from“ muss vor „to“ liegen.", "from")
    if end - start > MAX_SPAN:
        raise ReportRangeError("Der Zeitraum darf höchstens 31 Tage umfassen.", "to")


def history_report(
    session: Session,
    zone_id: int,
    start: datetime,
    end: datetime,
    *,
    requested_resolution: int | None = None,
    now: datetime,
) -> HistoryReport:
    """Reihe einer Zone in `[start, end]` (naive UTC)."""
    check_window(start, end)
    if requested_resolution is not None and requested_resolution < 1:
        raise ReportRangeError("„resolution“ muss mindestens 1 Sekunde betragen.", "resolution")
    width = resolve_resolution(end - start, requested_resolution)
    samples = read_samples(session, zone_id, start, end, limit=MAX_ROWS + 1)
    if len(samples) > MAX_ROWS:
        raise ReportRangeError(
            "Für diesen Zeitraum liegen zu viele Messzeilen vor; bitte einen kürzeren wählen.",
            "to",
        )
    count = max(1, ceil((end - start).total_seconds() / width))
    grouped: dict[int, list[Sample]] = {}
    for sample in samples:
        # Eine Zeile genau auf `end` gehört noch ins letzte Fach.
        index = min(floor((sample.at - start).total_seconds() / width), count - 1)
        grouped.setdefault(index, []).append(sample)
    buckets = tuple(
        _bucket(start + timedelta(seconds=index * width), grouped.get(index, []))
        for index in range(count)
    )
    return HistoryReport(
        start=_aware(start),
        end=_aware(end),
        resolution_seconds=width,
        rows=len(samples),
        buckets=buckets,
        gaps=_gaps(samples, start, min(end, now)),
    )


def _gaps(samples: list[Sample], start: datetime, limit: datetime) -> tuple[Gap, ...]:
    found: list[Gap] = []
    previous = start
    for sample in samples:
        if sample.at - previous > GAP:
            found.append(Gap(_aware(previous), _aware(sample.at)))
        previous = sample.at
    if limit - previous > GAP:
        found.append(Gap(_aware(previous), _aware(limit)))
    return tuple(found)


def _bucket(at: datetime, samples: list[Sample]) -> Bucket:
    temperatures = [s.temperature for s in samples if s.temperature is not None]
    scheduled = [s.scheduled for s in samples if s.scheduled is not None]
    effective = [s.effective for s in samples if s.effective is not None]
    setbacks = [s.solar_k for s in samples if s.solar_k is not None]
    return Bucket(
        start=_aware(at),
        rows=len(samples),
        measurements=len(temperatures),
        temperature_mean_c=_mean(temperatures, 2),
        temperature_min_c=min(temperatures) if temperatures else None,
        temperature_max_c=max(temperatures) if temperatures else None,
        scheduled_min_c=min(scheduled) if scheduled else None,
        scheduled_max_c=max(scheduled) if scheduled else None,
        effective_min_c=min(effective) if effective else None,
        effective_max_c=max(effective) if effective else None,
        heat_percent=(
            round(100 * sum(1 for s in samples if s.heat) / len(samples)) if samples else None
        ),
        solar_setback_k=max(setbacks) if setbacks else None,
    )


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)
