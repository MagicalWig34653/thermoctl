import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  baseModeId,
  blockAt,
  blockCovering,
  blocksOf,
  collision,
  dayAt,
  drawShape,
  edgeLimits,
  gapAround,
  minuteAt,
  moveShape,
  percentOf,
  resizeShape,
  snapMinute,
  type Block,
} from '../src/lib/schedule/geometry.ts';
import type { PlanMode, PlanSegment } from '../src/lib/schedule/types.ts';

const modes: PlanMode[] = [
  { id: 1, code: 'day', name: 'Tag', temperature_c: '21.0', warmth: 0.8 },
  { id: 2, code: 'night', name: 'Nacht', temperature_c: '17.0', warmth: 0.2 },
  { id: 3, code: 'away', name: 'Abwesend', temperature_c: '15.0', warmth: 0.1 },
];

const seg = (weekday: number, s: number, e: number, mode: number, point: number | null = 1): PlanSegment => ({
  weekday,
  start_minute: s,
  end_minute: e,
  mode_id: mode,
  mode_name: modes.find((m) => m.id === mode)!.name,
  point_id: point,
  temperature_c: '21.0',
  warmth: 0.5,
});

const block = (weekday: number, start: number, end: number, carry = false): Block => ({
  weekday,
  start,
  end,
  modeId: 1,
  modeName: 'Tag',
  warmth: 0.8,
  temperatureC: '21.0',
  carry,
});

