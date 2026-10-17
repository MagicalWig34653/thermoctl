import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RELOAD_KEY, RELOAD_WINDOW_MS, mayReload } from '../src/lib/reload.ts';

const store = (initial: Record<string, string> = {}) => {
  const data = { ...initial };
  return { data, getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) };
};

test('Neuladen: erstes Mal erlaubt und vermerkt, im Zeitfenster nicht wieder', () => {
  const s = store();
  assert.equal(mayReload(s, 1_000_000), true);
  assert.equal(s.data[RELOAD_KEY], '1000000');
  assert.equal(mayReload(s, 1_000_000 + RELOAD_WINDOW_MS - 1), false);
  assert.equal(mayReload(s, 1_000_000 + RELOAD_WINDOW_MS), true);
});
test('Neuladen: ohne Speicher oder mit kaputtem Speicher nie (keine Schleife)', () => {
  assert.equal(mayReload(null, 5), false);
  const broken = { getItem: () => { throw new Error('gesperrt'); }, setItem: () => {} };
  assert.equal(mayReload(broken, 5), false);
  const readOnly = { getItem: () => null, setItem: () => { throw new Error('voll'); } };
  assert.equal(mayReload(readOnly, 5), false);
});
test('Neuladen: Müll im Speicher zählt als nie neu geladen', () => {
  assert.equal(mayReload(store({ [RELOAD_KEY]: 'abc' }), 5), true);
});
