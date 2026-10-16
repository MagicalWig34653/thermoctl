import assert from 'node:assert/strict';
import { test } from 'node:test';
import { entryOf } from '../src/lib/dashboard/catalog.ts';
import {
  addSection,
  addTile,
  canAddSection,
  canAddTile,
  canRedo,
  canUndo,
  commit,
  createHistory,
  fromDraft,
  isDirty,
  makeTile,
  moveSection,
  moveSectionBefore,
  moveSectionBy,
  moveTile,
  moveTileBefore,
  moveTileBy,
  positionOf,
  redo,
  removeSection,
  removeTile,
  renameSection,
  toDraft,
  toPayload,
  undo,
  updateTile,
  validate,
  type Sections,
} from '../src/lib/dashboard/draft.ts';
import type { Layout, Section } from '../src/lib/dashboard/types.ts';

const tile = (zone: number) => makeTile(entryOf('room'), zone);
const layout = (sections: Section[]): Layout => ({ version: 3, customized: true, sections });
const base = (): Sections =>
  toDraft([
    { title: 'A', tiles: [fromDraft([{ key: 'x', title: '', tiles: [tile(1)] }])[0].tiles[0], fromDraft([{ key: 'x', title: '', tiles: [tile(2)] }])[0].tiles[0]] },
    { title: 'B', tiles: [fromDraft([{ key: 'x', title: '', tiles: [tile(3)] }])[0].tiles[0]] },
  ]);
const zones = (s: Sections) => s.map((x) => x.tiles.map((t) => t.zone_id));

test('toDraft vergibt Schlüssel, fromDraft/toPayload senden sie nie', () => {
  const s = base();
  assert.ok(s[0].key && s[0].tiles[0].key);
  const payload = toPayload(3, s);
  assert.equal(payload.version, 3);
  assert.deepEqual(Object.keys(payload.sections[0]).sort(), ['tiles', 'title']);
  assert.ok(!('key' in payload.sections[0].tiles[0]));
  assert.equal(payload.sections[0].tiles[0].zone_id, 1);
});

test('toPayload streift Leerraum von Titeln wie der Server', () => {
  const named = toDraft([{ title: '  Räume  ', tiles: [] }]);
  assert.equal(toPayload(0, named).sections[0].title, 'Räume');
});

test('Kacheln umsortieren innerhalb und zwischen Abschnitten', () => {
  const s = base();
  const [a, b] = s;
  const swapped = moveTile(s, a.tiles[0].key, a.key, 1);
  assert.deepEqual(zones(swapped), [[2, 1], [3]]);
  const across = moveTile(s, a.tiles[0].key, b.key, 0);
  assert.deepEqual(zones(across), [[2], [1, 3]]);
  assert.equal(moveTile(s, a.tiles[0].key, a.key, 0), s, 'gleiche Stelle: unverändert');
  assert.equal(moveTile(s, 'unbekannt', a.key, 0), s);
  assert.equal(moveTile(s, a.tiles[0].key, 'unbekannt', 0), s);
});

test('moveTileBefore: vor eine Kachel oder ans Ende', () => {
  const s = base();
  const [a, b] = s;
  assert.deepEqual(zones(moveTileBefore(s, b.tiles[0].key, a.key, a.tiles[0].key)), [[3, 1, 2], []]);
  assert.deepEqual(zones(moveTileBefore(s, a.tiles[0].key, a.key, null)), [[2, 1], [3]]);
  assert.equal(moveTileBefore(s, a.tiles[0].key, a.key, a.tiles[0].key), s);
  assert.equal(moveTileBefore(s, a.tiles[0].key, a.key, 'weg'), s);
});

test('moveTileBy wandert über Abschnittsgrenzen, nicht über den Rand', () => {
  const s = base();
  const [a, b] = s;
  const last = a.tiles[1].key;
  assert.deepEqual(zones(moveTileBy(s, last, 1)), [[1], [2, 3]]);
  assert.deepEqual(zones(moveTileBy(s, b.tiles[0].key, -1)), [[1, 2, 3], []]);
  assert.equal(moveTileBy(s, a.tiles[0].key, -1), s, 'ganz vorn');
  assert.equal(moveTileBy(s, b.tiles[0].key, 1), s, 'ganz hinten');
});

