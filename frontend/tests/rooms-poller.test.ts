import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPoller } from '../src/lib/rooms/poller.ts';

function harness(opts: { hidden?: boolean } = {}) {
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  let next = 1;
  let clock = 0;
  const state = { hidden: opts.hidden ?? false, runs: 0, release: null as null | (() => void) };
  const poller = createPoller({
    run: () => {
      state.runs += 1;
      return state.release === null ? Promise.resolve() : new Promise<void>((r) => (state.release = r));
    },
    intervalMs: 30_000,
    isHidden: () => state.hidden,
    now: () => clock,
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
    clock += t.ms;
    t.fn();
    await new Promise((r) => setImmediate(r));
  };
  return { poller, timers, state, fire, advance: (ms: number) => (clock += ms) };
}

test('Takt: führt aus und plant den nächsten Lauf', async () => {
  const h = harness();
  h.poller.start();
  assert.equal(h.timers.length, 1);
  assert.equal(h.timers[0].ms, 30_000);
  await h.fire();
  assert.equal(h.state.runs, 1);
  assert.equal(h.timers.length, 1);
});

test('Versteckter Tab: kein Lauf, kein Timer; Rückkehr lädt sofort nach, wenn fällig', async () => {
  const h = harness();
  h.poller.start();
  h.state.hidden = true;
  h.poller.visibilityChanged();
  assert.equal(h.timers.length, 0);
  h.advance(60_000);
  h.state.hidden = false;
  h.poller.visibilityChanged();
  assert.equal(h.timers.length, 1);
  assert.equal(h.timers[0].ms, 0);
  await h.fire();
  assert.equal(h.state.runs, 1);
});

test('Timer feuert im versteckten Tab: nichts wird geladen', async () => {
  const h = harness();
  h.poller.start();
  h.state.hidden = true;
  await h.fire();
  assert.equal(h.state.runs, 0);
  assert.equal(h.timers.length, 0);
});

test('Kurze Abwesenheit: Rückkehr wartet nur die Restzeit ab', () => {
  const h = harness();
  h.poller.start();
  h.state.hidden = true;
  h.poller.visibilityChanged();
  h.advance(10_000);
  h.state.hidden = false;
  h.poller.visibilityChanged();
  assert.equal(h.timers[0].ms, 20_000);
});

test('stop: keine weiteren Läufe', async () => {
  const h = harness();
  h.poller.start();
  h.poller.stop();
  assert.equal(h.timers.length, 0);
  h.poller.visibilityChanged();
  assert.equal(h.timers.length, 0);
});
