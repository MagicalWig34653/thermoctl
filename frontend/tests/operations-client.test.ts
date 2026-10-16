import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApi } from '../src/lib/api.ts';
import { createOperationsClient } from '../src/lib/operations/client.ts';

function harness(status = 200, body: unknown = {}) {
  const calls: { url: string; method: string; body: unknown; csrf: string | undefined }[] = [];
  const fetchFn = (async (url: string, init: RequestInit) => {
    calls.push({
      url,
      method: init.method ?? 'GET',
      body: init.body === undefined ? undefined : JSON.parse(String(init.body)),
      csrf: (init.headers as Record<string, string>)['X-CSRF-Token'],
    });
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  const api = createApi({ fetch: fetchFn, prefix: '/p', cookies: () => 'thermoctl_csrf=tok' });
  return { client: createOperationsClient(api), calls };
}

test('Teil-Update geht als PUT genau einmal mit CSRF-Header und nur den gesendeten Feldern', async () => {
  const { client, calls } = harness();
  await client.saveParameters(4, { hysteresis_k: null, valve_protection_enabled: true });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {
    url: '/p/ui-api/rooms/4/parameters',
    method: 'PUT',
    body: { hysteresis_k: null, valve_protection_enabled: true },
    csrf: 'tok',
  });
});

test('Freigabe: PUT /operation/armed mit Wunsch und Begründung', async () => {
  const { client, calls } = harness();
  await client.setArmed(true, 'Probelauf bestanden');
  assert.equal(calls[0].url, '/p/ui-api/operation/armed');
  assert.deepEqual(calls[0].body, { armed: true, reason: 'Probelauf bestanden' });
});

test('Schreibaufruf wird bei Serverfehler nicht wiederholt', async () => {
  const { client, calls } = harness(500, { detail: 'kaputt' });
  await assert.rejects(client.saveSensorFailure({ fixed_on_seconds: '1', fixed_off_seconds: '1', recovery_seconds: '1', recovery_samples: '1', warm_restart_hysteresis_k: '0.5', emergency_setpoint_c: '18', curve_points: [] }));
  assert.equal(calls.length, 1);
});

test('Fehlerkörper {detail, code, field} bleibt am Fehler erhalten', async () => {
  const { client } = harness(422, { detail: 'Zu groß.', code: 'validation', field: 'timezone' });
  await assert.rejects(client.saveDefaults({}, 'x'), (e: { code: string; field: string; message: string }) => e.code === 'validation' && e.field === 'timezone' && e.message === 'Zu groß.');
});

test('Testversand ist ein POST ohne Körper; Endpunkte stimmen mit dem Vertrag', async () => {
  const { client, calls } = harness();
  await client.testNotification();
  await client.saveOutdoorSource(null);
  await client.saveSolar({ enabled: true, latitude: '52.5', longitude: null });
  assert.deepEqual(
    calls.map((c) => [c.method, c.url, c.body]),
    [
      ['POST', '/p/ui-api/settings/notifications/test', undefined],
      ['PUT', '/p/ui-api/settings/outdoor-source', { device_id: null }],
      ['PUT', '/p/ui-api/settings/solar-location', { enabled: true, latitude: '52.5', longitude: null }],
    ],
  );
});
