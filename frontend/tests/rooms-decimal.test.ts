import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  compareDecimal,
  formatCelsius,
  formatTemperature,
  parseTemperatureInput,
  sameTemperature,
} from '../src/lib/rooms/decimal.ts';

test('formatTemperature: Komma, kein Runden, mindestens eine Nachkommastelle', () => {
  assert.equal(formatTemperature('21.50'), '21,5');
  assert.equal(formatTemperature('21'), '21,0');
  assert.equal(formatTemperature('21.25'), '21,25');
  assert.equal(formatTemperature('19.999'), '19,999'); // würde ein Float-Rundungsweg verfälschen
  assert.equal(formatTemperature('0.1'), '0,1');
  assert.equal(formatTemperature('-3.50'), '−3,5');
  assert.equal(formatTemperature(null), '—');
  assert.equal(formatTemperature('abc'), '—');
});

test('formatCelsius hängt die Einheit nur an echte Werte an', () => {
  assert.equal(formatCelsius('20.0'), '20,0 °C');
  assert.equal(formatCelsius(null), '—');
});

test('compareDecimal ist exakt und unabhängig von der Stellenzahl', () => {
  assert.equal(compareDecimal('21.0', '21.00'), 0);
  assert.equal(compareDecimal('21.5', '21.49'), 1);
  assert.equal(compareDecimal('0.1', '0.10000000000000001'), -1); // Float würde 0 liefern
  assert.equal(compareDecimal('-1.5', '1.5'), -1);
  assert.equal(compareDecimal('x', '1'), null);
});

test('sameTemperature behandelt null getrennt', () => {
  assert.equal(sameTemperature('21.0', '21'), true);
  assert.equal(sameTemperature(null, null), true);
  assert.equal(sameTemperature(null, '21'), false);
});

test('parseTemperatureInput: Komma oder Punkt, Form statt Bereich', () => {
  assert.deepEqual(parseTemperatureInput(' 21,5 '), { ok: true, value: '21.5' });
  assert.deepEqual(parseTemperatureInput('22'), { ok: true, value: '22' });
  for (const bad of ['', 'abc', '21,555', '1e3', '-5', '21,5,1']) {
    assert.equal(parseTemperatureInput(bad).ok, false, bad);
  }
});
