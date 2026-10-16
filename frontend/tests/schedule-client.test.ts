import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApi } from '../src/lib/api.ts';
import { createScheduleClient, requestOf, undoTokenOf } from '../src/lib/schedule/client.ts';
import type { Intent } from '../src/lib/schedule/intents.ts';

const ref = { weekday: 2, start: 420, end: 540 };

test('Jede Absicht wird zu genau einem Aufruf (Vertrag des echten Servers)', () => {
  const cases: [Intent, string, string, object][] = [
    [
      { type: 'paint', weekday: 2, start: 420, end: 540, modeId: 7 },
      'POST',
      'paint',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, mode_id: 7 },
    ],
    [
      { type: 'move', block: ref, toWeekday: 3, toStart: 600 },
      'POST',
      'block/move',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, to_weekday: 3, to_start_minute: 600 },
    ],
    [
      { type: 'duplicate', block: ref, toWeekday: 3, toStart: 600 },
      'POST',
      'block/duplicate',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, to_weekday: 3, to_start_minute: 600 },
    ],
    [
      { type: 'resize', block: ref, newStart: 430, newEnd: 560, adjacent: true },
      'POST',
      'block/resize',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, new_start_minute: 430, new_end_minute: 560, adjacent: true },
    ],
    [
      { type: 'resize', block: { weekday: 7, start: 1380, end: 1800 }, newStart: 1380, newEnd: 1860, adjacent: false },
      'POST',
      'block/resize',
      { version: 'v', weekday: 7, start_minute: 1380, end_minute: 1800, new_start_minute: 1380, new_end_minute: 1860 },
    ],
    [
      { type: 'delete', block: ref },
      'POST',
      'block/delete',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540 },
    ],
    [
      { type: 'setMode', block: ref, pointId: 12, modeId: 3 },
      'PUT',
      'points/12/mode',
      { version: 'v', mode_id: 3 },
    ],
    [{ type: 'copyDay', weekday: 2, scope: 'workdays' }, 'POST', 'copy-day', { version: 'v', weekday: 2, scope: 'workdays' }],
    [
      { type: 'copyWeek', targets: [{ zone_id: 4, version: '9' }] },
      'POST',
      'copy-week',
      { version: 'v', targets: [{ zone_id: 4, version: '9' }] },
    ],
  ];
  for (const [intent, method, path, body] of cases) {
    assert.deepEqual(requestOf(intent, 'v'), { method, path, body }, intent.type);
  }
});

test('Die Kette: previous_undo_token geht in jede Schreibanfrage, außer copy-week', () => {
  const del = requestOf({ type: 'delete', block: ref }, 'v', 'tok');
  assert.deepEqual(del.body, { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, previous_undo_token: 'tok' });
  assert.equal((requestOf({ type: 'setMode', block: ref, pointId: 1, modeId: 2 }, 'v', 'tok').body as Record<string, unknown>).previous_undo_token, 'tok');
  assert.equal('previous_undo_token' in requestOf({ type: 'copyWeek', targets: [] }, 'v', 'tok').body, false);
  assert.equal('previous_undo_token' in requestOf({ type: 'delete', block: ref }, 'v', null).body, false);
});

test('Rücknahme-Marke: nur eine nichtleere Zeichenkette zählt', () => {
  assert.equal(undoTokenOf({ undo_token: 'abc' }), 'abc');
  assert.equal(undoTokenOf({ undo_token: null }), null);
  assert.equal(undoTokenOf({ undo_token: '' }), null);
  assert.equal(undoTokenOf({ undo_token: 3 }), null);
  assert.equal(undoTokenOf(null), null);
  assert.equal(undoTokenOf('x'), null);
});

function stub(responses: unknown[]) {
  const calls: { url: string; method: string; body: unknown; csrf: string | null }[] = [];
  const fetchStub: typeof fetch = async (url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({
      url: String(url),
      method: String(init?.method),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      csrf: headers['X-CSRF-Token'] ?? null,
    });
    return new Response(JSON.stringify(responses.shift()), { status: 200 });
  };
  const api = createApi({ fetch: fetchStub, prefix: '/ing', cookies: () => 'thermoctl_csrf=tok' });
  return { calls, client: createScheduleClient(api) };
}

test('send/undo gehen mit Präfix, CSRF-Header und Kette ab', async () => {
  const { calls, client } = stub([
    { changed: true, undo_token: 'tok1', version: '5' },
    { changed: true, undo_token: 'tok0', version: '6' },
    { changed: true, version: '7' },
    { changed: true, undo_token: 'm', version: '8' },
  ]);
  const changed = await client.send(3, { type: 'delete', block: ref }, '4', 'tok0');
  assert.equal(changed.undo_token, 'tok1');
  const undone = await client.undo(3, 'tok1');
  assert.equal(undone.undo_token, 'tok0'); // die Marke für den nächsten Schritt
  const end = await client.undo(3, 'tok0');
  assert.equal(end.undo_token, null); // Ende der Kette
  await client.send(3, { type: 'setMode', block: ref, pointId: 12, modeId: 2 }, '7', null);
  assert.deepEqual(
    calls.map((c) => [c.method, c.url, c.csrf]),
    [
      ['POST', '/ing/ui-api/schedule/3/block/delete', 'tok'],
      ['POST', '/ing/ui-api/schedule/3/undo', 'tok'],
      ['POST', '/ing/ui-api/schedule/3/undo', 'tok'],
      ['PUT', '/ing/ui-api/schedule/3/points/12/mode', 'tok'],
    ],
  );
  assert.deepEqual(calls[0].body, { version: '4', weekday: 2, start_minute: 420, end_minute: 540, previous_undo_token: 'tok0' });
  assert.deepEqual(calls[1].body, { undo_token: 'tok1' });
});

test('Lesen und Urlaub/Abwesenheit/Modi nutzen die vorhandenen Pfade', async () => {
  const { calls, client } = stub(Array.from({ length: 12 }, () => ({})));
  await client.plan(1);
  await client.preview(1);
  await client.vacation();
  await client.setVacation({ start_date: 'a', end_date: 'b', setback_temperature_c: '16' });
  await client.endVacation();
  await client.absence();
  await client.startAbsence({ temperature_c: '16', return_on: 'd' });
  await client.endAbsence();
  await client.modes();
  await client.addMode({ code: 'c', name: 'n', sort_order: 1 });
  await client.renameMode(2, { code: 'c', name: 'n', sort_order: 1 });
  await client.removeMode(2);
  assert.deepEqual(
    calls.map((c) => `${c.method} ${c.url.replace('/ing', '')}`),
    [
      'GET /ui-api/schedule/1',
      'GET /ui-api/schedule/1/preview',
      'GET /ui-api/vacation',
      'POST /ui-api/vacation',
      'DELETE /ui-api/vacation',
      'GET /ui-api/absence',
      'POST /ui-api/absence',
      'POST /ui-api/absence/end',
      'GET /ui-api/modes',
      'POST /ui-api/modes',
      'PUT /ui-api/modes/2',
      'DELETE /ui-api/modes/2',
    ],
  );
});
