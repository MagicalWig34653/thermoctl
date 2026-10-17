import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCENTS } from '../src/lib/appearance.svelte.ts';
import { accentTokens } from '../src/lib/accent.ts';

test('die Auswahl hat 20 verschiedene, gültige Akzentfarben mit Gelb', () => {
  assert.equal(ACCENTS.length, 20);
  assert.equal(new Set(ACCENTS.map((a) => a.id)).size, 20);
  assert.equal(new Set(ACCENTS.map((a) => a.hex)).size, 20);
  assert.ok(ACCENTS.some((a) => a.id === 'gelb'));
  for (const a of ACCENTS) {
    for (const dark of [false, true]) {
      const tokens = accentTokens(a.hex, dark);
      assert.ok(tokens, `${a.id} ist keine gültige Farbe`);
      // Schrift auf der Akzentfläche: Gelb bekommt Schwarz, Dunkles Weiß.
      assert.match(tokens.foreground, /^#(ffffff|000000)$/);
    }
  }
  assert.equal(accentTokens('#eab308', false)?.foreground, '#000000');
});
