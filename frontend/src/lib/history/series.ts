// Reihenlogik des Verlaufs (ohne Svelte, mit node:test geprüft).
//
// Grundsatz: Es wird nichts erfunden. Ein Fach ohne Zeile (`rows === 0`) ist eine Lücke; die
// Linie reißt dort ab, es wird weder interpoliert noch geglättet. Zahlen zum Zeichnen entstehen
// hier per `Number()` aus den Decimal-Strings des Servers; zur ANZEIGE dienen immer die
// Original-Strings (`raw`, `formatCelsius`), nie ein umgerechneter Float.

import type { Bucket, Gap, History } from './types';

export interface Point {
  /** Mitte des Fachs (ms) -- hier sitzen Marker und Linienpunkte. */
  t: number;
  start: number;
  end: number;
  rows: number;
  measurements: number;
  temp: number | null;
  tempMin: number | null;
  tempMax: number | null;
  schedMin: number | null;
  schedMax: number | null;
  effMin: number | null;
  effMax: number | null;
  heat: number | null;
  /** > 0: Sonnenabsenkung wirkte; 0 oder `null`: keine bzw. unbekannt. */
  solar: number | null;
  raw: Bucket;
}

export interface Span {
  start: number;
  end: number;
}

const num = (v: string | null): number | null => {
  if (v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export function parseTime(iso: string): number {
  return new Date(iso).getTime();
}

export function buildPoints(history: Pick<History, 'buckets' | 'resolution_seconds'>): Point[] {
  const width = history.resolution_seconds * 1000;
  return history.buckets.map((b) => {
    const start = parseTime(b.start);
    const empty = b.rows === 0;
    return {
      t: start + width / 2,
      start,
      end: start + width,
      rows: b.rows,
      measurements: b.measurements,
      temp: empty ? null : num(b.temperature_c),
      tempMin: empty ? null : num(b.temperature_min_c),
      tempMax: empty ? null : num(b.temperature_max_c),
      schedMin: empty ? null : num(b.scheduled_min_c),
      schedMax: empty ? null : num(b.scheduled_max_c),
      effMin: empty ? null : num(b.effective_min_c),
      effMax: empty ? null : num(b.effective_max_c),
      heat: empty ? null : b.heat_percent,
      solar: empty ? null : num(b.solar_setback_k),
      raw: b,
    };
  });
}

export function gapSpans(gaps: readonly Gap[]): Span[] {
  return gaps
    .map((g) => ({ start: parseTime(g.start), end: parseTime(g.end) }))
    .filter((g) => Number.isFinite(g.start) && Number.isFinite(g.end) && g.end > g.start);
}

/** Lücken auf den sichtbaren Ausschnitt zugeschnitten; was ganz außerhalb liegt, entfällt. */
export function clipSpans(spans: readonly Span[], view: Span): Span[] {
  return spans
    .filter((g) => g.end > view.start && g.start < view.end)
    .map((g) => ({ start: Math.max(g.start, view.start), end: Math.min(g.end, view.end) }));
}

/** Liegt zwischen den Fachmitten `a` und `b` eine vom Server gemeldete Lücke? */
function gapBetween(a: Point, b: Point, gaps: readonly Span[]): boolean {
  return gaps.some((g) => g.start < b.t && g.end > a.t);
}

/**
 * Zusammenhängende Läufe von Punktindizes, in denen `has` gilt. Ein Lauf endet an jedem Punkt
 * ohne Wert, an jedem Fach ohne Zeile dazwischen und an jeder Lücke, die der Server zwischen
 * zwei Fachmitten meldet -- auch wenn beide Fächer Zeilen haben.
 */
export function runs(
  points: readonly Point[],
  has: (p: Point) => boolean,
  gaps: readonly Span[] = [],
): number[][] {
  const out: number[][] = [];
  let current: number[] = [];
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    if (!has(p)) {
      if (current.length) out.push(current);
      current = [];
      continue;
    }
    const prev = current.length ? points[current[current.length - 1]] : null;
    if (prev && gapBetween(prev, p, gaps)) {
      out.push(current);
      current = [];
    }
    current.push(i);
  }
  if (current.length) out.push(current);
  return out;
}

/** Index des Punkts, dessen Fachmitte `t` am nächsten liegt (-1 bei leerer Reihe). */
export function nearestIndex(points: readonly Point[], t: number): number {
  if (!points.length) return -1;
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && t - points[lo - 1].t <= points[lo].t - t) return lo - 1;
  return lo;
}

