import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSecretHolder, kioskLink } from '../src/lib/access/secret.ts';

const PLAIN = 'thermo_geheim_ABCDEFG123456';

test('Klartext steht nur im Halter und ist nach dem Schließen weg', () => {
  const h = createSecretHolder();
  assert.deepEqual([h.value, h.open], [null, false]);
  h.show(PLAIN, 'Skript');
  assert.deepEqual([h.value, h.label, h.open], [PLAIN, 'Skript', true]);
  h.clear();
  assert.deepEqual([h.value, h.label, h.open], [null, null, false]);
});

test('Ein zweites Zeigen ersetzt, nie ergänzt', () => {
  const h = createSecretHolder();
  h.show(PLAIN, 'a');
  h.show('anderer', 'b');
  assert.equal(h.value, 'anderer');
  h.clear();
  assert.equal(h.value, null);
});

test('Serialisieren und Ausgeben des Halters verrät den Klartext nicht', () => {
  const h = createSecretHolder();
  h.show(PLAIN, 'Skript');
  assert.equal(JSON.stringify(h).includes(PLAIN), false);
  assert.equal(String(h).includes(PLAIN), false);
  assert.equal(`${h}`.includes(PLAIN), false);
  assert.equal(JSON.stringify(Object.keys(h)).includes(PLAIN), false);
});

test('Der Halter schreibt nirgends hin: weder Storage noch Konsole', () => {
  const g = globalThis as Record<string, unknown>;
  const calls: string[] = [];
  const spy = { setItem: (...a: unknown[]) => calls.push(`storage:${a.join()}`) };
  g.localStorage = spy;
  g.sessionStorage = spy;
  const original = { log: console.log, error: console.error, warn: console.warn, debug: console.debug };
  for (const k of Object.keys(original) as (keyof typeof original)[]) console[k] = (...a: unknown[]) => calls.push(`console:${a.join()}`);
  try {
    const h = createSecretHolder();
    h.show(PLAIN, 'x');
    h.clear();
  } finally {
    Object.assign(console, original);
    delete g.localStorage;
    delete g.sessionStorage;
  }
  assert.deepEqual(calls, []);
});

test('Kiosk-Adresse enthält Ursprung, Präfix und Klartext genau einmal', () => {
  assert.equal(kioskLink('https://heizung.example', '/ing', PLAIN), `https://heizung.example/ing/kiosk/${PLAIN}`);
  assert.equal(kioskLink('http://h', '', 'abc'), 'http://h/kiosk/abc');
});