test('Abschnitte umsortieren', () => {
  const s = addSection(base(), 'C');
  const [a, b, c] = s;
  assert.deepEqual(moveSection(s, c.key, 0).map((x) => x.title), ['C', 'A', 'B']);
  assert.deepEqual(moveSectionBy(s, a.key, 1).map((x) => x.title), ['B', 'A', 'C']);
  assert.equal(moveSectionBy(s, a.key, -1), s);
  assert.deepEqual(moveSectionBefore(s, a.key, null).map((x) => x.title), ['B', 'C', 'A']);
  assert.deepEqual(moveSectionBefore(s, c.key, b.key).map((x) => x.title), ['A', 'C', 'B']);
  assert.equal(moveSectionBefore(s, a.key, a.key), s);
});

test('Schranke: höchstens 12 Abschnitte', () => {
  let s: Sections = [];
  for (let i = 0; i < 12; i++) s = addSection(s, `S${i}`);
  assert.equal(s.length, 12);
  assert.equal(canAddSection(s), false);
  assert.equal(addSection(s, 'zu viel'), s);
});

test('Schranke: höchstens 30 Kacheln je Abschnitt, auch beim Verschieben', () => {
  let s = addSection(addSection([], 'A'), 'B');
  const [a, b] = s;
  for (let i = 0; i < 30; i++) s = addTile(s, a.key, tile(i));
  assert.equal(s[0].tiles.length, 30);
  assert.equal(canAddTile(s, a.key), false);
  assert.equal(addTile(s, a.key, tile(99)), s);
  const extra = tile(100);
  s = addTile(s, b.key, extra);
  assert.equal(moveTile(s, extra.key, a.key, 0), s, 'volles Ziel nimmt nichts auf');
  assert.equal(moveTileBy(s, s[0].tiles[29].key, 1)[0].tiles.length, 29, 'in den leeren Nachbarn geht es');
});

test('Titel werden bei 80 Zeichen, Notizen bei 500 abgeschnitten', () => {
  const s0 = addTile(addSection([], 'A'), 'x', tile(1)); // falscher Schlüssel: unverändert
  assert.equal(s0.length, 1);
  const sec = addSection([], 'A');
  const t = makeTile(entryOf('note'), null);
  const s = addTile(sec, sec[0].key, t);
  assert.equal(updateTile(s, t.key, { title: 'x'.repeat(200) })[0].tiles[0].title.length, 80);
  assert.equal(updateTile(s, t.key, { note: 'y'.repeat(900) })[0].tiles[0].note.length, 500);
  assert.equal(renameSection(s, sec[0].key, 'z'.repeat(200))[0].title.length, 80);
  assert.equal(addSection([], 'q'.repeat(200))[0].title.length, 80);
});

test('updateTile: unverändert liefert dieselbe Liste; Zeitraum bleibt zur Art passend', () => {
  const sec = addSection([], 'A');
  const t = makeTile(entryOf('chart'), 1);
  const s = addTile(sec, sec[0].key, t);
  assert.equal(updateTile(s, t.key, { width: 8 }), s, 'Breite war schon 8');
  assert.equal(updateTile(s, t.key, { period_days: 90 })[0].tiles[0].period_days, 30, 'Verlauf kennt kein 90');
  const h = addTile(sec, sec[0].key, makeTile(entryOf('heating'), 1));
  assert.equal(h[0].tiles[0].period_days, 7);
  assert.equal(updateTile(h, h[0].tiles[0].key, { period_days: 1 })[0].tiles[0].period_days, 7, 'Heizzeit kennt kein 1');
});

test('Entfernen einer Raumkachel verändert nur den Entwurf', () => {
  const s = base();
  const next = removeTile(s, s[0].tiles[0].key);
  assert.deepEqual(zones(next), [[2], [3]]);
  assert.equal(removeTile(s, 'weg'), s);
  assert.deepEqual(removeSection(s, s[1].key).map((x) => x.title), ['A']);
  assert.equal(removeSection(s, 'weg'), s);
});

test('Makeln: Standardwerte je Art', () => {
  assert.equal(makeTile(entryOf('metric'), null).metric, 'rooms');
  assert.equal(makeTile(entryOf('shortcut'), null).destination, 'schedule');
  assert.equal(makeTile(entryOf('note'), 5).zone_id, null, 'ohne Raumbezug kein zone_id');
  assert.equal(makeTile(entryOf('room'), 5).zone_id, 5);
  assert.equal(makeTile(entryOf('chart'), 5).width, 8);
});

