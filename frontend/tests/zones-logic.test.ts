import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  createBody,
  filterGlossary,
  groupByLetter,
  impactLines,
  impactTotal,
  noteRemaining,
  patchOf,
  termOf,
  validateRoomValues,
  valuesOf,
} from '../src/lib/zones/logic.ts';
import type { DeleteImpact, GlossaryEntry, RoomForm, RoomValues } from '../src/lib/zones/types.ts';

const limits = { name_limit: 64, display_name_limit: 128 };
const room: RoomForm = {
  id: 4,
  name: 'bad',
  display_name: 'Bad',
  sort_order: 2,
  operating_mode: 'auto',
  temperature_source_device_id: null,
};
const good: RoomValues = { name: 'kueche', display_name: 'Küche', operating_mode: 'auto', sort_order: '3' };

test('valuesOf: Anlegen startet mit der ersten Betriebsart und Sortierung 0, Bearbeiten mit den Werten', () => {
  assert.deepEqual(valuesOf(null, 'auto'), { name: '', display_name: '', operating_mode: 'auto', sort_order: '0' });
  assert.deepEqual(valuesOf(room, 'off'), { name: 'bad', display_name: 'Bad', operating_mode: 'auto', sort_order: '2' });
});

test('validateRoomValues: gültige Eingabe ergibt kein Problem', () => {
  assert.equal(validateRoomValues(good, limits), null);
  assert.equal(validateRoomValues({ ...good, sort_order: ' -5 ' }, limits), null);
});

test('validateRoomValues: nennt das erste fehlerhafte Feld in Formularreihenfolge', () => {
  const field = (change: Partial<RoomValues>) => validateRoomValues({ ...good, ...change }, limits)?.field;
  assert.equal(field({ name: '  ' }), 'name');
  assert.equal(field({ name: 'x'.repeat(65) }), 'name');
  assert.equal(field({ display_name: '' }), 'display_name');
  assert.equal(field({ display_name: 'x'.repeat(129) }), 'display_name');
  assert.equal(field({ operating_mode: '' }), 'operating_mode');
  assert.equal(field({ sort_order: '1,5' }), 'sort_order');
  assert.equal(field({ sort_order: '' }), 'sort_order');
  assert.equal(field({ sort_order: String(2 ** 31) }), 'sort_order');
  assert.equal(field({ name: '', display_name: '' }), 'name');
});

test('validateRoomValues: Grenzen sind erlaubt', () => {
  assert.equal(validateRoomValues({ ...good, name: 'x'.repeat(64) }, limits), null);
  assert.equal(validateRoomValues({ ...good, sort_order: String(2 ** 31 - 1) }, limits), null);
});

test('createBody: schneidet Leerraum ab und macht die Sortierung zur Zahl', () => {
  assert.deepEqual(createBody({ name: ' flur ', display_name: ' Flur ', operating_mode: 'off', sort_order: ' 7 ' }), {
    name: 'flur',
    display_name: 'Flur',
    operating_mode: 'off',
    sort_order: 7,
  });
});

test('patchOf: nur geänderte Felder; unverändert ergibt ein leeres Objekt', () => {
  assert.deepEqual(patchOf({ name: 'bad', display_name: 'Bad', operating_mode: 'auto', sort_order: '2' }, room), {});
  assert.deepEqual(patchOf({ name: 'bad', display_name: ' Badezimmer ', operating_mode: 'auto', sort_order: '2' }, room), {
    display_name: 'Badezimmer',
  });
  assert.deepEqual(patchOf({ name: 'wc', display_name: 'WC', operating_mode: 'off', sort_order: '9' }, room), {
    name: 'wc',
    display_name: 'WC',
    operating_mode: 'off',
    sort_order: 9,
  });
});

const impact: DeleteImpact = {
  id: 1,
  name: 'bad',
  display_name: 'Bad',
  schedule_points: 12,
  devices: 0,
  setpoints: 3,
  overrides: 0,
  shadow_decisions: 400,
};

