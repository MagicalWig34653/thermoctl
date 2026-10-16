import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildPoints,
  clipSpans,
  gapSpans,
  lastIndexInView,
  moveIndex,
  nearestIndex,
  runs,
  temperatureValues,
  viewRange,
  DEFAULT_FLAGS,
} from '../src/lib/history/series.ts';
import type { Bucket } from '../src/lib/history/types.ts';

const T0 = Date.UTC(2026, 9, 1, 0, 0, 0);
const bucket = (i: number, over: Partial<Bucket> = {}): Bucket => ({
  start: new Date(T0 + i * 3600_000).toISOString(),
  rows: 60,
  measurements: 60,
  temperature_c: '21.40',
  temperature_min_c: '21.10',
  temperature_max_c: '21.70',
  scheduled_min_c: '20.50',
  scheduled_max_c: '20.50',
  effective_min_c: '20.50',
  effective_max_c: '21.00',
  heat_percent: 40,
  solar_setback_k: '0',
  ...over,
});
const empty = (i: number): Bucket =>
  bucket(i, {
    rows: 0,
    measurements: 0,
    temperature_c: null,
    temperature_min_c: null,
    temperature_max_c: null,
    scheduled_min_c: null,
    scheduled_max_c: null,
    effective_min_c: null,
    effective_max_c: null,
    heat_percent: null,
    solar_setback_k: null,
  });
const build = (buckets: Bucket[]) => buildPoints({ buckets, resolution_seconds: 3600 });

test('buildPoints: Fachmitte, Zahlen zum Zeichnen, Original-String bleibt in raw', () => {
  const [p] = build([bucket(0)]);
  assert.equal(p.t, T0 + 1800_000);
  assert.equal(p.temp, 21.4);
  assert.equal(p.raw.temperature_c, '21.40');
  assert.equal(p.heat, 40);
});

test('buildPoints: Fach mit rows=0 ist Lücke, nichts wird gefüllt', () => {
  const [p] = build([empty(0)]);
  assert.equal(p.temp, null);
  assert.equal(p.tempMin, null);
  assert.equal(p.effMax, null);
  assert.equal(p.heat, null);
  assert.equal(p.solar, null);
});

test('buildPoints: rows>0 aber ohne Messwert bleibt ohne Temperatur (kein Nullwert)', () => {
  const [p] = build([bucket(0, { measurements: 0, temperature_c: null, temperature_min_c: null, temperature_max_c: null })]);
  assert.equal(p.temp, null);
  assert.equal(p.rows, 60);
});

test('runs: Fach ohne Zeile trennt die Linie, kein Verbinden über die Lücke', () => {
  const pts = build([bucket(0), bucket(1), empty(2), bucket(3), empty(4), bucket(5)]);
  assert.deepEqual(runs(pts, (p) => p.temp !== null), [[0, 1], [3], [5]]);
});

test('runs: vom Server gemeldete Lücke zwischen zwei belegten Fächern trennt ebenfalls', () => {
  const pts = build([bucket(0), bucket(1), bucket(2)]);
  const gaps = gapSpans([{ start: new Date(T0 + 1.6 * 3600_000).toISOString(), end: new Date(T0 + 1.9 * 3600_000).toISOString() }]);
  assert.deepEqual(runs(pts, (p) => p.temp !== null, gaps), [[0, 1], [2]]); // Lücke liegt zwischen Mitte 1 und Mitte 2
});

test('runs: Lücke außerhalb der Mitten trennt nicht', () => {
  const pts = build([bucket(0), bucket(1)]);
  const gaps = gapSpans([{ start: new Date(T0 + 2 * 3600_000).toISOString(), end: new Date(T0 + 3 * 3600_000).toISOString() }]);
  assert.deepEqual(runs(pts, (p) => p.temp !== null, gaps), [[0, 1]]);
});

