import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseIntegralPercent, pendingText } from '../src/lib/rooms/pi.ts';

test('Eingabe in Prozent wird zu 0..1, Grenzen eingeschlossen', () => {
  assert.deepEqual(parseIntegralPercent('0'), { ok: true, value: '0.000000' });
  assert.deepEqual(parseIntegralPercent('100'), { ok: true, value: '1.000000' });
  assert.deepEqual(parseIntegralPercent('42,5'), { ok: true, value: '0.425000' });
  assert.deepEqual(parseIntegralPercent(' 7.25 '), { ok: true, value: '0.072500' });
});

test('Eingaben außerhalb von 0 bis 100 oder ohne Zahl werden nicht verschickt', () => {
  for (const text of ['', ' ', '-1', '100,1', '101', '1000', 'abc', '1e2', '5%', '0,12345', '1,', ',5', 'NaN']) {
    const result = parseIntegralPercent(text);
    assert.equal(result.ok, false, `"${text}" muss abgewiesen werden`);
  }
});

test('Der vorgemerkte Eingriff wird mit Wert und Zeitpunkt der Wirkung benannt', () => {
  assert.match(pendingText({ reason: 'manuell_reset', reason_label: null, integral: '0.000000' }), /zurücksetzen.*nächsten Regelzyklus/);
  assert.match(pendingText({ reason: 'manuell_gesetzt', reason_label: null, integral: '0.425000' }), /auf 42,5 % setzen.*nächsten Regelzyklus/);
});
