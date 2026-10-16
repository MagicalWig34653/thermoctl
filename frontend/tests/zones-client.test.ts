import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createApi } from '../src/lib/api.ts';
import { createZonesClient } from '../src/lib/zones/client.ts';
import { toFailure } from '../src/lib/rooms/logic.ts';

type Call = { url: string; init: RequestInit };

function setup(status: number, body: unknown = {}) {
  const calls: Call[] = [];
  const fakeFetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const api = createApi({ fetch: fakeFetch, prefix: '/p', cookies: () => 'thermoctl_csrf=tok' });
  return { client: createZonesClient(api), calls };
}

const header = (call: Call, name: string) => (call.init.headers as Record<string, string>)[name];

test('Lesen: GET ohne CSRF-Header, Raumformular mit und ohne Raum', async () => {
  const { client, calls } = setup(200, {});
  await client.form();
  await client.form(7);
  await client.deleteImpact(7);
  await client.reportOptions();
  await client.glossary();
  assert.deepEqual(
    calls.map((c) => [c.init.method, c.url]),
    [
      ['GET', '/p/ui-api/room-form'],
      ['GET', '/p/ui-api/room-form?zone_id=7'],
      ['GET', '/p/ui-api/rooms/7/delete-impact'],
      ['GET', '/p/ui-api/report-options'],
      ['GET', '/p/ui-api/glossary'],
    ],
  );
  assert.ok(calls.every((c) => header(c, 'X-CSRF-Token') === undefined));
});

test('Schreiben: Methode, Pfad, JSON-Körper und CSRF-Header', async () => {
  const { client, calls } = setup(200, {});
  await client.create({ name: 'a', display_name: 'A', operating_mode: 'auto', sort_order: 0 });
  await client.save(3, { display_name: 'B' });
  await client.remove(3);
  await client.report(3, { kind: 'not_warm', note: 'kalt' });
  assert.deepEqual(
    calls.map((c) => [c.init.method, c.url]),
    [
      ['POST', '/p/ui-api/rooms'],
      ['PUT', '/p/ui-api/rooms/3'],
      ['DELETE', '/p/ui-api/rooms/3'],
      ['POST', '/p/ui-api/rooms/3/report'],
    ],
  );
  assert.ok(calls.every((c) => header(c, 'X-CSRF-Token') === 'tok'));
  assert.deepEqual(JSON.parse(calls[1].init.body as string), { display_name: 'B' });
  assert.deepEqual(JSON.parse(calls[3].init.body as string), { kind: 'not_warm', note: 'kalt' });
});

test('Schreiben wird bei Fehlern nie wiederholt', async () => {
  for (const status of [500, 409, 422]) {
    const { client, calls } = setup(status, { detail: 'x', code: 'c' });
    await assert.rejects(client.create({ name: 'a', display_name: 'A', operating_mode: 'auto', sort_order: 0 }));
    await assert.rejects(client.remove(1));
    await assert.rejects(client.report(1, { kind: 'other', note: '' }));
    assert.equal(calls.length, 3);
  }
});

test('Netzwerkfehler beim Schreiben: genau ein Versuch, Meldung nennt den unklaren Ausgang', async () => {
  let attempts = 0;
  const api = createApi({
    fetch: (async () => {
      attempts += 1;
      throw new TypeError('offline');
    }) as unknown as typeof fetch,
    prefix: '',
    cookies: () => 'thermoctl_csrf=tok',
  });
  const client = createZonesClient(api);
  const error = await client.remove(1).catch((e: unknown) => e);
  assert.ok(error instanceof ApiError);
  assert.equal(attempts, 1);
  const failure = toFailure(error);
  assert.equal(failure.kind, 'network');
  assert.match(failure.message, /unklar/);
});

test('Fehlerzuordnung: Namenskonflikt, Feldfehler, Berechtigung, fremder Raum, Sitzung', async () => {
  const cases: [number, Record<string, unknown>, string, string | null][] = [
    [409, { detail: 'Dieser Name ist bereits vergeben.', code: 'name_taken', field: 'name' }, 'conflict', 'name'],
    [422, { detail: 'Bitte eine ganze Zahl eingeben.', code: 'validation', field: 'sort_order' }, 'validation', 'sort_order'],
    [403, { detail: 'Dafür fehlt die Berechtigung.', code: 'forbidden' }, 'forbidden', null],
    [404, { detail: 'Raum nicht gefunden.', code: 'not_found' }, 'not_found', null],
    [401, { detail: 'Nicht angemeldet.' }, 'session', null],
  ];
  for (const [status, body, kind, field] of cases) {
    const { client } = setup(status, body);
    const error = await client.save(1, { name: 'x' }).catch((e: unknown) => e);
    const failure = toFailure(error);
    assert.equal(failure.kind, kind, String(status));
    assert.equal(failure.field, field, String(status));
  }
});
