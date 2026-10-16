import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  absOf,
  blockAt,
  blockStartingAt,
  blocksOf,
  collision,
  dayAt,
  drawShape,
  edgeLimits,
  minuteAt,
  moveShape,
  normalizeRef,
  percentOf,
  resizeShape,
  snapMinute,
  splitRange,
  weekMinute,
  type Block,
} from '../src/lib/schedule/geometry.ts';
import type { PlanSegment } from '../src/lib/schedule/types.ts';

const seg = (
  weekday: number,
  s: number,
  e: number,
  mode: number,
  point: number | null,
): PlanSegment => ({
  weekday,
  start_minute: s,
  end_minute: e,
  mode_id: mode,
  mode_name: `M${mode}`,
  point_id: point,
  temperature_c: '21.0',
  warmth: 0.5,
});

/** Block ohne Server: Tagesstücke aus dem Bereich. */
const block = (weekday: number, start: number, end: number, modeId = 1): Block => ({
  weekday,
  start,
  end,
  pointId: weekday * 10000 + start,
  modeId,
  modeName: `M${modeId}`,
  warmth: 0.5,
  temperatureC: '21.0',
  pieces: splitRange(weekday, start, end),
});

/** Genau das, was `week_segments` des echten Servers für „Mo 06:00 Tag, Mo 08:00 Nacht“ liefert. */
const DUO: PlanSegment[] = [
  seg(1, 0, 360, 2, null),
  seg(1, 360, 480, 1, 11),
  seg(1, 480, 1440, 2, 12),
  ...[2, 3, 4, 5, 6, 7].map((d) => seg(d, 0, 1440, 2, null)),
];

test('Pixel werden in Minuten umgerechnet, begrenzt und eingerastet', () => {
  assert.equal(minuteAt(100, 100, 1440), 0);
  assert.equal(minuteAt(100 + 720, 100, 1440), 720);
  assert.equal(minuteAt(-50, 100, 1440), 0);
  assert.equal(minuteAt(5000, 100, 1440), 1440);
  assert.equal(minuteAt(10, 0, 0), 0);
  assert.equal(snapMinute(7), 5);
  assert.equal(snapMinute(8), 10);
  assert.equal(percentOf(720), 50);
  assert.equal(percentOf(-3), 0);
});

test('Tagesspur unter dem Zeiger', () => {
  const rows = [
    { weekday: 1, top: 0, bottom: 40 },
    { weekday: 2, top: 44, bottom: 84 },
  ];
  assert.equal(dayAt(10, rows), 1);
  assert.equal(dayAt(60, rows), 2);
  assert.equal(dayAt(42, rows), null);
});

test('Woche als Ring: Minuten, Normalisierung, Schnitt in Tagesstücke', () => {
  assert.equal(weekMinute(1, 0), 0);
  assert.equal(weekMinute(7, 1439), 10079);
  assert.equal(weekMinute(1, -60), 10020); // vor Montag 00:00 ist Sonntag 23:00
  assert.deepEqual(normalizeRef(1, -60, 60), { weekday: 7, start: 1380, end: 1500 });
  assert.deepEqual(normalizeRef(2, 1500, 1600), { weekday: 3, start: 60, end: 160 });
  assert.deepEqual(
    splitRange(7, 1320, 1560).map((p) => [p.weekday, p.start, p.end, p.first, p.last]),
    [
      [7, 1320, 1440, true, false],
      [1, 0, 120, false, true],
    ],
  );
  assert.equal(splitRange(1, 0, 10080).length, 7);
  assert.equal(absOf({ weekday: 3, start: 30, end: 40 }), 2 * 1440 + 30);
});

test('Blöcke sind die Läufe zwischen Schaltpunkten -- der Server-Plan hat keine Lücken', () => {
  const blocks = blocksOf(DUO);
  assert.deepEqual(
    blocks.map((b) => [b.weekday, b.start, b.end, b.pointId, b.modeId]),
    [
      [1, 360, 480, 11, 1],
      [1, 480, 10440, 12, 2], // Nacht läuft über die ganze Woche bis Montag 06:00 (9960 Minuten)
    ],
  );
  const night = blocks[1];
  assert.equal(night.pieces.length, 8); // Mo-Rest, Di..So (6), Mo-Anfang
  assert.deepEqual(night.pieces[0], { weekday: 1, start: 480, end: 1440, first: true, last: false });
  assert.deepEqual(night.pieces[7], { weekday: 1, start: 0, end: 360, first: false, last: true });
  // Summe aller Stücke = Woche: nichts erfunden, nichts verloren.
  const covered = blocks.flatMap((b) => b.pieces).reduce((sum, p) => sum + p.end - p.start, 0);
  assert.equal(covered, 10080);
  assert.deepEqual(blocksOf([]), []);
});

