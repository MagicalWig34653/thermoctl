import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  FOOT_H, HEAD_H, NODE_W, ROW_H, anchors, autoLayout, bounds, clampZoom, columnOf, curvePath, fitView,
  matchesSearch, nodeHeight, nodeRects, pathEnds, portCenter, resolvePlacement, toScreen, toWorld,
  visiblePorts, zoomAround,
} from '../src/lib/devices/geometry.ts';
import { sensor, valve, zone } from './devices-fixture.ts';

test('Portmitte: Ausgang rechte, Eingang linke Kante, Zeilenmitte', () => {
  const o = { x: 100, y: 40 };
  assert.deepEqual(portCenter(o, 'out', 0, false), { x: 100 + NODE_W, y: 40 + HEAD_H + ROW_H / 2 });
  assert.deepEqual(portCenter(o, 'in', 2, false), { x: 100, y: 40 + HEAD_H + 2 * ROW_H + ROW_H / 2 });
});

test('eingeklappt endet die Linie an der Kopfzeile auf der Seite des Anschlusses', () => {
  const o = { x: 0, y: 0 };
  assert.deepEqual(portCenter(o, 'in', 3, true), { x: 0, y: HEAD_H / 2 });
  assert.deepEqual(portCenter(o, 'out', 3, true), { x: NODE_W, y: HEAD_H / 2 });
  assert.equal(nodeHeight(5, true), HEAD_H);
  assert.equal(nodeHeight(5, false), HEAD_H + 5 * ROW_H + FOOT_H);
});

test('Linienende ist genau die Portmitte (Anfang und Ende des Pfads)', () => {
  const at = { 'device:1': { x: 10, y: 20 }, 'zone:7': { x: 400.5, y: 90 } };
  const a = anchors([sensor, zone], at, new Set(), false);
  const d = curvePath(a['d1.temp'], a['z7.source']);
  const ends = pathEnds(d);
  assert.ok(ends);
  assert.deepEqual(ends.start, a['d1.temp']);
  assert.deepEqual(ends.end, a['z7.source']);
});

test('technische Anschlüsse zählen nur mit Schalter; Zeilenindex folgt den sichtbaren', () => {
  assert.equal(visiblePorts(sensor, false).length, 2);
  assert.equal(visiblePorts(sensor, true).length, 3);
  const at = { 'device:1': { x: 0, y: 0 } };
  const off = anchors([sensor], at, new Set(), false);
  const on = anchors([sensor], at, new Set(), true);
  assert.equal(off['d1.battery'], undefined);
  assert.equal(off['d1.hum'].y, HEAD_H + ROW_H + ROW_H / 2);
  assert.equal(on['d1.battery'].y, HEAD_H + ROW_H + ROW_H / 2);
  assert.equal(on['d1.hum'].y, HEAD_H + 2 * ROW_H + ROW_H / 2);
});

test('Bildschirm-Welt-Umrechnung ist umkehrbar; Zoom hält den Fixpunkt', () => {
  const view = { x: 30, y: -12, k: 0.8 };
  const p = { x: 123, y: 45 };
  const back = toWorld(view, toScreen(view, p));
  assert.ok(Math.abs(back.x - p.x) < 1e-9 && Math.abs(back.y - p.y) < 1e-9);
  const anchor = { x: 200, y: 150 };
  const before = toWorld(view, anchor);
  const next = zoomAround(view, anchor, 1.5);
  const after = toWorld(next, anchor);
  assert.ok(Math.abs(before.x - after.x) < 1e-9 && Math.abs(before.y - after.y) < 1e-9);
  assert.equal(clampZoom(99), 2);
  assert.equal(zoomAround(view, anchor, 1000).k, 2);
});

test('Einpassen wartet auf echte Maße und zentriert', () => {
  const box = { x: 0, y: 0, w: 1000, h: 500 };
  assert.equal(fitView(box, { w: 0, h: 0 }), null);
  assert.equal(fitView(box, { w: 20, h: 800 }), null);
  const v = fitView(box, { w: 1048, h: 600 }, 24);
  assert.ok(v);
  assert.equal(v.k, 1);
  assert.equal(v.x, 24);
  assert.equal(v.y, 50);
  const small = fitView(box, { w: 424, h: 600 }, 24)!;
  assert.equal(small.k, 0.376);
  assert.equal(fitView(null, { w: 500, h: 500 })!.k, 1);
  assert.deepEqual(bounds([{ x: 1, y: 2, w: 3, h: 4 }, { x: 5, y: 0, w: 1, h: 1 }]), { x: 1, y: 0, w: 5, h: 6 });
  assert.equal(bounds([]), null);
});

test('automatisch anordnen: Fühler links, Zonen Mitte, Empfänger rechts; rein Darstellung', () => {
  const nodes = [sensor, zone, valve];
  const snapshot = JSON.stringify(nodes);
  const auto = autoLayout(nodes, new Set(), false);
  assert.equal(JSON.stringify(nodes), snapshot, 'Eingabe bleibt unverändert');
  assert.equal(columnOf(sensor, false), 'left');
  assert.equal(columnOf(zone, false), 'mid');
  assert.equal(columnOf(valve, false), 'right');
  assert.ok(auto['device:1'].x < auto['zone:7'].x && auto['zone:7'].x < auto['device:2'].x);
  assert.deepEqual(autoLayout(nodes, new Set(), false), auto, 'deterministisch');
  // Keine Überlappung gleicher Spalte und lange Spalten brechen um.
  const many = Array.from({ length: 12 }, (_, i) => ({ ...sensor, id: `device:${100 + i}` }));
  const layout = autoLayout(many, new Set(), false);
  assert.ok(new Set(Object.values(layout).map((p) => p.x)).size > 1);
});

test('gemerkte Positionen haben Vorrang, fehlende kommen aus der Anordnung', () => {
  const auto = { a: { x: 0, y: 0 }, b: { x: 5, y: 5 } };
  assert.deepEqual(resolvePlacement(auto, { a: { x: 9, y: 9 }, ghost: { x: 1, y: 1 } }), { a: { x: 9, y: 9 }, b: { x: 5, y: 5 } });
});

test('Knotenrechtecke berücksichtigen Einklappen', () => {
  const at = { 'device:1': { x: 0, y: 0 } };
  const open = nodeRects([sensor], at, new Set(), false)[0];
  const shut = nodeRects([sensor], at, new Set(['device:1']), false)[0];
  assert.ok(shut.h < open.h);
});

test('Suche trifft Knotenname und Anschlussbeschriftung', () => {
  assert.ok(matchesSearch(sensor, 'wohnzimmer'));
  assert.ok(matchesSearch(sensor, 'D1.HUM'));
  assert.ok(!matchesSearch(sensor, 'keller'));
  assert.ok(matchesSearch(sensor, '  '));
});

test('Startansicht bleibt lesbar: schmaler Container beginnt links oben statt winzig zu zentrieren', async () => {
  const { readableView } = await import('../src/lib/devices/geometry.ts');
  const box = { x: -100, y: 20, w: 1100, h: 900 };
  const wide = readableView(box, { w: 1400, h: 1000 })!;
  assert.deepEqual(wide, fitView(box, { w: 1400, h: 1000 }, 16));
  const narrow = readableView(box, { w: 342, h: 680 })!;
  assert.equal(narrow.k, 0.55);
  assert.equal(narrow.x, 16 + 100 * 0.55);
  assert.equal(narrow.y, 16 - 20 * 0.55);
  assert.equal(readableView(box, { w: 0, h: 0 }), null);
});
