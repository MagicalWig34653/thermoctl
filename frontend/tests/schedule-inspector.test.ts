import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clockValue, exactTimes, nudge, parseClock, toDay } from '../src/lib/schedule/inspector.ts';
import { describeIntent, ghostOf, isNoop, range, selectionAfter, type Intent } from '../src/lib/schedule/intents.ts';
import { localClock, stripParts, zoneLabel } from '../src/lib/schedule/format.ts';

import { ring } from './schedule-fixture.ts';

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

// Di 07:00 „Tag“ (120 Minuten), Di 09:00 „Nacht“ (Rest der Woche), Do 12:00 „Komfort“ (bis Di 07:00).
const [tag, nacht, komfort] = ring([
  [2, 420, 1],
  [2, 540, 2],
  [4, 720, 3],
]);
const plan = [tag, nacht, komfort];

test('Exakte Zeiten: eine Kante, im Spielraum, mit gemeinsamer Kante', () => {
  const later = exactTimes(tag, plan, 420, 580);
  assert.deepEqual(later, { intent: { type: 'resize', block: tag, newStart: 420, newEnd: 580, adjacent: true } });
  const earlier = exactTimes(tag, plan, 400, 540);
  assert.deepEqual(earlier, { intent: { type: 'resize', block: tag, newStart: 400, newEnd: 540, adjacent: true } });
  // Beide Kanten auf einmal: der Server ändert immer nur eine.
  assert.ok('error' in exactTimes(tag, plan, 400, 560));
  // Kürzer als 5 Minuten.
  assert.ok('error' in exactTimes(tag, plan, 420, 421));
  assert.ok('error' in exactTimes(tag, plan, 420.5, 560));
  // Das Ende von „Tag“ darf den Nachbarn „Nacht“ bis 5 Minuten vor den Beginn von „Komfort“ (Do 12:00 = 3600 ab Di) verdrängen.
  assert.ok('intent' in exactTimes(tag, plan, 420, 3595));
  const tooFar = exactTimes(tag, plan, 420, 3600);
  assert.ok('error' in tooFar && /5 Minuten/.test(tooFar.error));
});

test('Block über Mitternacht: Ende liegt am Folgetag und zählt über 1440', () => {
  const [late, rest] = ring([
    [2, 1380, 1],
    [3, 360, 2],
  ]);
  assert.equal(late.end, 1380 + 420);
  const ok = exactTimes(late, [late, rest], 1380, 1860);
  assert.ok('intent' in ok && ok.intent.type === 'resize' && ok.intent.newEnd === 1860);
});

test('Nudge und Tagwechsel', () => {
  const n = nudge(tag, plan, 'end', 15);
  assert.equal((n as { intent: { newEnd: number } }).intent.newEnd, 555);
  assert.ok('error' in nudge(tag, plan, 'start', -20000));
  assert.ok('error' in toDay(tag, plan, 2, false)); // bereits dieser Tag
  // Mittwoch 07:00: mitten in der „Nacht“, nichts beginnt dort -> frei.
  assert.deepEqual(toDay(tag, plan, 3, false), { intent: { type: 'move', block: tag, toWeekday: 3, toStart: 420 } });
  // Donnerstag 07:00..09:00: frei (Komfort beginnt erst 12:00); Do 11:30 würde ihn überdecken.
  const [x, y, z] = ring([[2, 690, 1], [2, 810, 2], [4, 720, 3]]);
  assert.deepEqual(toDay(x, [x, y, z], 4, true), { error: 'Dort beginnt bereits ein anderer Zeitbereich.' });
  assert.deepEqual(toDay(tag, plan, 2, true), { error: 'Dort beginnt bereits ein anderer Zeitbereich.' });
  assert.deepEqual(toDay(tag, plan, 6, true), {
    intent: { type: 'duplicate', block: tag, toWeekday: 6, toStart: 420 },
  });
});

test('Ghost und Auswahl nach der Absicht', () => {
  const move: Intent = { type: 'move', block: tag, toWeekday: 5, toStart: 600 };
  assert.deepEqual(ghostOf(move), { weekday: 5, start: 600, end: 720, source: tag, label: 'Wird gespeichert' });
  const dup = ghostOf({ type: 'duplicate', block: tag, toWeekday: 5, toStart: 600 });
  assert.equal(dup?.source, null);
  assert.equal(ghostOf({ type: 'copyDay', weekday: 1, scope: 'all' }), null);
  assert.equal(ghostOf({ type: 'delete', block: tag })?.label, 'Wird entfernt');
  assert.deepEqual(selectionAfter(move), { weekday: 5, start: 600, end: 720 });
  // Vor Mitternacht beginnen: der Beginn rutscht auf den Vortag.
  assert.deepEqual(selectionAfter({ type: 'resize', block: tag, newStart: -60, newEnd: 540, adjacent: true }), {
    weekday: 1,
    start: 1380,
    end: 1380 + 600,
  });
  assert.equal(selectionAfter({ type: 'delete', block: tag }), null);
  assert.deepEqual(selectionAfter({ type: 'setMode', block: tag, pointId: 1, modeId: 2 }), tag);
  assert.equal(isNoop({ type: 'move', block: tag, toWeekday: 2, toStart: 420 }), true);
  assert.equal(isNoop({ type: 'resize', block: tag, newStart: 420, newEnd: 540, adjacent: false }), true);
  assert.equal(isNoop(move), false);
  assert.match(describeIntent(move), /Verschoben nach Freitag, 10:00 bis 12:00/);
  assert.match(describeIntent({ type: 'copyDay', weekday: 6, scope: 'workdays' }), /Samstag kopiert \(Montag bis Freitag\)/);
  assert.match(describeIntent({ type: 'delete', block: tag }), /Dienstag, 07:00 bis 09:00/);
  assert.match(describeIntent({ type: 'delete', block: nacht }), /Dienstag, 09:00 bis Donnerstag 12:00/);
  assert.match(range({ weekday: 7, start: 1380, end: 1500 }), /Sonntag, 23:00 bis Montag 01:00/);
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