test('positionOf nennt Abschnitt und Stelle', () => {
  const s = base();
  assert.deepEqual(positionOf(s, s[0].tiles[1].key), { sectionTitle: 'A', sectionIndex: 0, sectionCount: 2, index: 1, count: 2 });
  assert.equal(positionOf(s, 'weg'), null);
});

test('Undo/Redo: Schritte, Redo verfällt bei neuer Änderung', () => {
  const s0 = base();
  let h = createHistory(s0);
  assert.equal(canUndo(h), false);
  const s1 = moveSectionBy(s0, s0[0].key, 1);
  h = commit(h, s1);
  const s2 = addSection(s1, 'C');
  h = commit(h, s2);
  assert.equal(h.present, s2);
  h = undo(h);
  assert.equal(h.present, s1);
  assert.equal(canRedo(h), true);
  h = undo(h);
  assert.equal(h.present, s0);
  assert.equal(undo(h), h, 'am Anfang');
  h = redo(h);
  assert.equal(h.present, s1);
  h = commit(h, removeSection(s1, s1[0].key));
  assert.equal(canRedo(h), false);
  assert.equal(redo(h), h);
});

test('Undo: dieselbe Liste ist kein Schritt; Eingaben im selben Feld verschmelzen', () => {
  const s0 = base();
  let h = createHistory(s0);
  assert.equal(commit(h, s0), h);
  const k = s0[0].key;
  h = commit(h, renameSection(s0, k, 'N'), `${k}:title`);
  h = commit(h, renameSection(h.present, k, 'Ne'), `${k}:title`);
  h = commit(h, renameSection(h.present, k, 'Neu'), `${k}:title`);
  assert.equal(h.past.length, 1, 'drei Tastenanschläge = ein Schritt');
  h = undo(h);
  assert.equal(h.present, s0);
  h = redo(h);
  h = commit(h, removeSection(h.present, k)); // anderer Eingriff beendet die Verschmelzung
  h = commit(h, addSection(h.present, 'Z'), 'anderes');
  assert.equal(h.past.length, 3);
});

test('Undo-Verlauf ist begrenzt', () => {
  let h = createHistory(addSection([], 'A'));
  for (let i = 0; i < 150; i++) h = commit(h, addSection(removeSection(h.present, h.present[0]?.key ?? ''), `T${i}`));
  assert.equal(h.past.length, 100);
});

test('isDirty vergleicht Inhalt, nicht Schlüssel', () => {
  const server = layout([{ title: 'A', tiles: [fromDraft([{ key: 'k', title: '', tiles: [tile(1)] }])[0].tiles[0]] }]);
  const draft = toDraft(server.sections);
  assert.equal(isDirty(server, draft), false);
  assert.equal(isDirty(server, renameSection(draft, draft[0].key, 'B')), true);
  assert.equal(isDirty(null, []), false);
});

test('validate meldet, was der Server ablehnen würde, mit Bezug auf die Stelle', () => {
  const sec = addSection([], '  ');
  assert.deepEqual(validate(sec).map((p) => p.field), ['title']);
  const ok = renameSection(sec, sec[0].key, 'A');
  assert.deepEqual(validate(ok), []);
  const room = { ...tile(1), zone_id: null };
  const withRoom = addTile(ok, ok[0].key, room);
  assert.equal(validate(withRoom)[0].field, 'zone_id');
  assert.equal(validate(withRoom)[0].key, room.key);
  const metric = { ...makeTile(entryOf('metric'), null), metric: null };
  assert.equal(validate(addTile(ok, ok[0].key, metric))[0].field, 'metric');
  const short = { ...makeTile(entryOf('shortcut'), null), destination: null };
  assert.equal(validate(addTile(ok, ok[0].key, short))[0].field, 'destination');
  const note = { ...makeTile(entryOf('note'), null), note: 'x'.repeat(501), title: 'y'.repeat(81) };
  assert.deepEqual(validate(addTile(ok, ok[0].key, note)).map((p) => p.field).sort(), ['note', 'title']);
  const many = Array.from({ length: 13 }, (_, i) => ({ key: `s${i}`, title: 't', tiles: [] }));
  assert.equal(validate(many)[0].field, 'sections');
  const full = [{ key: 's', title: 't', tiles: Array.from({ length: 31 }, (_, i) => ({ ...tile(i), key: `t${i}` })) }];
  assert.equal(validate(full)[0].field, 'tiles');
  const long = [{ key: 's', title: 'x'.repeat(81), tiles: [] }];
  assert.match(validate(long)[0].message, /80/);
});
