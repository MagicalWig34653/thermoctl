import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createApi } from '../src/lib/api.ts';
import {
  OFFLINE_WRITE_MESSAGE,
  bannerText,
  classifyFailure,
  clockLabel,
  createConnectionTracker,
  createProber,
} from '../src/lib/connection.ts';
import { KioskError } from '../src/lib/kiosk/client.ts';

function tracker(start = 1000) {
  let clock = start;
  const t = createConnectionTracker({ now: () => clock });
  return { t, tick: (ms: number) => (clock += ms) };
}

test('ein einzelner Fehlschlag meldet noch keine Unterbrechung', () => {
  const { t } = tracker();
  t.success();
  t.failure();
  assert.equal(t.state.down, false);
  assert.equal(t.state.failures, 1);
});

test('zwei Fehlschläge in Folge: unterbrochen, Zeit der letzten Aktualität bleibt stehen', () => {
  const { t, tick } = tracker(1000);
  t.success();
  tick(5000);
  t.failure();
  tick(5000);
  t.failure();
  assert.equal(t.state.down, true);
  assert.equal(t.state.lastOkAt, 1000);
  tick(5000);
  t.failure();
  assert.equal(t.state.lastOkAt, 1000);
});

test('ein Erfolg dazwischen setzt den Zähler zurück', () => {
  const { t } = tracker();
  t.failure();
  t.success();
  t.failure();
  assert.equal(t.state.down, false);
});

test('Erholung: der nächste Erfolg hebt die Unterbrechung auf und erneuert die Zeit', () => {
  const { t, tick } = tracker(1000);
  t.success();
  t.failure();
  t.failure();
  tick(9000);
  t.success();
  assert.equal(t.state.down, false);
  assert.equal(t.state.failures, 0);
  assert.equal(t.state.lastOkAt, 10_000);
});

test('Änderungsmeldung kommt nur beim Wechsel', () => {
  const seen: boolean[] = [];
  const t = createConnectionTracker({ onChange: (s) => seen.push(s.down) });
  t.success();
  t.failure();
  t.failure();
  t.failure();
  t.success();
  t.success();
  assert.deepEqual(seen, [true, false]);
});

test('classifyFailure: Netz, Zeitüberschreitung und 502/503/504 zählen', () => {
  assert.equal(classifyFailure(new ApiError('network', 0, 'x')), 'down');
  assert.equal(classifyFailure(new ApiError('http', 502, 'x')), 'down');
  assert.equal(classifyFailure(new ApiError('http', 503, 'x')), 'down');
  assert.equal(classifyFailure(new ApiError('http', 504, 'x')), 'down');
  assert.equal(classifyFailure(new KioskError('network', 0, 'x')), 'down');
  assert.equal(classifyFailure(new KioskError('http', 502, 'x')), 'down');
});

test('classifyFailure: ein gewöhnliches 500/501 ist eine Antwort des Servers und zählt nicht', () => {
  assert.equal(classifyFailure(new ApiError('http', 500, 'x')), 'up');
  assert.equal(classifyFailure(new ApiError('http', 501, 'x')), 'up');
  assert.equal(classifyFailure(new KioskError('http', 500, 'x')), 'up');
});

