import assert from 'node:assert/strict';
import { test } from 'node:test';
import { requestOf } from '../src/lib/schedule/client.ts';
import { describeIntent, ghostOf, isNoop, selectionAfter } from '../src/lib/schedule/intents.ts';
import { pointIntentOf, rangeIntentOf } from '../src/lib/schedule/newtime.ts';

test('Schaltpunkt: Tag, Uhrzeit und Modus werden zur Absicht point', () => {
  assert.deepEqual(pointIntentOf(3, '06:30', 2), { intent: { type: 'point', weekday: 3, minute: 390, modeId: 2 } });
  assert.deepEqual(pointIntentOf(1, '00:00', 1), { intent: { type: 'point', weekday: 1, minute: 0, modeId: 1 } });
});

test('Schaltpunkt: ungültige Uhrzeit und fehlender Modus nennen das Feld', () => {
  assert.deepEqual(pointIntentOf(3, '', 2), { error: 'Bitte eine gültige Uhrzeit eingeben (zum Beispiel 06:30).', field: 'time' });
  assert.deepEqual(pointIntentOf(3, '24:00', 2), { error: 'Bitte eine gültige Uhrzeit eingeben (zum Beispiel 06:30).', field: 'time' });
  assert.deepEqual(pointIntentOf(3, '06:30', null), { error: 'Bitte einen Modus wählen.', field: 'mode' });
});

test('Zeitraum: wird zur selben Absicht wie die Pinselgeste', () => {
  assert.deepEqual(rangeIntentOf(2, '06:30', '08:00', false, 1), {
    intent: { type: 'paint', weekday: 2, start: 390, end: 480, modeId: 1 },
  });
  assert.deepEqual(rangeIntentOf(2, '22:00', '', true, 1), {
    intent: { type: 'paint', weekday: 2, start: 1320, end: 1440, modeId: 1 },
  });
});

test('Zeitraum: Fehler nennen das Feld, Ende 00:00 verweist auf Tagesende', () => {
  assert.equal((rangeIntentOf(2, '', '08:00', false, 1) as { field: string }).field, 'start');
  assert.equal((rangeIntentOf(2, '06:00', 'x', false, 1) as { field: string }).field, 'end');
  const zero = rangeIntentOf(2, '06:00', '00:00', false, 1) as { field: string; error: string };
  assert.equal(zero.field, 'end');
  assert.match(zero.error, /Bis Tagesende/);
  assert.equal((rangeIntentOf(2, '08:00', '08:00', false, 1) as { field: string }).field, 'end');
  assert.deepEqual(rangeIntentOf(2, '24:00', '', true, 1), { error: 'Bitte einen gültigen Beginn eingeben (zum Beispiel 06:30).', field: 'start' });
  assert.equal((rangeIntentOf(2, '06:00', '08:00', false, null) as { field: string }).field, 'mode');
});

test('point: Anfrage an den Punkte-Endpunkt mit Rücknahme-Kette, Beschreibung, keine Vorschau', () => {
  const intent = { type: 'point', weekday: 3, minute: 390, modeId: 2 } as const;
  assert.deepEqual(requestOf(intent, 'v1', 'tok'), {
    method: 'POST',
    path: 'points',
    body: { version: 'v1', weekday: 3, minute_of_day: 390, mode_id: 2, previous_undo_token: 'tok' },
  });
  assert.equal(describeIntent(intent), 'Schaltpunkt angelegt: Mittwoch 06:30');
  assert.equal(ghostOf(intent), null);
  assert.equal(selectionAfter(intent), null);
  assert.equal(isNoop(intent), false);
});
