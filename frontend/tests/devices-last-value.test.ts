import assert from 'node:assert/strict';
import { test } from 'node:test';
import { lastValueLine } from '../src/lib/devices/last-value';

const base = { unit: null, value_type: 'text', last_value: null, last_value_at: null, last_value_age_seconds: null };
const now = new Date('2026-10-16T12:30:00Z');

test('ohne Wert steht, dass noch keiner kam', () => {
  assert.equal(lastValueLine(base, 'Europe/Berlin', now), 'Noch kein Wert empfangen');
});

test('Zahl mit Einheit, Alter und Uhrzeit in der Anlagenzeit (nicht der des Browsers)', () => {
  const line = lastValueLine(
    { ...base, value_type: 'numeric', unit: '°C', last_value: '21.5', last_value_at: '2026-10-16T12:25:00+00:00', last_value_age_seconds: 300 },
    'Europe/Berlin',
    now,
  );
  assert.equal(line, '21,5 °C · vor 5 Min. (14:25 Uhr)');
  const utc = lastValueLine(
    { ...base, value_type: 'numeric', last_value: '1', last_value_at: '2026-10-16T12:25:00+00:00', last_value_age_seconds: 300 },
    'UTC',
    now,
  );
  assert.equal(utc, '1 · vor 5 Min. (12:25 Uhr)');
});

test('Text bleibt unverändert; ohne Zeitpunkt nur der Wert; frischer Wert heißt gerade eben', () => {
  assert.equal(lastValueLine({ ...base, last_value: 'hold.5' }, 'UTC', now), 'hold.5');
  assert.equal(
    lastValueLine({ ...base, last_value: 'hold', last_value_at: '2026-10-16T12:30:00+00:00', last_value_age_seconds: 0 }, 'UTC', now),
    'hold · gerade eben (12:30 Uhr)',
  );
});
