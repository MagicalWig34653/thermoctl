import assert from 'node:assert/strict';
import { test } from 'node:test';
import { KIOSK_CSRF_COOKIE, KioskError, createKioskClient, kioskErrorKind } from '../src/lib/kiosk/client.ts';

interface Call {
  url: string;
  init: RequestInit;
}

function harness(responses: (Response | Error)[], cookies = `${KIOSK_CSRF_COOKIE}=abc123; other=1`) {
  const calls: Call[] = [];
  const queue = [...responses];
  const client = createKioskClient({
    prefix: '/ing',
    cookies: () => cookies,
    fetch: (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const next = queue.shift();
      if (next === undefined) throw new Error('unerwarteter Aufruf');
      if (next instanceof Error) throw next;
      return next;
    }) as unknown as typeof fetch,
  });
  return { client, calls };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const INVALID = { detail: 'Dieser Kiosk-Zugang ist nicht mehr gültig.', code: 'kiosk_token_invalid' };

test('Lesen: Präfix, kein Umleiten, kein Zwischenspeicher, kein CSRF-Header', async () => {
  const { client, calls } = harness([json(200, { now: 'x', timezone: 'UTC', rooms: [] })]);
  await client.state();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/ing/kiosk-api/state');
  assert.equal(calls[0].init.method, 'GET');
  assert.equal(calls[0].init.redirect, 'error');
  assert.equal(calls[0].init.cache, 'no-store');
  assert.equal(calls[0].init.credentials, 'same-origin');
  assert.equal((calls[0].init.headers as Record<string, string>)['X-CSRF-Token'], undefined);
});

test('Schreiben: CSRF aus dem Kiosk-Cookie im Header, JSON-Körper', async () => {
  const { client, calls } = harness([json(200, { id: 3 })]);
  await client.step(3, 'up');
  assert.equal(calls[0].url, '/ing/kiosk-api/rooms/3/setpoint-step');
  assert.equal(calls[0].init.method, 'POST');
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers['X-CSRF-Token'], 'abc123');
  assert.equal(headers['Content-Type'], 'application/json');
  assert.equal(calls[0].init.body, JSON.stringify({ direction: 'up' }));
});

test('Das Sitzungs-Cookie zählt nicht: nur thermoctl_kiosk_csrf wird gelesen', async () => {
  const { client, calls } = harness([], 'thermoctl_csrf=sitzung');
  await assert.rejects(client.boost(1), (e: unknown) => e instanceof KioskError && e.kind === 'client');
  assert.equal(calls.length, 0, 'ohne Kiosk-Sicherheitswert wird nichts gesendet');
});

test('Beschädigtes Cookie sendet nichts', async () => {
  const { client, calls } = harness([], `${KIOSK_CSRF_COOKIE}=%E0%A4%A`);
  await assert.rejects(client.cancelOverride(1), (e: unknown) => e instanceof KioskError && e.kind === 'client');
  assert.equal(calls.length, 0);
});

test('401 ist ein Fehler `invalid` -- kein Umleiten, kein zweiter Aufruf', async () => {
  const { client, calls } = harness([json(401, INVALID), json(200, {})]);
  await assert.rejects(client.state(), (e: unknown) => e instanceof KioskError && e.kind === 'invalid' && e.status === 401);
  assert.equal(calls.length, 1);
});

test('Schreibaufrufe werden bei keinem Fehler wiederholt', async () => {
  for (const response of [
    json(401, INVALID),
    json(403, { detail: 'x', code: 'csrf' }),
    json(404, { detail: 'x', code: 'not_found' }),
    json(422, { detail: 'x', code: 'rejected' }),
    json(500, { detail: 'x' }),
    new Error('Netz weg'),
  ]) {
    const { client, calls } = harness([response, json(200, {})]);
    await assert.rejects(client.boost(7));
    assert.equal(calls.length, 1, 'genau ein Versuch');
  }
});

test('Fehlerarten', () => {
  assert.equal(kioskErrorKind(401, 'kiosk_token_invalid'), 'invalid');
  // Ein 401 anderer Herkunft (z. B. vorgeschalteter Proxy) ist kein widerrufener Zugang.
  assert.equal(kioskErrorKind(401, null), 'http');
  assert.equal(kioskErrorKind(403, 'csrf'), 'csrf');
  assert.equal(kioskErrorKind(403, 'forbidden'), 'forbidden');
  assert.equal(kioskErrorKind(404, 'not_found'), 'not_found');
  assert.equal(kioskErrorKind(404, null), 'http');
  assert.equal(kioskErrorKind(422, 'rejected'), 'rejected');
  assert.equal(kioskErrorKind(422, 'validation'), 'http');
  assert.equal(kioskErrorKind(503, 'setup_incomplete'), 'setup');
});

test('Netzfehler und ungültiges JSON', async () => {
  const net = harness([new TypeError('Failed to fetch')]);
  await assert.rejects(net.client.state(), (e: unknown) => e instanceof KioskError && e.kind === 'network');
  const bad = harness([new Response('<html>', { status: 200 })]);
  await assert.rejects(bad.client.state(), (e: unknown) => e instanceof KioskError && e.kind === 'http');
  const noJson = harness([new Response('boom', { status: 502, statusText: 'Bad Gateway' })]);
  await assert.rejects(
    noJson.client.state(),
    (e: unknown) => e instanceof KioskError && e.kind === 'http' && e.message === 'Bad Gateway',
  );
});

test('Pfade der drei Aktionen', async () => {
  const { client, calls } = harness([json(200, {}), json(200, {}), json(200, {})]);
  await client.step(2, 'down');
  await client.boost(2);
  await client.cancelOverride(2);
  assert.deepEqual(
    calls.map((c) => c.url),
    [
      '/ing/kiosk-api/rooms/2/setpoint-step',
      '/ing/kiosk-api/rooms/2/boost',
      '/ing/kiosk-api/rooms/2/override/cancel',
    ],
  );
  assert.equal(calls[1].init.body, undefined);
});