/** Erster und letzter Index (einschließlich) der Fächer, die den Ausschnitt berühren; `null` ohne. */
export function viewRange(points: readonly Point[], view: Span): [number, number] | null {
  let lo = -1;
  let hi = -1;
  for (let i = 0; i < points.length; i++) {
    if (points[i].end > view.start && points[i].start < view.end) {
      if (lo < 0) lo = i;
      hi = i;
    }
  }
  return lo < 0 ? null : [lo, hi];
}

/** Letzter Punkt im Ausschnitt mit einem Messwert (sonst mit irgendeiner Zeile, sonst -1). */
export function lastIndexInView(points: readonly Point[], view: Span): number {
  const r = viewRange(points, view);
  if (!r) return -1;
  for (let i = r[1]; i >= r[0]; i--) if (points[i].temp !== null) return i;
  for (let i = r[1]; i >= r[0]; i--) if (points[i].rows > 0) return i;
  return -1;
}

/** Nächster Index in Richtung `step` (±1), begrenzt auf den Ausschnitt; Pos1/Ende über `edge`. */
export function moveIndex(
  points: readonly Point[],
  view: Span,
  current: number,
  action: 'left' | 'right' | 'first' | 'last' | 'far-left' | 'far-right',
): number {
  const r = viewRange(points, view);
  if (!r) return -1;
  const [lo, hi] = r;
  const at = current < lo || current > hi ? hi : current;
  switch (action) {
    case 'first':
      return lo;
    case 'last':
      return hi;
    case 'left':
      return Math.max(lo, at - 1);
    case 'right':
      return Math.min(hi, at + 1);
    case 'far-left':
      return Math.max(lo, at - 10);
    case 'far-right':
      return Math.min(hi, at + 10);
  }
}

/** Alle Werte, die die Temperaturachse enthalten muss (Messwerte, Band, Sollwerte) im Ausschnitt. */
export function temperatureValues(points: readonly Point[], view: Span, flags: SeriesFlags): number[] {
  const r = viewRange(points, view);
  if (!r) return [];
  const out: number[] = [];
  for (let i = r[0]; i <= r[1]; i++) {
    const p = points[i];
    const push = (v: number | null) => {
      if (v !== null) out.push(v);
    };
    if (flags.actual) push(p.temp);
    if (flags.range) {
      push(p.tempMin);
      push(p.tempMax);
    }
    if (flags.scheduled) {
      push(p.schedMin);
      push(p.schedMax);
    }
    if (flags.effective) {
      push(p.effMin);
      push(p.effMax);
    }
  }
  return out;
}

export interface SeriesFlags {
  actual: boolean;
  range: boolean;
  scheduled: boolean;
  effective: boolean;
  heating: boolean;
  solar: boolean;
}

export const DEFAULT_FLAGS: SeriesFlags = {
  actual: true,
  range: true,
  scheduled: true,
  effective: true,
  heating: true,
  solar: true,
};

export const SERIES: readonly { key: keyof SeriesFlags; label: string; description: string }[] = [
  { key: 'actual', label: 'Messwert', description: 'Mittel der gemessenen Raumtemperatur je Zeitabschnitt.' },
  { key: 'range', label: 'Min./Max.', description: 'Kleinster und größter Messwert je Zeitabschnitt.' },
  { key: 'scheduled', label: 'Geplant', description: 'Sollwert aus dem Wochenplan.' },
  { key: 'effective', label: 'Wirksam', description: 'Sollwert, der tatsächlich galt (nach Übersteuerung, Urlaub, Sonnenabsenkung).' },
  { key: 'heating', label: 'Heizanforderung', description: 'Anteil der Regelentscheidungen mit Heizanforderung.' },
  { key: 'solar', label: 'Sonnenabsenkung', description: 'Zeiten, in denen der Sollwert wegen Sonneneinstrahlung gesenkt wurde.' },
];

/** Hat die Reihe im Ausschnitt überhaupt eine Messung? (Für den Leerzustand.) */
export function hasAnyMeasurement(points: readonly Point[]): boolean {
  return points.some((p) => p.temp !== null);
}