test('impactLines: nur Posten mit Inhalt, impactTotal zählt sie zusammen', () => {
  assert.deepEqual(
    impactLines(impact).map((l) => [l.count, l.label]),
    [
      [12, 'Schaltpunkte im Wochenplan'],
      [3, 'Temperaturen je Modus'],
      [400, 'Aufgezeichnete Regelentscheidungen'],
    ],
  );
  assert.equal(impactTotal(impact), 415);
  const empty = { ...impact, schedule_points: 0, setpoints: 0, shadow_decisions: 0 };
  assert.deepEqual(impactLines(empty), []);
  assert.equal(impactTotal(empty), 0);
});

test('noteRemaining: zählt Zeichen, nicht Bytes, und wird nie negativ', () => {
  assert.equal(noteRemaining('', 200), 200);
  assert.equal(noteRemaining('äöü😀', 200), 196);
  assert.equal(noteRemaining('x'.repeat(250), 200), 0);
});

const entry = (id: string, term: string, area: string, extra: Partial<GlossaryEntry> = {}): GlossaryEntry => ({
  id,
  term,
  short: `Kurz ${term}`,
  explanation: `Erklärung zu ${term}`,
  area,
  letter: term[0].toUpperCase(),
  synonyms: [],
  see_also: [],
  ...extra,
});
const entries = [
  entry('hysterese', 'Hysterese', 'Regelung', { synonyms: ['Schaltabstand'], explanation: 'Der Abstand zwischen Ein und Aus.' }),
  entry('sollwert', 'Sollwert', 'Regelung', { see_also: ['hysterese'] }),
  entry('sensor', 'Sensor', 'Sensorik'),
  entry('uebersteuerung', 'Übersteuerung', 'Betrieb', { letter: 'U' }),
];

test('filterGlossary: leere Suche liefert alles, Bereich schränkt ein', () => {
  assert.equal(filterGlossary(entries, '', null).length, 4);
  assert.deepEqual(filterGlossary(entries, '  ', 'Regelung').map((e) => e.id), ['hysterese', 'sollwert']);
});

test('filterGlossary: findet in Begriff, Synonym, Kurzfassung und Erklärung ohne Beachtung der Schreibweise', () => {
  assert.deepEqual(filterGlossary(entries, 'HYSTERESE', null).map((e) => e.id), ['hysterese']);
  assert.deepEqual(filterGlossary(entries, 'schaltabstand', null).map((e) => e.id), ['hysterese']);
  assert.deepEqual(filterGlossary(entries, 'kurz sensor', null).map((e) => e.id), ['sensor']);
  assert.deepEqual(filterGlossary(entries, 'zwischen ein', null).map((e) => e.id), ['hysterese']);
  assert.deepEqual(filterGlossary(entries, 'übersteuerung', null).map((e) => e.id), ['uebersteuerung']);
});

test('filterGlossary: alle Suchwörter müssen treffen; Suche und Bereich verbinden sich', () => {
  assert.deepEqual(filterGlossary(entries, 'sensor hysterese', null), []);
  assert.deepEqual(filterGlossary(entries, 'erklärung', 'Sensorik').map((e) => e.id), ['sensor']);
  assert.deepEqual(filterGlossary(entries, 'sensor', 'Regelung'), []);
});

test('groupByLetter: bündelt benachbarte Einträge je Anfangsbuchstabe', () => {
  const groups = groupByLetter([
    entry('a', 'Anlage', 'Betrieb'),
    entry('b', 'Absenkung', 'Betrieb'),
    entry('c', 'Sensor', 'Sensorik'),
  ]);
  assert.deepEqual(groups.map((g) => [g.letter, g.entries.length]), [['A', 2], ['S', 1]]);
  assert.deepEqual(groupByLetter([]), []);
});

test('termOf: Begriff zur Kennung, sonst die Kennung selbst', () => {
  assert.equal(termOf(entries, 'sensor'), 'Sensor');
  assert.equal(termOf(entries, 'unbekannt'), 'unbekannt');
});
