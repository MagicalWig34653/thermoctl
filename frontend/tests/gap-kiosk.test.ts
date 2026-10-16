// Testlücken der abgelösten Alt-Oberfläche: fester Kiosk-Takt, Kiosk-Uhr in der Anlagenzeit.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { clockText, overrideText, POLL_INTERVAL_MS } from '../src/lib/kiosk/logic.ts';
import { createPoller } from '../src/lib/rooms/poller.ts';

// -- Fester 20-s-Takt -----------------------------------------------------------------------------

test('Der Kiosk liest alle 20 Sekunden', () => {
  assert.equal(POLL_INTERVAL_MS, 20_000);
});

test('Der Takt des Kiosks plant den nächsten Lauf nach genau 20 Sekunden', async () => {
  const timers: { fn: () => void; ms: number }[] = [];
  let runs = 0;
  const poller = createPoller({
    run: async () => {
      runs += 1;
    },
    intervalMs: POLL_INTERVAL_MS,
    isHidden: () => false,
    now: () => 0,
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimer: () => {},
  });
  poller.start();
  assert.deepEqual(timers.map((t) => t.ms), [20_000]);
  timers[0].fn();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(runs, 1);
  assert.equal(timers.at(-1)?.ms, 20_000);
});

test('Der Takt ist fest verdrahtet: weder die Ansicht noch der Zustand des Servers stellen ihn um', () => {
  const app = readFileSync(new URL('../src/lib/kiosk/KioskApp.svelte', import.meta.url), 'utf8');
  const controller = readFileSync(new URL('../src/lib/kiosk/controller.svelte.ts', import.meta.url), 'utf8');
  const types = readFileSync(new URL('../src/lib/kiosk/types.ts', import.meta.url), 'utf8');
  // Standard im Controller ist die Konstante ...
  assert.match(controller, /intervalMs:\s*deps\.intervalMs \?\? POLL_INTERVAL_MS/);
  // ... und niemand reicht einen anderen Takt hinein oder liest ihn aus der Serverantwort.
  assert.doesNotMatch(app, /intervalMs/);
  assert.doesNotMatch(app, /poll_interval/);
  assert.doesNotMatch(types, /poll_interval/);
});

// -- Uhr in der Zeitzone der Anlage ---------------------------------------------------------------

test('Die Uhr zeigt die konfigurierte Anlagenzeit, nicht die des Geräts', () => {
  const instant = '2026-10-16T12:05:00+00:00';
  assert.equal(clockText(instant, 'Europe/Berlin'), '14:05');
  assert.equal(clockText(instant, 'Asia/Tokyo'), '21:05');
  assert.equal(clockText(instant, 'America/New_York'), '08:05');
  assert.equal(clockText(instant, 'UTC'), '12:05');
  // Halbstunden-Zone und Datumsgrenze (Mitternacht liegt im nächsten Tag, die Uhr zeigt nur Zeit).
  assert.equal(clockText(instant, 'Asia/Kolkata'), '17:35');
  assert.equal(clockText('2026-10-16T23:30:00+00:00', 'Europe/Berlin'), '01:30');
});

test('Sommer- und Winterzeit: dieselbe UTC-Stunde zeigt je nach Datum eine andere Uhr', () => {
  assert.equal(clockText('2026-01-15T12:00:00+00:00', 'Europe/Berlin'), '13:00');
  assert.equal(clockText('2026-07-15T12:00:00+00:00', 'Europe/Berlin'), '14:00');
  // Umstellung auf Sommerzeit am 29.03.2026 um 01:00 UTC: 02:xx Ortszeit gibt es nicht.
  assert.equal(clockText('2026-03-29T00:59:00+00:00', 'Europe/Berlin'), '01:59');
  assert.equal(clockText('2026-03-29T01:00:00+00:00', 'Europe/Berlin'), '03:00');
  // Zurück auf Winterzeit am 25.10.2026 um 01:00 UTC: 02:30 Ortszeit kommt zweimal vor.
  assert.equal(clockText('2026-10-25T00:30:00+00:00', 'Europe/Berlin'), '02:30');
  assert.equal(clockText('2026-10-25T01:30:00+00:00', 'Europe/Berlin'), '02:30');
  assert.equal(clockText('2026-10-25T02:30:00+00:00', 'Europe/Berlin'), '03:30');
  // Andere Zone, andere Umstellung: New York endet die Sommerzeit am 01.11.2026 um 06:00 UTC.
  assert.equal(clockText('2026-11-01T05:30:00+00:00', 'America/New_York'), '01:30');
  assert.equal(clockText('2026-11-01T06:30:00+00:00', 'America/New_York'), '01:30');
  assert.equal(clockText('2026-11-01T07:30:00+00:00', 'America/New_York'), '02:30');
  // Südhalbkugel: Sydney hat im Oktober Sommerzeit (UTC+11), im Juli Winterzeit (UTC+10).
  assert.equal(clockText('2026-10-16T12:00:00+00:00', 'Australia/Sydney'), '23:00');
  assert.equal(clockText('2026-07-15T12:00:00+00:00', 'Australia/Sydney'), '22:00');
});

test('Das Ende einer Übersteuerung steht in der Anlagenzeit', () => {
  const override = { id: 1, temperature: '23.0', starts_at: '2026-10-16T10:00:00+00:00', ends_at: '2026-10-16T12:05:00+00:00' };
  assert.equal(overrideText(override as never, 'Asia/Tokyo'), 'Übersteuert bis 21:05');
  assert.equal(overrideText(override as never, 'Europe/Berlin'), 'Übersteuert bis 14:05');
  assert.equal(overrideText({ ...override, ends_at: null } as never, 'Asia/Tokyo'), 'Übersteuert, ohne Ende');
});
