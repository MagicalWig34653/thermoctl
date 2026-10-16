import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createApi, readCookie, runtimePrefix } from '../src/lib/api.ts';

type Call = { url: string; init: RequestInit };

function setup(status: number, body: unknown = {}, cookies = 'a=1; thermoctl_csrf=tok%20en') {
  const calls: Call[] = [];
  let unauthorized = 0;
  const fakeFetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const api = createApi({
    fetch: fakeFetch,
    prefix: '/ing',
    cookies: () => cookies,
    onUnauthorized: () => {
      unauthorized += 1;
    },
  });
  return { api, calls, unauthorized: () => unauthorized };
}

test('readCookie findet den Wert und dekodiert ihn', () => {
  assert.equal(readCookie('a=1; thermoctl_csrf=x%20y', 'thermoctl_csrf'), 'x y');
  assert.equal(readCookie('a=1', 'thermoctl_csrf'), null);
});

test('GET: Präfix, Cookie-Credentials, kein CSRF-Header', async () => {
  const { api, calls } = setup(200, { ok: true });
  assert.deepEqual(await api.get('/ui-api/bootstrap'), { ok: true });
  assert.equal(calls[0].url, '/ing/ui-api/bootstrap');
  assert.equal(calls[0].init.credentials, 'same-origin');
  assert.equal((calls[0].init.headers as Record<string, string>)['X-CSRF-Token'], undefined);
});

test('POST: CSRF-Header aus dem Cookie, JSON-Body', async () => {
  const { api, calls } = setup(200);
  await api.post('/ui-api/x', { n: 1 });
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers['X-CSRF-Token'], 'tok en');
  assert.equal(headers['Content-Type'], 'application/json');
  assert.equal(calls[0].init.body, '{"n":1}');
});

test('401: Callback, ApiError, genau ein Aufruf (keine Wiederholung)', async () => {
  const { api, calls, unauthorized } = setup(401, { detail: 'abgelaufen' });
  await assert.rejects(api.post('/ui-api/x', {}), (e: ApiError) => {
    assert.equal(e.kind, 'unauthorized');
    assert.equal(e.message, 'abgelaufen');
    return true;
  });
  assert.equal(calls.length, 1);
  assert.equal(unauthorized(), 1);
});

test('403: forbidden ohne Unauthorized-Callback, ohne Wiederholung', async () => {
  const { api, calls, unauthorized } = setup(403, { detail: 'verboten' });
  await assert.rejects(api.put('/ui-api/x', {}), (e: ApiError) => e.kind === 'forbidden');
  assert.equal(calls.length, 1);
  assert.equal(unauthorized(), 0);
});

test('Netzwerkfehler und 500 werden als ApiError gemeldet', async () => {
  const failing = createApi({
    fetch: (async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch,
    prefix: '',
    cookies: () => '',
  });
  await assert.rejects(failing.get('/x'), (e: ApiError) => e.kind === 'network');
  const { api } = setup(500, {});
  await assert.rejects(api.delete('/x'), (e: ApiError) => e.kind === 'http' && e.status === 500);
});

test('kaputtes CSRF-Cookie: ApiError statt URIError, kein Request', async () => {
  const { api, calls } = setup(200, {}, 'thermoctl_csrf=%E0%A4%A');
  assert.throws(() => readCookie('thermoctl_csrf=%E0%A4%A', 'thermoctl_csrf'), URIError);
  await assert.rejects(api.post('/ui-api/x', {}), (e: ApiError) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.kind, 'client');
    return true;
  });
  assert.equal(calls.length, 0);
  // Lesende Aufrufe brauchen das Cookie nicht und laufen weiter.
  await api.get('/ui-api/bootstrap');
  assert.equal(calls.length, 1);
});

test('403 mit stale: Sitzung ungültig, Callback mit Grund, keine Wiederholung', async () => {
  const reasons: string[] = [];
  let n = 0;
  const api = createApi({
    fetch: (async () => {
      n += 1;
      return new Response(JSON.stringify({ detail: 'Ungültiges CSRF-Token', stale: true }), { status: 403 });
    }) as unknown as typeof fetch,
    prefix: '',
    cookies: () => '',
    onUnauthorized: (r) => reasons.push(r),
  });
  await assert.rejects(api.post('/ui-api/logout'), (e: ApiError) => e.kind === 'forbidden' && e.stale);
  assert.equal(n, 1);
  assert.deepEqual(reasons, ['stale']);
});

test('401 ohne JSON-Body und Erfolg mit kaputtem JSON werden zu ApiError', async () => {
  const noBody = createApi({
    fetch: (async () => new Response('nope', { status: 401, statusText: 'Unauthorized' })) as unknown as typeof fetch,
    prefix: '',
    cookies: () => '',
  });
  await assert.rejects(noBody.get('/x'), (e: ApiError) => e.kind === 'unauthorized' && e.message === 'Unauthorized');
  const broken = createApi({
    fetch: (async () => new Response('<html>', { status: 200 })) as unknown as typeof fetch,
    prefix: '',
    cookies: () => '',
  });
  await assert.rejects(broken.get('/x'), (e: ApiError) => e.kind === 'http');
});

test('204 liefert undefined', async () => {
  const api = createApi({
    fetch: (async () => new Response(null, { status: 204 })) as unknown as typeof fetch,
    prefix: '',
    cookies: () => '',
  });
  assert.equal(await api.delete('/x'), undefined);
});

function fakeDoc(meta: string | null, dataset?: string): Document {
  return {
    querySelector: () => (meta === null ? null : { getAttribute: () => meta }),
    getElementById: () => (dataset === undefined ? null : { dataset: { urlPrefix: dataset } }),
  } as unknown as Document;
}

test('runtimePrefix: Meta, Fallback auf data-Attribut, Schlussstriche, leer', () => {
  assert.equal(runtimePrefix(fakeDoc('/api/hassio_ingress/abc_DEF-1/')), '/api/hassio_ingress/abc_DEF-1');
  assert.equal(runtimePrefix(fakeDoc('')), '');
  assert.equal(runtimePrefix(fakeDoc(null, '/ing')), '/ing');
  assert.equal(runtimePrefix(fakeDoc(null)), '');
});

test('runtimePrefix: ungültige Präfixe (Schema, //, Anführungszeichen) werden verworfen', () => {
  for (const bad of ['https://evil.example', '//evil.example', '/a"b', '/a b', 'ohne-slash', '/a//b']) {
    assert.equal(runtimePrefix(fakeDoc(bad)), '', bad);
  }
});