test('gapSpans/clipSpans: ungültige Spannen entfallen, Ausschnitt schneidet zu', () => {
  const spans = gapSpans([
    { start: 'kaputt', end: 'kaputt' },
    { start: new Date(T0).toISOString(), end: new Date(T0 + 10_000).toISOString() },
    { start: new Date(T0 + 100).toISOString(), end: new Date(T0 + 100).toISOString() },
  ]);
  assert.equal(spans.length, 1);
  assert.deepEqual(clipSpans(spans, { start: T0 + 5000, end: T0 + 99_999 }), [{ start: T0 + 5000, end: T0 + 10_000 }]);
  assert.deepEqual(clipSpans(spans, { start: T0 + 20_000, end: T0 + 30_000 }), []);
});

test('nearestIndex: nächstgelegene Fachmitte, Ränder und leere Reihe', () => {
  const pts = build([bucket(0), bucket(1), bucket(2)]);
  assert.equal(nearestIndex(pts, T0), 0);
  assert.equal(nearestIndex(pts, pts[1].t + 100), 1);
  assert.equal(nearestIndex(pts, pts[1].t + 2000_000), 2);
  assert.equal(nearestIndex(pts, T0 + 99 * 3600_000), 2);
  assert.equal(nearestIndex([], T0), -1);
});

test('lastIndexInView: letzter Messwert im Ausschnitt, nicht der der Gesamtreihe', () => {
  const pts = build([bucket(0), bucket(1), bucket(2), empty(3)]);
  assert.equal(lastIndexInView(pts, { start: T0, end: T0 + 4 * 3600_000 }), 2); // Lücke am Ende zählt nicht
  assert.equal(lastIndexInView(pts, { start: T0, end: T0 + 1.5 * 3600_000 }), 1);
  assert.equal(lastIndexInView(pts, { start: T0 + 3 * 3600_000, end: T0 + 4 * 3600_000 }), -1);
});

test('lastIndexInView: ohne Messwert fällt es auf ein Fach mit Zeilen zurück', () => {
  const pts = build([bucket(0, { temperature_c: null, measurements: 0 })]);
  assert.equal(lastIndexInView(pts, { start: T0, end: T0 + 3600_000 }), 0);
});

test('viewRange und moveIndex: Pfeiltasten bleiben im Ausschnitt, Pos1/Ende springen', () => {
  const pts = build(Array.from({ length: 30 }, (_, i) => bucket(i)));
  const view = { start: T0 + 5 * 3600_000, end: T0 + 15 * 3600_000 };
  assert.deepEqual(viewRange(pts, view), [5, 14]);
  assert.equal(moveIndex(pts, view, 5, 'left'), 5);
  assert.equal(moveIndex(pts, view, 14, 'right'), 14);
  assert.equal(moveIndex(pts, view, 8, 'right'), 9);
  assert.equal(moveIndex(pts, view, 8, 'first'), 5);
  assert.equal(moveIndex(pts, view, 8, 'last'), 14);
  assert.equal(moveIndex(pts, view, 8, 'far-right'), 14);
  assert.equal(moveIndex(pts, view, 0, 'left'), 13); // Index außerhalb: startet am Ende des Ausschnitts
  assert.equal(moveIndex(pts, { start: T0 + 99 * 3600_000, end: T0 + 100 * 3600_000 }, 0, 'right'), -1);
});

test('temperatureValues: nur eingeschaltete Reihen bestimmen die Achse', () => {
  const pts = build([bucket(0), bucket(1, { effective_max_c: '25.00' })]);
  const view = { start: T0, end: T0 + 2 * 3600_000 };
  assert.ok(temperatureValues(pts, view, DEFAULT_FLAGS).includes(25));
  assert.ok(!temperatureValues(pts, view, { ...DEFAULT_FLAGS, effective: false }).includes(25));
  assert.deepEqual(temperatureValues(pts, view, { ...DEFAULT_FLAGS, actual: false, range: false, scheduled: false, effective: false }), []);
});