test('Pixel werden in Minuten umgerechnet, begrenzt und eingerastet', () => {
  assert.equal(minuteAt(100, 100, 1440), 0);
  assert.equal(minuteAt(100 + 720, 100, 1440), 720);
  assert.equal(minuteAt(-50, 100, 1440), 0);
  assert.equal(minuteAt(5000, 100, 1440), 1440);
  assert.equal(minuteAt(10, 0, 0), 0); // keine Breite: keine Division durch null
  assert.equal(snapMinute(7), 5);
  assert.equal(snapMinute(8), 10);
  assert.equal(snapMinute(1438), 1440);
  assert.equal(percentOf(720), 50);
  assert.equal(percentOf(2000), 100);
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

test('Grundmodus ist der kälteste, bei Gleichstand der erste', () => {
  assert.equal(baseModeId(modes), 3);
  assert.equal(baseModeId([modes[0], { ...modes[1], warmth: 0.8 }]), 1);
  assert.equal(baseModeId([]), null);
});

test('Blöcke sind alle Balken außer dem Grundmodus; Reste vom Vortag sind als solche markiert', () => {
  const blocks = blocksOf(
    [seg(2, 0, 360, 1, null), seg(2, 360, 1440, 2), seg(1, 420, 540, 1), seg(1, 0, 420, 2)],
    2,
  );
  assert.deepEqual(
    blocks.map((b) => [b.weekday, b.start, b.end, b.carry]),
    [
      [1, 420, 540, false],
      [2, 0, 360, true],
    ],
  );
  assert.equal(blocksOf([seg(1, 100, 100, 1)], 2).length, 0);
});

test('Lücken und Kollision', () => {
  const blocks = [block(1, 420, 540), block(1, 600, 720), block(2, 0, 100)];
  assert.deepEqual(gapAround(blocks, 1, 100), { start: 0, end: 420 });
  assert.deepEqual(gapAround(blocks, 1, 560), { start: 540, end: 600 });
  assert.deepEqual(gapAround(blocks, 1, 800), { start: 720, end: 1440 });
  assert.equal(gapAround(blocks, 1, 430), null);
  assert.deepEqual(gapAround(blocks, 3, 10), { start: 0, end: 1440 });
  assert.equal(collision(blocks, 1, 530, 560), blocks[0]);
  assert.equal(collision(blocks, 1, 540, 600), null); // Berührung ist keine Überschneidung
  assert.equal(collision(blocks, 1, 420, 540, blocks[0]), null); // sich selbst ignorieren
  assert.equal(blockAt(blocks, 1, 539)?.start, 420);
  assert.equal(blockAt(blocks, 1, 540), null);
  assert.equal(blockCovering(blocks, 1, 430, 500)?.start, 420);
  assert.equal(blockCovering(blocks, 1, 430, 560), null);
});

test('Ziehen auf freier Fläche: auf die Lücke begrenzt, Ziehrichtung egal', () => {
  const blocks = [block(1, 420, 540), block(1, 600, 720)];
  const s = drawShape(blocks, 1, 545, 590, false);
  assert.deepEqual([s.start, s.end, s.valid], [545, 590, true]);
  const back = drawShape(blocks, 1, 590, 545, false);
  assert.deepEqual([back.start, back.end], [545, 590]);
  const clipped = drawShape(blocks, 1, 550, 900, false);
  assert.deepEqual([clipped.start, clipped.end, clipped.valid], [550, 600, true]);
  const onBlock = drawShape(blocks, 1, 430, 500, false);
  assert.equal(onBlock.valid, false);
  assert.match(onBlock.reason, /bereits/);
  assert.equal(drawShape(blocks, 1, 550, 551, false).valid, false); // zu kurz
});

test('Umschalt übermalt vorhandene Bereiche', () => {
  const blocks = [block(1, 420, 540)];
  const s = drawShape(blocks, 1, 430, 600, true);
  assert.deepEqual([s.start, s.end, s.valid], [430, 600, true]);
});

test('Ziehen bis Mitternacht', () => {
  const s = drawShape([], 1, 1300, 1440, false);
  assert.deepEqual([s.start, s.end, s.valid], [1300, 1440, true]);
  assert.equal(drawShape([], 1, 1439, 1440, false).valid, false);
  assert.equal(drawShape([], 1, 0, 0, false).valid, false);
});

test('Verschieben: Kollision macht die Vorschau ungültig, die Quelle blockiert sich nicht selbst', () => {
  const a = block(1, 420, 540);
  const b = block(1, 600, 720);
  const blocks = [a, b];
  const ok = moveShape(a, blocks, 500, 30, 1, false);
  assert.deepEqual([ok.start, ok.end, ok.valid], [470, 590, true]);
  const hit = moveShape(a, blocks, 600, 0, 1, false);
  assert.equal(hit.valid, false);
  assert.equal(hit.reason, 'Hier ist bereits eine Zeit geplant.');
  const sameSpot = moveShape(a, blocks, 450, 30, 1, false);
  assert.equal(sameSpot.valid, true);
  const day2 = moveShape(a, blocks, 600, 0, 2, false);
  assert.deepEqual([day2.weekday, day2.valid], [2, true]);
});

test('Duplizieren: die Quelle bleibt Hindernis', () => {
  const a = block(1, 420, 540);
  const onSelf = moveShape(a, [a], 440, 20, 1, true);
  assert.equal(onSelf.copy, true);
  assert.equal(onSelf.valid, false);
  const free = moveShape(a, [a], 700, 0, 1, true);
  assert.equal(free.valid, true);
  assert.equal(free.copy, true);
});

test('Verschieben klemmt an Mitternacht und am Tagesbeginn', () => {
  const a = block(1, 420, 540);
  const late = moveShape(a, [a], 1440, 0, 3, false);
  assert.deepEqual([late.start, late.end], [1320, 1440]);
  const early = moveShape(a, [a], 0, 60, 3, false);
  assert.deepEqual([early.start, early.end], [0, 120]);
  const odd = block(1, 0, 70);
  const lateOdd = moveShape(odd, [odd], 1440, 0, 3, false);
  assert.equal(lateOdd.end, 1440 - ((1440 - 70) % 5));
});

test('Kanten: freie Nachbarschaft begrenzt auf den nächsten Block', () => {
  const a = block(1, 420, 540);
  const b = block(1, 600, 720);
  const left = edgeLimits(a, 'start', [a, b]);
  assert.deepEqual([left.min, left.max, left.neighbor], [0, 535, null]);
  const right = edgeLimits(a, 'end', [a, b]);
  assert.deepEqual([right.min, right.max, right.neighbor], [425, 600, null]);
  const last = edgeLimits(b, 'end', [a, b]);
  assert.equal(last.max, 1440);
});

test('Angrenzende Modusgrenzen wandern gemeinsam (adjacent)', () => {
  const a = block(1, 420, 540);
  const b = { ...block(1, 540, 720), modeId: 2, modeName: 'Nacht' };
  const blocks = [a, b];
  const s = resizeShape(a, 'end', blocks, 600);
  assert.deepEqual([s.start, s.end, s.adjacent, s.valid], [420, 600, true, true]);
  const limit = resizeShape(a, 'end', blocks, 5000);
  assert.equal(limit.end, 715); // der Nachbar behält 5 Minuten
  const noChange = resizeShape(a, 'end', blocks, 540);
  assert.equal(noChange.adjacent, false);
  const free = resizeShape(b, 'end', blocks, 800);
  assert.deepEqual([free.end, free.adjacent], [800, false]);
  const toMidnight = resizeShape(b, 'end', blocks, 1440);
  assert.equal(toMidnight.end, 1440);
  const tooShort = resizeShape(a, 'end', blocks, 421);
  assert.equal(tooShort.end, 425);
  const startEdge = resizeShape(b, 'start', blocks, 0);
  assert.deepEqual([startEdge.start, startEdge.adjacent], [425, true]);
});
