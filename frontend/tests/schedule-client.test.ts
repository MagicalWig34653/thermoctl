import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApi } from '../src/lib/api.ts';
import { createScheduleClient, requestOf, undoTokensOf } from '../src/lib/schedule/client.ts';
import type { Intent } from '../src/lib/schedule/intents.ts';

const ref = { weekday: 2, start: 420, end: 540 };

test('Jede Absicht wird zu genau einem Aufruf (hier steht der angenommene Block-Vertrag)', () => {
  const cases: [Intent, string, object][] = [
    [
      { type: 'paint', weekday: 2, start: 420, end: 540, modeId: 7 },
      'paint',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, mode_id: 7 },
    ],
    [
      { type: 'move', block: ref, toWeekday: 3, toStart: 600 },
      'block/move',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, to_weekday: 3, to_start_minute: 600 },
    ],
    [
      { type: 'duplicate', block: ref, toWeekday: 3, toStart: 600 },
      'block/duplicate',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, to_weekday: 3, to_start_minute: 600 },
    ],
    [
      { type: 'resize', block: ref, newStart: 430, newEnd: 560, adjacent: true },
      'block/resize',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, new_start_minute: 430, new_end_minute: 560, adjacent: true },
    ],
    [
      { type: 'resize', block: ref, newStart: 430, newEnd: 560, adjacent: false },
      'block/resize',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540, new_start_minute: 430, new_end_minute: 560 },
    ],
    [
      { type: 'delete', block: ref },
      'block/delete',
      { version: 'v', weekday: 2, start_minute: 420, end_minute: 540 },
    ],
    [{ type: 'copyDay', weekday: 2, scope: 'workdays' }, 'copy-day', { version: 'v', weekday: 2, scope: 'workdays' }],
    [
      { type: 'copyWeek', targets: [{ zone_id: 4, version: '9' }] },
      'copy-week',
      { version: 'v', targets: [{ zone_id: 4, version: '9' }] },
    ],
  ];
  for (const [intent, path, body] of cases) {
    assert.deepEqual(requestOf(intent, 'v'), { path, body }, intent.type);
  }
});

test('Rücknahme-Marken: Liste oder einzelne Marke, sonst keine', () => {
  assert.deepEqual(undoTokensOf({ undo_token: 'abc' }), ['abc']);
  assert.deepEqual(undoTokensOf({ undo_tokens: ['a', 'b', 3] }), ['a', 'b']);
  assert.deepEqual(undoTokensOf({ undo_token: null }), []);
  assert.deepEqual(undoTokensOf({ undo_token: '' }), []);
  assert.deepEqual(undoTokensOf(null), []);
  assert.deepEqual(undoTokensOf('x'), []);
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

test('send/undo gehen mit Präfix und CSRF-Header ab und normalisieren die Marken', async () => {
  const { calls, client } = stub([
    { changed: true, undo_token: 'tok1', version: '5' },
    { changed: true, version: '6' },
  ]);
  const changed = await client.send(3, { type: 'delete', block: ref }, '4');
  assert.deepEqual(changed.undo_tokens, ['tok1']);
  const undone = await client.undo(3, 'tok1');
  assert.deepEqual(undone.undo_tokens, []);
  assert.deepEqual(
    calls.map((c) => [c.method, c.url, c.csrf]),
    [
      ['POST', '/ing/ui-api/schedule/3/block/delete', 'tok'],
      ['POST', '/ing/ui-api/schedule/3/undo', 'tok'],
    ],
  );
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
