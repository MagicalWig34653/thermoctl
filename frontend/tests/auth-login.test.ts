import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createApi } from '../src/lib/api.ts';
import {
  PASSKEY_REFUSED,
  assertionBody,
  fetchPasskeysOffered,
  passkeyLogin,
  passkeyLoginMessage,
  prepareAuthentication,
} from '../src/lib/auth/login.ts';
import { acceptedTarget, replyTarget, requestedReturnTo, targetHref } from '../src/lib/auth/target.ts';
import { MESSAGES } from '../src/lib/session.svelte.ts';

type Call = { url: string; init: RequestInit };

function fakeApi(routes: Record<string, { status: number; body: unknown }>, cookies = 'thermoctl_csrf=tok') {
  const calls: Call[] = [];
  const fetchFake = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const hit = routes[`${init.method} ${url}`];
    if (!hit) throw new Error(`unerwartet: ${init.method} ${url}`);
    return new Response(JSON.stringify(hit.body), { status: hit.status });
  }) as unknown as typeof fetch;
  return { api: createApi({ fetch: fetchFake, prefix: '/ing', cookies: () => cookies }), calls };
}

test('requestedReturnTo: Rohwert aus der Adresse, leer und zu lang ergeben null', () => {
  assert.equal(requestedReturnTo('?return_to=%2Foauth%2Fconsent%3Frequest%3Dabc'), '/oauth/consent?request=abc');
  assert.equal(requestedReturnTo(''), null);
  assert.equal(requestedReturnTo('?return_to='), null);
  assert.equal(requestedReturnTo(`?return_to=/${'a'.repeat(2048)}`), null);
});

test('acceptedTarget: nur Pfade der eigenen Herkunft', () => {
  assert.equal(acceptedTarget('/oauth/consent?request=abc'), '/oauth/consent?request=abc');
  for (const bad of [
    '//evil.example/x',
    'https://evil.example/',
    'javascript:alert(1)',
    '/\\evil.example',
    '/a\nb',
    '/a\u007fb',
    'relativ',
    '',
    `/${'a'.repeat(2048)}`,
    5,
    null,
    undefined,
    ['/x'],
  ]) {
    assert.equal(acceptedTarget(bad), null, String(bad));
  }
});

test('replyTarget und targetHref: nur die Antwort des Servers zählt, Präfix kommt von der Seite', () => {
  assert.equal(replyTarget({ ok: true, return_to: '/x' }), '/x');
  assert.equal(replyTarget({ ok: true, return_to: '//evil' }), null);
  assert.equal(replyTarget({ ok: true }), null);
  assert.equal(replyTarget(undefined), null);
  assert.equal(targetHref('/ing', '/oauth/consent?request=a'), '/ing/oauth/consent?request=a');
  assert.equal(targetHref('', '/x'), '/x');
});

test('fetchPasskeysOffered: nur true bei ausdrücklichem true, Fehler ergeben false', async () => {
  const on = fakeApi({ 'GET /ing/ui-api/login-options': { status: 200, body: { passkeys_available: true } } });
  assert.equal(await fetchPasskeysOffered(on.api), true);
  const off = fakeApi({ 'GET /ing/ui-api/login-options': { status: 200, body: { passkeys_available: 'ja' } } });
  assert.equal(await fetchPasskeysOffered(off.api), false);
  const down = fakeApi({ 'GET /ing/ui-api/login-options': { status: 500, body: {} } });
  assert.equal(await fetchPasskeysOffered(down.api), false);
});

test('prepareAuthentication wandelt Challenge und Zulassungsliste in Bytes', () => {
  const ready = prepareAuthentication({ challenge: 'AQID', allowCredentials: [{ id: 'BAU', type: 'public-key' }], rpId: 'x' });
  assert.deepEqual([...new Uint8Array(ready.challenge as ArrayBuffer)], [1, 2, 3]);
  assert.deepEqual([...new Uint8Array(ready.allowCredentials![0].id as ArrayBuffer)], [4, 5]);
  assert.equal(ready.rpId, 'x');
  assert.equal(prepareAuthentication({ challenge: 'AQID' }).allowCredentials, undefined);
});

function credential(userHandle: ArrayBuffer | null): PublicKeyCredential {
  return {
    id: 'cred',
    type: 'public-key',
    rawId: new Uint8Array([1, 2]).buffer,
    response: {
      clientDataJSON: new Uint8Array([3]).buffer,
      authenticatorData: new Uint8Array([4]).buffer,
      signature: new Uint8Array([5]).buffer,
      userHandle,
    },
  } as unknown as PublicKeyCredential;
}

