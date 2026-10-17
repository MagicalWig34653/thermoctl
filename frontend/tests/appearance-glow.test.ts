import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { GLOW_KEY, loadGlow, parseGlow, saveGlow } from '../src/lib/appearance.svelte.ts';

function stubStorage(store: Record<string, string> | 'broken') {
  const storage =
    store === 'broken'
      ? {
          getItem() {
            throw new Error('gesperrt');
          },
          setItem() {
            throw new Error('gesperrt');
          },
        }
      : {
          getItem: (k: string) => (k in store ? store[k] : null),
          setItem: (k: string, v: string) => {
            store[k] = v;
          },
        };
  Object.defineProperty(globalThis, 'window', { value: { localStorage: storage }, configurable: true });
}
afterEach(() => {
  Reflect.deleteProperty(globalThis, 'window');
});

test('parseGlow: Vorgabe aus, nur "on" schaltet ein', () => {
  assert.equal(parseGlow(null), false);
  assert.equal(parseGlow('off'), false);
  assert.equal(parseGlow('1'), false);
  assert.equal(parseGlow('on'), true);
});
test('Glow: leerer Speicher ergibt aus', () => {
  stubStorage({});
  assert.equal(loadGlow(), false);
});
test('Glow: Speichern und Laden', () => {
  const store: Record<string, string> = {};
  stubStorage(store);
  saveGlow(true);
  assert.equal(store[GLOW_KEY], 'on');
  assert.equal(loadGlow(), true);
  saveGlow(false);
  assert.equal(store[GLOW_KEY], 'off');
  assert.equal(loadGlow(), false);
});
test('Glow: kaputter Speicher bricht nichts, Vorgabe aus', () => {
  stubStorage('broken');
  assert.equal(loadGlow(), false);
  assert.doesNotThrow(() => saveGlow(true));
});
test('Glow: kaputter Wert im Speicher gilt als aus', () => {
  stubStorage({ [GLOW_KEY]: '{"x":' });
  assert.equal(loadGlow(), false);
});