test('Ein einzelner Schaltpunkt besitzt die ganze Woche', () => {
  const only = blocksOf([seg(1, 0, 360, 3, null), seg(1, 360, 1440, 3, 5), ...[2, 3, 4, 5, 6, 7].map((d) => seg(d, 0, 1440, 3, null))]);
  assert.equal(only.length, 1);
  assert.equal(only[0].end - only[0].start, 10080);
  assert.equal(only[0].pieces.reduce((n, p) => n + p.end - p.start, 0), 10080);
});

test('Block über Mitternacht und Wochennaht', () => {
  const plan = [
    seg(1, 0, 60, 1, null),
    seg(1, 60, 1440, 2, 1),
    ...[2, 3, 4, 5, 6].map((d) => seg(d, 0, 1440, 2, null)),
    seg(7, 0, 1320, 2, null),
    seg(7, 1320, 1440, 1, 2),
  ];
  const blocks = blocksOf(plan);
  const late = blocks.find((b) => b.modeId === 1)!;
  assert.deepEqual([late.weekday, late.start, late.end], [7, 1320, 1320 + 120 + 60]);
  assert.deepEqual(late.pieces.map((p) => [p.weekday, p.start, p.end]), [[7, 1320, 1440], [1, 0, 60]]);
  assert.equal(blockAt(blocks, 1, 30), late);
  assert.equal(blockAt(blocks, 1, 60)?.modeId, 2);
  assert.equal(blockStartingAt(blocks, { weekday: 7, start: 1320, end: 0 }), late);
});

test('Kollision: besetzt heißt, dass im Zielbereich ein anderer Block BEGINNT', () => {
  const blocks = blocksOf(DUO);
  const [tag, nacht] = blocks;
  // Mitten in der Nacht: kein Beginn -> frei, auch über Tage.
  assert.equal(collision(blocks, 3, 600, 700), null);
  assert.equal(collision(blocks, 3, 600, 600 + 3000), null);
  // Der Bereich enthält den Beginn von „Tag“ (Mo 06:00).
  assert.equal(collision(blocks, 1, 300, 400), tag);
  assert.equal(collision(blocks, 1, 360, 400), tag); // genau auf dem Beginn
  assert.equal(collision(blocks, 1, 250, 360), null); // Ende ist ausgeschlossen
  assert.equal(collision(blocks, 1, 360, 480, tag), null); // sich selbst ignorieren
  // Über die Wochennaht: Sonntag 23:00 bis Montag 06:30 enthält Mo 06:00.
  assert.equal(collision(blocks, 7, 1380, 1380 + 450), tag);
  assert.equal(collision(blocks, 1, 480, 500), nacht);
});

test('Ziehen auf dem Plan teilt den Block darunter und stößt an den nächsten Beginn', () => {
  const blocks = blocksOf(DUO);
  const free = drawShape(blocks, 3, 600, 700, false);
  assert.deepEqual([free.start, free.end, free.valid], [600, 700, true]);
  const back = drawShape(blocks, 3, 700, 600, false);
  assert.deepEqual([back.start, back.end], [600, 700]);
  // Montag: Beginn bei 06:00 und 08:00. Anker nach 08:00 -> frühestens 08:05.
  const after = drawShape(blocks, 1, 600, 300, false);
  assert.deepEqual([after.start, after.end], [485, 600]);
  // Anker vor 06:00: bis 06:00.
  const before = drawShape(blocks, 1, 100, 900, false);
  assert.deepEqual([before.start, before.end, before.valid], [100, 360, true]);
  assert.equal(drawShape(blocks, 3, 550, 551, false).valid, false); // zu kurz
  assert.equal(drawShape(blocks, 3, 1380, 1440, false).end, 1440);
});