test('assertionBody: base64url, return_to nur bei Wunsch', () => {
  const plain = assertionBody(credential(null), null);
  assert.equal(plain.response.userHandle, null);
  assert.equal(plain.rawId, 'AQI');
  assert.ok(!('return_to' in plain));
  const wished = assertionBody(credential(new Uint8Array([9]).buffer), '/x');
  assert.equal(wished.response.userHandle, 'CQ');
  assert.equal((wished as { return_to?: string }).return_to, '/x');
});

test('passkeyLogin: Optionen, Browser-Zeremonie, Prüfung; nur das bestätigte Ziel kommt zurück', async () => {
  const { api, calls } = fakeApi({
    'POST /ing/passkey/authentication/options': { status: 200, body: { challenge: 'AQID' } },
    'POST /ing/passkey/authentication/verify': {
      status: 200,
      body: { status: 'signed_in', redirect: '/ing/', return_to: '/oauth/consent?request=a' },
    },
  });
  let asked: CredentialRequestOptions | undefined;
  const credentials = {
    get: async (options: CredentialRequestOptions) => {
      asked = options;
      return credential(null);
    },
  };
  const result = await passkeyLogin({ api, credentials }, '/oauth/consent?request=a');
  assert.deepEqual(result, { target: '/oauth/consent?request=a' });
  assert.equal(calls.length, 2);
  assert.equal((calls[1].init.headers as Record<string, string>)['X-CSRF-Token'], 'tok');
  assert.equal(JSON.parse(calls[1].init.body as string).return_to, '/oauth/consent?request=a');
  assert.ok(asked?.publicKey?.challenge instanceof Uint8Array);
});

test('passkeyLogin: ein unbrauchbares Ziel der Antwort wird verworfen, `redirect` nie benutzt', async () => {
  const { api } = fakeApi({
    'POST /ing/passkey/authentication/options': { status: 200, body: { challenge: 'AQID' } },
    'POST /ing/passkey/authentication/verify': {
      status: 200,
      body: { status: 'signed_in', redirect: 'https://evil.example/', return_to: '//evil.example' },
    },
  });
  const result = await passkeyLogin({ api, credentials: { get: async () => credential(null) } }, '//evil.example');
  assert.deepEqual(result, { target: null });
});

test('passkeyLogin: ohne Browserunterstützung kein Netzaufruf, ohne Auswahl keine Prüfung', async () => {
  const none = fakeApi({});
  await assert.rejects(passkeyLogin({ api: none.api, credentials: undefined }, null), /unterstützt keine Passkeys/);
  assert.equal(none.calls.length, 0);
  const { api, calls } = fakeApi({
    'POST /ing/passkey/authentication/options': { status: 200, body: { challenge: 'AQID' } },
  });
  await assert.rejects(passkeyLogin({ api, credentials: { get: async () => null } }, null), /kein Passkey ausgewählt/);
  assert.equal(calls.length, 1);
});

test('passkeyLogin: Ablehnung des Servers (401) bleibt ein ApiError', async () => {
  const { api } = fakeApi({
    'POST /ing/passkey/authentication/options': { status: 200, body: { challenge: 'AQID' } },
    'POST /ing/passkey/authentication/verify': { status: 401, body: { status: 'rejected', notice: 'x' } },
  });
  await assert.rejects(
    passkeyLogin({ api, credentials: { get: async () => credential(null) } }, null),
    (e: unknown) => e instanceof ApiError && e.kind === 'unauthorized',
  );
});

test('passkeyLoginMessage: neutral, ohne Serverdetails', () => {
  assert.equal(passkeyLoginMessage(new ApiError('unauthorized', 401, 'Unauthorized')), PASSKEY_REFUSED);
  assert.equal(passkeyLoginMessage(new ApiError('network', 0, 'x')), MESSAGES.network);
  assert.equal(passkeyLoginMessage(new ApiError('forbidden', 403, 'x', true)), MESSAGES.stale);
  assert.equal(passkeyLoginMessage(new ApiError('http', 404, 'x')), 'Passkeys sind nicht eingerichtet.');
  assert.equal(passkeyLoginMessage(new ApiError('http', 500, 'Interner Fehler: Benutzer admin')), PASSKEY_REFUSED);
  const aborted = new Error('x');
  aborted.name = 'NotAllowedError';
  assert.equal(passkeyLoginMessage(aborted), 'Die Anmeldung wurde abgebrochen.');
  assert.equal(passkeyLoginMessage(new Error('Es wurde kein Passkey ausgewählt.')), 'Es wurde kein Passkey ausgewählt.');
  assert.equal(passkeyLoginMessage(new Error('Dieser Browser unterstützt keine Passkeys.')), 'Dieser Browser unterstützt keine Passkeys.');
  assert.equal(passkeyLoginMessage(new Error('geheimes Detail')), PASSKEY_REFUSED);
  assert.equal(passkeyLoginMessage('boom'), PASSKEY_REFUSED);
});
