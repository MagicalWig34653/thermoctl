import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatDateTime } from '../src/lib/access/format.ts';

// 2026-07-01 22:30 UTC ist in Berlin schon der 2.7. (Sommerzeit, 00:30) und in Auckland der 2.7., 10:30.
const ISO = '2026-07-01T22:30:00+00:00';

test('formatDateTime: Anlagen-Zeitzone statt Zeitzone des Browsers', () => {
  const berlin = formatDateTime(ISO, '—', 'Europe/Berlin');
  const auckland = formatDateTime(ISO, '—', 'Pacific/Auckland');
  const newYork = formatDateTime(ISO, '—', 'America/New_York');
  assert.match(berlin, /02\.07\.2026/);
  assert.match(berlin, /00:30/);
  assert.match(auckland, /10:30/);
  assert.match(newYork, /01\.07\.2026/);
  assert.match(newYork, /18:30/);
});

test('formatDateTime: Winterzeit und Sommerzeit der Anlage', () => {
  assert.match(formatDateTime('2026-01-15T12:00:00+00:00', '—', 'Europe/Berlin'), /13:00/);
  assert.match(formatDateTime('2026-07-15T12:00:00+00:00', '—', 'Europe/Berlin'), /14:00/);
});

test('formatDateTime: unbekannte Zeitzone und leere Werte fallen nicht aus', () => {
  assert.match(formatDateTime(ISO, '—', 'Kein/Ort'), /\d{2}:\d{2}/);
  assert.equal(formatDateTime(null, 'nie', 'Europe/Berlin'), 'nie');
  assert.equal(formatDateTime('', 'nie'), 'nie');
  assert.equal(formatDateTime('kein datum', 'x'), 'x');
});
