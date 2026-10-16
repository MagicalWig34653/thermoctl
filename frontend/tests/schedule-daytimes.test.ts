import assert from 'node:assert/strict';
import { test } from 'node:test';
import { requestOf } from '../src/lib/schedule/client';
import { dayTimesIntent, dayView, planIsEmpty, SUGGESTED_MINUTES } from '../src/lib/schedule/daytimes';
import { describeIntent, isNoop } from '../src/lib/schedule/intents';

const modes = [
  { id: 1, code: 'tag', name: 'Tag', temperature_c: '21.0', warmth: 0.7 },
  { id: 2, code: 'nacht', name: 'Nacht', temperature_c: '17.0', warmth: 0.3 },
];
const empty = { points: [], modes, empty_day_modes: [{ id: 1, name: 'Tag' }, { id: 2, name: 'Nacht' }] };

test('leerer Tag: zwei Felder mit den vom Server gewählten Modi und vorgeschlagenen Zeiten', () => {
  assert.deepEqual(dayView(empty, 3), {
    kind: 'empty',
    fields: [
      { label: 'Tag ab', minute: 390 },
      { label: 'Nacht ab', minute: 1320 },
    ],
  });
  assert.deepEqual(SUGGESTED_MINUTES, [390, 1320]);
  assert.equal(planIsEmpty(empty), true);
});

test('leerer Tag ohne zwei Temperaturen ist nicht einrichtbar', () => {
  assert.deepEqual(dayView({ ...empty, empty_day_modes: null }, 3), { kind: 'unavailable' });
  assert.deepEqual(dayView({ ...empty, empty_day_modes: [{ id: 1, name: 'Tag' }] }, 3), { kind: 'unavailable' });
});

test('zwei Punkte: Felder in Zeitreihenfolge, benannt nach dem Modus des Punktes', () => {
  const plan = {
    ...empty,
    points: [
      { id: 9, weekday: 2, minute_of_day: 1320, mode_id: 2 },
      { id: 8, weekday: 2, minute_of_day: 0, mode_id: 1 },
      { id: 7, weekday: 1, minute_of_day: 600, mode_id: 1 },
    ],
  };
  assert.deepEqual(dayView(plan, 2), {
    kind: 'two',
    fields: [
      { label: 'Tag ab', minute: 0 },
      { label: 'Nacht ab', minute: 1320 },
    ],
  });
  assert.equal(planIsEmpty(plan), false);
});

test('ein oder drei Punkte: nicht vereinfacht bearbeitbar, Zahl wird genannt', () => {
  const point = (id: number, minute: number) => ({ id, weekday: 4, minute_of_day: minute, mode_id: 1 });
  assert.deepEqual(dayView({ ...empty, points: [point(1, 10)] }, 4), { kind: 'other', count: 1 });
  assert.deepEqual(dayView({ ...empty, points: [point(1, 10), point(2, 20), point(3, 30)] }, 4), {
    kind: 'other',
    count: 3,
  });
});

test('Zeitfelder werden zur Absicht; Mitternacht ist gültig; Unsinn wird abgewiesen', () => {
  assert.deepEqual(dayTimesIntent(2, '06:30', '22:00'), { intent: { type: 'dayTimes', weekday: 2, first: 390, second: 1320 } });
  assert.deepEqual(dayTimesIntent(2, '00:00', '23:59'), { intent: { type: 'dayTimes', weekday: 2, first: 0, second: 1439 } });
  for (const bad of ['', '24:00', '6', '12:60', 'ab:cd']) {
    assert.deepEqual(dayTimesIntent(2, bad, '22:00'), { error: 'Bitte gültige Uhrzeiten eingeben.' }, bad);
    assert.deepEqual(dayTimesIntent(2, '06:30', bad), { error: 'Bitte gültige Uhrzeiten eingeben.' }, bad);
  }
});

test('die Absicht geht an day-times, mit Version und Rücknahme-Kette', () => {
  const intent = { type: 'dayTimes', weekday: 2, first: 0, second: 1320 } as const;
  assert.deepEqual(requestOf(intent, '41', 'tok'), {
    method: 'POST',
    path: 'day-times',
    body: { version: '41', weekday: 2, first_minute: 0, second_minute: 1320, previous_undo_token: 'tok' },
  });
  assert.equal(isNoop(intent), false);
  assert.equal(describeIntent(intent), 'Tageszeiten gesetzt: Dienstag 00:00 und 22:00');
});
