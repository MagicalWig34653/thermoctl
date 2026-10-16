import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Block } from '../src/lib/schedule/geometry.ts';
import { clockValue, exactTimes, nudge, parseClock, toDay } from '../src/lib/schedule/inspector.ts';
import { describeIntent, ghostOf, isNoop, selectionAfter, type Intent } from '../src/lib/schedule/intents.ts';
import { localClock, stripParts, zoneLabel } from '../src/lib/schedule/format.ts';

const block = (weekday: number, start: number, end: number): Block => ({
  weekday,
  start,
  end,
  modeId: 1,
  modeName: 'Tag',
  warmth: 0.8,
  temperatureC: null,
  carry: false,
});

test('Uhrzeiten: Mitternacht als Ende', () => {
  assert.equal(parseClock('07:30'), 450);
  assert.equal(parseClock('7:05'), 425);
  assert.equal(parseClock('00:00'), 0);
  assert.equal(parseClock('00:00', true), 1440);
  assert.equal(parseClock('24:00'), null);
  assert.equal(parseClock('12:60'), null);
  assert.equal(parseClock('abc'), null);
  assert.equal(clockValue(1440), '00:00');
  assert.equal(clockValue(450), '07:30');
});

test('Exakte Zeiten halten Spielraum und Mindestdauer ein', () => {
  const a = block(1, 420, 540);
  const b = block(1, 600, 720);
  const ok = exactTimes(a, [a, b], 400, 590);
  assert.deepEqual(ok, { intent: { type: 'resize', block: a, newStart: 400, newEnd: 590, adjacent: false } });
  const hit = exactTimes(a, [a, b], 400, 610);
  assert.ok('error' in hit && /bereits/.test(hit.error));
  assert.ok('error' in exactTimes(a, [a, b], 500, 500));
  assert.ok('error' in exactTimes(a, [a, b], 500.5, 560));
  const toMidnight = exactTimes(b, [a, b], 600, 1440);
  assert.ok('intent' in toMidnight);
});

test('Exakte Zeiten an einer angrenzenden Grenze schieben den Nachbarn mit', () => {
  const a = block(1, 420, 540);
  const b = { ...block(1, 540, 720), modeId: 2 };
  const moved = exactTimes(a, [a, b], 420, 600);
  assert.deepEqual((moved as { intent: { adjacent: boolean } }).intent.adjacent, true);
  const tooFar = exactTimes(a, [a, b], 420, 719);
  assert.ok('error' in tooFar && /5 Minuten/.test(tooFar.error));
});

test('Nudge und Tagwechsel', () => {
  const a = block(1, 420, 540);
  const n = nudge(a, [a], 'end', 15);
  assert.equal((n as { intent: { newEnd: number } }).intent.newEnd, 555);
  assert.ok('error' in nudge(a, [a], 'start', -500));
  const other = block(3, 400, 600);
  assert.ok('error' in toDay(a, [a, other], 3, false));
  assert.ok('error' in toDay(a, [a], 1, false));
  assert.deepEqual(toDay(a, [a], 1, true), { error: 'Hier ist bereits eine Zeit geplant.' });
  assert.deepEqual(toDay(a, [a], 2, true), {
    intent: { type: 'duplicate', block: a, toWeekday: 2, toStart: 420 },
  });
});

test('Ghost und Auswahl nach der Absicht', () => {
  const a = block(2, 420, 540);
  const move: Intent = { type: 'move', block: a, toWeekday: 4, toStart: 600 };
  assert.deepEqual(ghostOf(move), { weekday: 4, start: 600, end: 720, source: a, label: 'Wird gespeichert' });
  const dup = ghostOf({ type: 'duplicate', block: a, toWeekday: 4, toStart: 600 });
  assert.equal(dup?.source, null);
  assert.equal(ghostOf({ type: 'copyDay', weekday: 1, scope: 'all' }), null);
  assert.equal(ghostOf({ type: 'delete', block: a })?.label, 'Wird entfernt');
  assert.deepEqual(selectionAfter(move), { weekday: 4, start: 600, end: 720 });
  assert.deepEqual(selectionAfter({ type: 'resize', block: a, newStart: 400, newEnd: 560, adjacent: false }), {
    weekday: 2,
    start: 400,
    end: 560,
  });
  assert.equal(selectionAfter({ type: 'delete', block: a }), null);
  assert.equal(isNoop({ type: 'move', block: a, toWeekday: 2, toStart: 420 }), true);
  assert.equal(isNoop({ type: 'resize', block: a, newStart: 420, newEnd: 540, adjacent: false }), true);
  assert.equal(isNoop(move), false);
  assert.match(describeIntent(move), /Verschoben nach Donnerstag, 10:00 bis 12:00/);
  assert.match(describeIntent({ type: 'copyDay', weekday: 6, scope: 'workdays' }), /Samstag kopiert \(Montag bis Freitag\)/);
  assert.match(describeIntent({ type: 'delete', block: a }), /Dienstag, 07:00 bis 09:00/);
});

test('Vorschau: Anteile summieren sich auf 100 und die Uhrzeit bleibt lokal', () => {
  const seg = (d: number) => ({
    starts_at: '',
    ends_at: '',
    local_starts_at: '2026-10-16T14:30:00+02:00',
    duration_minutes: d,
    temperature_c: '20',
    reason: 'plan',
    mode_id: 1,
    mode_name: 'Tag',
    warmth: 0.5,
  });
  const parts = stripParts([seg(360), seg(0), seg(1080)]);
  assert.equal(parts.length, 2);
  assert.equal(Math.round(parts[0].width + parts[1].width), 100);
  assert.equal(Math.round(parts[1].left), 25);
  assert.deepEqual(stripParts([]), []);
  assert.equal(localClock('2026-10-16T14:30:00+02:00'), '14:30');
  assert.equal(localClock('kaputt'), '—');
});

test('Zeitzone mit Namen der Jahreszeit', () => {
  assert.match(zoneLabel('Europe/Berlin', new Date('2026-07-01T12:00:00Z')), /Europe\/Berlin · (MESZ|GMT\+2)/);
  assert.match(zoneLabel('Europe/Berlin', new Date('2026-01-01T12:00:00Z')), /Europe\/Berlin · (MEZ|GMT\+1)/);
  assert.equal(zoneLabel('Kein/Ort'), 'Kein/Ort');
});
