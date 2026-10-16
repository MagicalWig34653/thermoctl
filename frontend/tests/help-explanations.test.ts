import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EXPLANATIONS } from '../src/lib/help/explanations.ts';

test('Erklärtexte der Alt-Hilfeseite sind vollständig, eindeutig und reiner Text', () => {
  assert.deepEqual(
    EXPLANATIONS.map((e) => e.title),
    ['Sollwert', 'Heizt / wartet', 'Zeitplan', 'Normale Temperatur ändern', 'Für eine Weile wärmer', 'Messwert möglicherweise nicht aktuell'],
  );
  assert.equal(new Set(EXPLANATIONS.map((e) => e.id)).size, EXPLANATIONS.length);
  for (const e of EXPLANATIONS) {
    assert.ok(e.text.length > 40, e.id);
    assert.doesNotMatch(e.text, /[<>]/, `${e.id} enthält Markup`);
  }
});
