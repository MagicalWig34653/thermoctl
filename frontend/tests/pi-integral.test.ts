import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CATALOG, entryOf } from '../src/lib/dashboard/catalog.ts';
import { makeTile, updateTile } from '../src/lib/dashboard/draft.ts';
import { tileTitle } from '../src/lib/dashboard/tiles.ts';
import { TILE_KINDS } from '../src/lib/dashboard/types.ts';
import { buildPoints, DEFAULT_FLAGS, gapSpans, runs } from '../src/lib/history/series.ts';
import { seriesList, TECHNICAL, TENANT } from '../src/lib/history/wording.ts';
import type { Bucket } from '../src/lib/history/types.ts';
import { controllerRows, lastResetText, resetMarks } from '../src/lib/rooms/pi.ts';
import type { Room } from '../src/lib/rooms/types.ts';

const T0 = Date.UTC(2026, 10, 26, 0, 0, 0);
const bucket = (i: number, pi: string | null, rows = 60): Bucket => ({
  start: new Date(T0 + i * 3600_000).toISOString(),
  rows,
  measurements: rows,
  temperature_c: rows ? '21.4' : null,
  temperature_min_c: rows ? '21.1' : null,
  temperature_max_c: rows ? '21.7' : null,
  scheduled_min_c: null,
  scheduled_max_c: null,
  effective_min_c: null,
  effective_max_c: null,
  heat_percent: rows ? 40 : null,
  solar_setback_min_k: null,
  solar_setback_k: null,
  pi_integral: pi,
});
const build = (buckets: Bucket[]) => buildPoints({ buckets, resolution_seconds: 3600 });

test('I-Anteil: Prozentwerte 0 bis 100; ohne Wert eine Lücke, nie interpoliert', () => {
  const points = build([bucket(0, '0.25'), bucket(1, '0'), bucket(2, null), bucket(3, '1'), bucket(4, '0.5', 0)]);
  assert.deepEqual(points.map((p) => p.pi), [25, 0, null, 100, null]);
  assert.deepEqual(runs(points, (p) => p.pi !== null, []), [[0, 1], [3]]);
});

test('I-Anteil: eine gemeldete Messlücke trennt die Linie auch zwischen zwei Werten', () => {
  const points = build([bucket(0, '0.2'), bucket(1, '0.3')]);
  const gaps = gapSpans([{ start: new Date(T0 + 2400_000).toISOString(), end: new Date(T0 + 4800_000).toISOString() }]);
  assert.deepEqual(runs(points, (p) => p.pi !== null, gaps), [[0], [1]]);
});

test('I-Anteil: eigene Reihe, nur in der Anlagensicht angeboten', () => {
  assert.equal(DEFAULT_FLAGS.pi, true);
  assert.ok(!seriesList(TECHNICAL).some((s) => s.key === 'pi'), 'ohne Angebot des Servers keine Reihe');
  assert.deepEqual(seriesList(TECHNICAL, true).at(-1)?.label, 'I-Anteil');
  assert.ok(!seriesList(TENANT).some((s) => s.key === 'pi'));
});

test('Resets: Marker nur im Ausschnitt, mit Zeitpunkt und Grund', () => {
  const resets = [
    { at: new Date(T0 - 1000).toISOString(), reason: 'x', reason_label: 'davor' },
    { at: new Date(T0 + 3600_000).toISOString(), reason: 'keine_heizaktivitaet_24h', reason_label: 'Seit mehr als 24 Stunden gab es keine Wärmeanforderung.' },
    { at: new Date(T0 + 7200_000).toISOString(), reason: 'unbekannt_x', reason_label: null },
  ];
  assert.deepEqual(resetMarks(resets, { start: T0, end: T0 + 7200_000 }), [
    { t: T0 + 3600_000, reason: 'Seit mehr als 24 Stunden gab es keine Wärmeanforderung.' },
    { t: T0 + 7200_000, reason: 'unbekannt_x' },
  ]);
  assert.deepEqual(resetMarks(null, { start: T0, end: T0 + 1 }), []);
});

test('letzter Reset als Satz: Zeit in der Anlagenzone und Grund; ohne Reset ehrlich', () => {
  const now = new Date('2026-11-26T12:00:00Z');
  assert.equal(
    lastResetText({ at: '2026-11-26T05:30:00+00:00', reason: 'keine_heizaktivitaet_24h', reason_label: 'Seit mehr als 24 Stunden gab es keine Wärmeanforderung.' }, 'Europe/Berlin', now),
    '06:30 Uhr: Seit mehr als 24 Stunden gab es keine Wärmeanforderung.',
  );
  assert.equal(lastResetText({ at: '2026-11-26T05:30:00+00:00', reason: null, reason_label: null }, 'UTC', now), '05:30 Uhr: ohne Angabe eines Grunds');
  assert.equal(lastResetText(null, 'UTC', now), 'Keiner im aufbewahrten Schaltprotokoll.');
});

const room = (id: number, pi: Room['pi']) => ({ id, name: `Raum ${id}`, pi }) as Room;

test('Regler-Kachel: nur Räume mit PI, Balken 0 bis 100, ohne Wert kein Balken', () => {
  const rows = controllerRows([
    room(1, { integral: '0.425000', last_reset: null }),
    room(2, null),
    room(3, { integral: null, last_reset: null }),
  ]);
  assert.deepEqual(
    rows.map((r) => [r.id, r.percent, r.text, r.path]),
    [
      [1, 42.5, '42,5 %', '/raeume/1?ansicht=technik'],
      [3, null, '—', '/raeume/3?ansicht=technik'],
    ],
  );
});

test('Katalog: Regler-Kachel nur für die Anlagensicht, ohne eigenen Raum', () => {
  const entry = entryOf('controller');
  assert.equal(entry.name, 'Regler');
  assert.equal(entry.adminOnly, true);
  assert.equal(entry.needsZone, false);
  assert.ok(TILE_KINDS.includes('controller'));
  assert.equal(CATALOG.length, TILE_KINDS.length);
  assert.equal(tileTitle({ kind: 'controller', title: '' }), 'Regler');
});

test('Verlaufskachel: „I-Anteil zeigen“ ist anfangs aus und lässt sich einschalten', () => {
  const tile = makeTile(entryOf('chart'), 7);
  assert.equal(tile.show_pi, false);
  const next = updateTile([{ key: 's', title: 'S', tiles: [tile] }], tile.key, { show_pi: true });
  assert.equal(next[0].tiles[0].show_pi, true);
});
