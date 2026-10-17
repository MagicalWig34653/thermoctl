import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { GLOW_KEY, GLOW_STEP_KEY, GLOW_STEPS, loadGlowStep, parseGlowStep, saveGlowStep } from '../src/lib/appearance.svelte.ts';

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

test('parseGlowStep: nur ganze Zahlen von 0 bis 6', () => {
  assert.equal(parseGlowStep(null), null);
  assert.equal(parseGlowStep(''), null);
  assert.equal(parseGlowStep('7'), null);
  assert.equal(parseGlowStep('-1'), null);
  assert.equal(parseGlowStep('2.5'), null);
  assert.equal(parseGlowStep('stark'), null);
  for (let n = 0; n <= 6; n++) assert.equal(parseGlowStep(String(n)), n);
});
test('Glow: sechs Stufen plus aus, Beschriftungen ohne Lücke', () => {
  assert.deepEqual(GLOW_STEPS.map((s) => s.id), [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(GLOW_STEPS[0].label, 'Aus');
  assert.equal(new Set(GLOW_STEPS.map((s) => s.label)).size, 7);
});
test('Glow: leerer Speicher ergibt aus', () => {
  stubStorage({});
  assert.equal(loadGlowStep(), 0);
});
test('Glow: Speichern und Laden', () => {
  const store: Record<string, string> = {};
  stubStorage(store);
  saveGlowStep(5);
  assert.equal(store[GLOW_STEP_KEY], '5');
  assert.equal(loadGlowStep(), 5);
  saveGlowStep(0);
  assert.equal(loadGlowStep(), 0);
});
test('Glow: wer früher „an“ gewählt hat, bekommt Mittel; „aus“ bleibt aus', () => {
  stubStorage({ [GLOW_KEY]: 'on' });
  assert.equal(loadGlowStep(), 3);
  stubStorage({ [GLOW_KEY]: 'off' });
  assert.equal(loadGlowStep(), 0);
  // Die neue Wahl hat Vorrang vor dem alten Schalter.
  stubStorage({ [GLOW_KEY]: 'on', [GLOW_STEP_KEY]: '0' });
  assert.equal(loadGlowStep(), 0);
});
test('Glow: kaputter Speicher bricht nichts, Vorgabe aus', () => {
  stubStorage('broken');
  assert.equal(loadGlowStep(), 0);
  assert.doesNotThrow(() => saveGlowStep(4));
});
test('Glow: kaputter Wert im Speicher gilt als aus', () => {
  stubStorage({ [GLOW_STEP_KEY]: '{"x":' });
  assert.equal(loadGlowStep(), 0);
});