test('Umschalt übermalt auch fremde Beginne', () => {
  const blocks = blocksOf(DUO);
  const s = drawShape(blocks, 1, 300, 600, true);
  assert.deepEqual([s.start, s.end, s.valid], [300, 600, true]);
});

test('Verschieben: nur an Stellen, an denen kein anderer Block beginnt', () => {
  const blocks = blocksOf(DUO);
  const tag = blocks[0];
  const ok = moveShape(tag, blocks, 700, 30, 3, false);
  assert.deepEqual([ok.weekday, ok.start, ok.end, ok.valid], [3, 670, 790, true]);
  const onNight = moveShape(tag, blocks, 500, 0, 2, false); // Di 08:20: dort beginnt nichts
  assert.equal(onNight.valid, true);
  const hit = moveShape(tag, blocks, 480, 0, 1, true); // Kopie auf den Beginn der Nacht
  assert.equal(hit.valid, false);
  assert.equal(hit.reason, 'Dort beginnt bereits ein anderer Zeitbereich.');
  // Nach rechts im eigenen Bereich scheitert es am Beginn des Nachfolgers (Server: 409 overlap).
  assert.equal(moveShape(tag, blocks, 400, 0, 1, false).valid, false);
  // Nach links geht es: der eigene Beginn zählt beim Verschieben nicht.
  const self = moveShape(tag, blocks, 340, 0, 1, false);
  assert.deepEqual([self.start, self.end, self.valid], [340, 460, true]);
  const selfCopy = moveShape(tag, blocks, 390, 30, 1, true);
  assert.equal(selfCopy.valid, false); // beim Duplizieren bleibt die Quelle Hindernis
});

test('Verschieben darf über Mitternacht reichen (end > 1440) und klemmt nur den Beginn', () => {
  const blocks = blocksOf(DUO);
  const tag = blocks[0];
  const late = moveShape(tag, blocks, 1440, 0, 3, false);
  assert.deepEqual([late.start, late.end, late.valid], [1435, 1555, true]);
  const early = moveShape(tag, blocks, 0, 60, 3, false);
  assert.equal(early.start, 0);
});

test('Kanten: Start- und Endkante mit gemeinsamer Kante zum Nachbarn', () => {
  const blocks = blocksOf(DUO);
  const [tag, nacht] = blocks;
  const end = edgeLimits(tag, 'end', blocks);
  assert.equal(end.neighbor, nacht);
  assert.equal(end.min, 365);
  // Nachbar „Nacht“ endet erst bei Mo 06:00 der nächsten Woche -> übernächster Beginn ist „Tag“ selbst.
  assert.equal(end.max, 360 + 10080 - 5);
  const start = edgeLimits(tag, 'start', blocks);
  assert.equal(start.neighbor, nacht);
  assert.equal(start.max, 475);
  assert.equal(start.min, 480 - 10080 + 5); // „Nacht“ beginnt 9600 Minuten zuvor, 5 Minuten bleiben ihr
  const nightEnd = edgeLimits(nacht, 'end', blocks);
  assert.equal(nightEnd.neighbor, tag);
});

test('Kanten ziehen: Verlängern nimmt dem Nachbarn weg, Verkleinern gibt die Zeit zurück', () => {
  const blocks = blocksOf(DUO);
  const tag = blocks[0];
  const grow = resizeShape(tag, 'end', blocks, weekMinute(1, 540));
  assert.deepEqual([grow.start, grow.end, grow.adjacent, grow.valid], [360, 540, true, true]);
  const shrink = resizeShape(tag, 'end', blocks, weekMinute(1, 420));
  assert.deepEqual([shrink.end, shrink.adjacent], [420, true]);
  const alone = resizeShape(tag, 'end', blocks, weekMinute(1, 420), true);
  assert.equal(alone.adjacent, false); // Alt: der Rest erbt den Modus davor
  const none = resizeShape(tag, 'end', blocks, weekMinute(1, 480));
  assert.equal(none.adjacent, false);
  // Startkante nach hinten in den Vortag: negativer Beginn relativ zu Montag.
  const early = resizeShape(tag, 'start', blocks, weekMinute(7, 1380));
  assert.deepEqual([early.start, early.end], [-60, 480]);
  const tooShort = resizeShape(tag, 'end', blocks, weekMinute(1, 361));
  assert.equal(tooShort.end, 365);
});