function probeHarness() {
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  let next = 1;
  const s = { runs: 0, release: null as null | (() => void), fail: false };
  const prober = createProber({
    probe: () => {
      s.runs += 1;
      if (s.release !== null) return new Promise<void>((r) => (s.release = r));
      return s.fail ? Promise.reject(new Error('weg')) : Promise.resolve();
    },
    intervalMs: 10_000,
    setTimer: (fn, ms) => {
      const id = next++;
      timers.push({ fn, ms, id });
      return id;
    },
    clearTimer: (h) => {
      const i = timers.findIndex((t) => t.id === h);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const fire = async () => {
    const t = timers.shift();
    assert.ok(t, 'kein Timer geplant');
    t.fn();
    await new Promise((r) => setImmediate(r));
  };
  return { prober, timers, s, fire };
}

test('Prober: wartet das Intervall ab, probiert, plant erneut (auch nach Fehler)', async () => {
  const { prober, timers, s, fire } = probeHarness();
  prober.start();
  assert.equal(s.runs, 0);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 10_000);
  s.fail = true;
  await fire();
  assert.equal(s.runs, 1);
  assert.equal(timers.length, 1);
  await fire();
  assert.equal(s.runs, 2);
});

test('Prober: start ist idempotent, stop beendet den Timer und weitere Proben', async () => {
  const { prober, timers, s } = probeHarness();
  prober.start();
  prober.start();
  assert.equal(timers.length, 1);
  prober.stop();
  assert.equal(timers.length, 0);
  assert.equal(s.runs, 0);
  prober.stop();
});

test('Prober: keine Überlappung, der nächste Takt wird erst nach Ende der Probe geplant', async () => {
  const { prober, timers, s, fire } = probeHarness();
  s.release = () => {};
  prober.start();
  await fire();
  assert.equal(s.runs, 1);
  assert.equal(timers.length, 0);
  s.release();
  await new Promise((r) => setImmediate(r));
  assert.equal(timers.length, 1);
});

test('Prober: stop während einer laufenden Probe plant nichts mehr', async () => {
  const { prober, timers, s, fire } = probeHarness();
  s.release = () => {};
  prober.start();
  await fire();
  prober.stop();
  s.release();
  await new Promise((r) => setImmediate(r));
  assert.equal(timers.length, 0);
});

test('classifyFailure: 401, 403, 404 und andere Antworten des Servers zählen nicht', () => {
  assert.equal(classifyFailure(new ApiError('unauthorized', 401, 'x')), 'up');
  assert.equal(classifyFailure(new ApiError('forbidden', 403, 'x')), 'up');
  assert.equal(classifyFailure(new ApiError('http', 404, 'x')), 'up');
  assert.equal(classifyFailure(new ApiError('http', 422, 'x')), 'up');
  assert.equal(classifyFailure(new ApiError('client', 0, 'x')), 'ignore');
  assert.equal(classifyFailure(new KioskError('invalid', 401, 'x')), 'up');
  assert.equal(classifyFailure(new KioskError('setup', 503, 'x')), 'up');
  assert.equal(classifyFailure(new Error('x')), 'ignore');
});

test('Texte', () => {
  const at = new Date(2026, 10, 11, 7, 5).getTime();
  assert.equal(clockLabel(at), '07:05');
  assert.equal(bannerText(at), 'Verbindung unterbrochen – Anzeige veraltet seit 07:05');
  // Noch keine Antwort seit dem Laden: Zeit des Seitenstarts, mit Hinweis.
  assert.equal(
    bannerText(null, at),
    'Verbindung unterbrochen – Anzeige veraltet seit 07:05 (noch keine Antwort seit dem Laden)',
  );
});

function response(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function harness(next: () => Response | Error, offline = () => false) {
  const outcomes: boolean[] = [];
  let calls = 0;
  const api = createApi({
    prefix: '',
    cookies: () => 'thermoctl_csrf=abc',
    fetch: (async () => {
      calls += 1;
      const r = next();
      if (r instanceof Error) throw r;
      return r;
    }) as typeof fetch,
    onReachability: (reachable) => outcomes.push(reachable),
    isOffline: offline,
  });
  return { api, outcomes, calls: () => calls };
}

test('api meldet Erreichbarkeit: 200 und 404 erreichbar, Netzfehler und 502 nicht, 500 und 403 erreichbar', async () => {
  const seq: (Response | Error)[] = [response(200), response(404), new Error('weg'), response(502), response(500), response(403)];
  const h = harness(() => seq.shift()!);
  await h.api.get('/a');
  for (let n = 0; n < 5; n++) await assert.rejects(h.api.get('/a'));
  assert.deepEqual(h.outcomes, [true, true, false, false, true, true]);
});

test('api sperrt schreibende Aufrufe bei Unterbrechung, ohne zu senden und ohne zu zählen', async () => {
  const h = harness(() => response(200), () => true);
  for (const call of [() => h.api.post('/x', {}), () => h.api.put('/x', {}), () => h.api.delete('/x')]) {
    await assert.rejects(
      call(),
      (e: unknown) => e instanceof ApiError && e.kind === 'network' && e.message === OFFLINE_WRITE_MESSAGE,
    );
  }
  assert.equal(h.calls(), 0);
  assert.deepEqual(h.outcomes, []);
  await h.api.get('/x'); // Lesen bleibt erlaubt: so erholt sich die Anzeige
  assert.equal(h.calls(), 1);
});
