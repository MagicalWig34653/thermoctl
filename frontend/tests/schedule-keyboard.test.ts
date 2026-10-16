import assert from 'node:assert/strict';
import { test } from 'node:test';
import { blockKeyAction, deleteKeyAllowed, historyKey, type KeyLike } from '../src/lib/schedule/keyboard.ts';

import { ring } from './schedule-fixture.ts';

const key = (k: string, extra: Partial<KeyLike> = {}): KeyLike => ({
  key: k,
  altKey: false,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  ...extra,
});
// Di 07:00 „Tag“ (2 h), Di 09:00 „Nacht“ bis zum nächsten Di 07:00.
const [a, night] = ring([
  [2, 420, 1],
  [2, 540, 2],
]);
const blocks = [a, night];

test('Enter und Leertaste öffnen den Inspektor', () => {
  assert.deepEqual(blockKeyAction(key('Enter'), a, blocks), { type: 'open' });
  assert.deepEqual(blockKeyAction(key(' '), a, blocks), { type: 'open' });
  assert.deepEqual(blockKeyAction(key('Tab'), a, blocks), { type: 'none' });
});

test('Pfeile verschieben um 15 Minuten, mit Umschalt um 5, hoch/runter um einen Tag', () => {
  // Nach rechts würde der Block den Beginn seines Nachfolgers überdecken: der Server lehnt das ab.
  assert.equal(blockKeyAction(key('ArrowRight'), a, blocks).type, 'blocked');
  const left = blockKeyAction(key('ArrowLeft', { shiftKey: true }), a, blocks);
  assert.deepEqual(left, { type: 'intent', intent: { type: 'move', block: a, toWeekday: 2, toStart: 415 } });
  const down = blockKeyAction(key('ArrowDown'), a, blocks);
  assert.deepEqual(down, { type: 'intent', intent: { type: 'move', block: a, toWeekday: 3, toStart: 420 } });
  const up = blockKeyAction(key('ArrowUp'), a, blocks);
  assert.equal((up as { intent: { toWeekday: number } }).intent.toWeekday, 1);
});

test('Pfeile klemmen an Wochenrand und Mitternacht (kein Umbruch)', () => {
  const [mon] = ring([[1, 420, 1], [3, 0, 2]]);
  assert.deepEqual(blockKeyAction(key('ArrowUp'), mon, [mon]), { type: 'none' });
  const sun = ring([[7, 420, 1], [3, 0, 2]]).find((b) => b.modeId === 1)!;
  assert.deepEqual(blockKeyAction(key('ArrowDown'), sun, [sun]), { type: 'none' });
  const [first] = ring([[2, 0, 1], [3, 600, 2]]);
  assert.deepEqual(blockKeyAction(key('ArrowLeft'), first, [first]), { type: 'none' });
  const [end] = ring([[2, 1435, 1], [4, 0, 2]]);
  assert.deepEqual(blockKeyAction(key('ArrowRight'), end, [end]), { type: 'none' });
});

test('Kollision beim Verschieben per Tastatur wird gemeldet statt gesendet', () => {
  // Tag (Di 07:00) nach Mittwoch 07:00 verschieben, dort beginnt schon ein anderer Block.
  const [x, y, z] = ring([[2, 420, 1], [2, 540, 2], [3, 420, 3]]);
  const r = blockKeyAction(key('ArrowDown'), x, [x, y, z]);
  assert.deepEqual(r, { type: 'blocked', reason: 'Dort beginnt bereits ein anderer Zeitbereich.' });
  const touching = blockKeyAction(key('ArrowRight'), x, [x, y, z]); // 07:15-09:15 überdeckt den Beginn von „Nacht“ (09:00)
  assert.equal(touching.type, 'blocked');
  // Nach links ist es frei (Di 06:45-08:45 enthält keinen fremden Beginn).
  assert.equal(blockKeyAction(key('ArrowLeft'), x, [x, y, z]).type, 'intent');
});

test('Alt+Pfeile ändern Größe: links/rechts Ende, hoch/runter Beginn; die gemeinsame Kante wandert mit', () => {
  const end = blockKeyAction(key('ArrowRight', { altKey: true }), a, blocks);
  assert.deepEqual(end, {
    type: 'intent',
    intent: { type: 'resize', block: a, newStart: 420, newEnd: 555, adjacent: true },
  });
  const shrink = blockKeyAction(key('ArrowLeft', { altKey: true }), a, blocks);
  assert.equal((shrink as { intent: { newEnd: number } }).intent.newEnd, 525);
  const startUp = blockKeyAction(key('ArrowUp', { altKey: true }), a, blocks);
  assert.equal((startUp as { intent: { newStart: number } }).intent.newStart, 405);
  const startDown = blockKeyAction(key('ArrowDown', { altKey: true }), a, blocks);
  assert.equal((startDown as { intent: { newStart: number } }).intent.newStart, 435);
});

test('Alt+Pfeile bleiben im Spielraum', () => {
  const [tight, rest] = ring([[2, 420, 1], [2, 425, 2]]);
  assert.deepEqual(blockKeyAction(key('ArrowLeft', { altKey: true }), tight, [tight, rest]), { type: 'none' });
  // Die Startkante kann höchstens bis 5 Minuten hinter den vorigen Beginn zurück.
  const [first, second] = ring([[2, 420, 1], [2, 427, 2]]);
  const limited = blockKeyAction(key('ArrowUp', { altKey: true, shiftKey: true }), second, [first, second]);
  assert.equal((limited as { intent: { newStart: number } }).intent.newStart, 425);
});

test('Fortsetzungen über Mitternacht gehören zum Block: Tastenabsicht gilt dem ganzen Block', () => {
  const [late] = ring([[2, 1380, 1], [3, 360, 2]]);
  assert.equal(late.end, 1380 + 420);
  const r = blockKeyAction(key('ArrowDown'), late, [late]);
  assert.equal(r.type, 'intent');
  assert.deepEqual(blockKeyAction(key('Enter'), late, [late]), { type: 'open' });
});

test('Strg/Cmd mit Pfeil ist nicht belegt', () => {
  assert.deepEqual(blockKeyAction(key('ArrowRight', { ctrlKey: true }), a, blocks), { type: 'none' });
  assert.deepEqual(blockKeyAction(key('ArrowRight', { metaKey: true }), a, blocks), { type: 'none' });
});

const target = (matches: boolean) => ({ closest: () => (matches ? {} : null) });
const del = (extra: Record<string, unknown> = {}) => ({
  ...key('Backspace'),
  repeat: false,
  defaultPrevented: false,
  target: target(false),
  ...extra,
});

test('Backspace/Entf löscht -- außer in Eingabe, Auswahl, editierbarem Inhalt oder Dialog', () => {
  assert.equal(deleteKeyAllowed(del()), true);
  assert.equal(deleteKeyAllowed(del({ key: 'Delete' })), true);
  assert.equal(deleteKeyAllowed(del({ target: target(true) })), false);
  assert.equal(deleteKeyAllowed(del({ target: null })), true);
  assert.equal(deleteKeyAllowed(del({ key: 'a' })), false);
  assert.equal(deleteKeyAllowed(del({ repeat: true })), false);
  assert.equal(deleteKeyAllowed(del({ defaultPrevented: true })), false);
  assert.equal(deleteKeyAllowed(del({ ctrlKey: true })), false);
  assert.equal(deleteKeyAllowed(del({ metaKey: true })), false);
  assert.equal(deleteKeyAllowed(del({ altKey: true })), false);
  assert.equal(deleteKeyAllowed(del({ shiftKey: true })), false);
});

test('Der Auswahlstring für Textkontexte nennt alle geforderten Fälle', async () => {
  const { TEXT_CONTEXT } = await import('../src/lib/schedule/keyboard.ts');
  for (const part of ['input', 'select', 'textarea', 'contenteditable', '[role="dialog"]']) {
    assert.ok(TEXT_CONTEXT.includes(part), part);
  }
});

test('Strg/Cmd+Z nimmt zurück, mit Umschalt wiederholt; nicht in Textfeldern', () => {
  const k = (extra: Partial<KeyLike>, matches = false) => ({ ...key('z', extra), target: target(matches) });
  assert.equal(historyKey(k({ ctrlKey: true })), 'undo');
  assert.equal(historyKey(k({ metaKey: true })), 'undo');
  assert.equal(historyKey(k({ metaKey: true, shiftKey: true })), 'redo');
  assert.equal(historyKey({ ...k({ ctrlKey: true, shiftKey: true }), key: 'Z' }), 'redo');
  assert.equal(historyKey(k({})), null);
  assert.equal(historyKey(k({ ctrlKey: true, altKey: true })), null);
  assert.equal(historyKey(k({ ctrlKey: true }, true)), null);
  assert.equal(historyKey({ ...k({ ctrlKey: true }), key: 'y' }), null);
  assert.equal(historyKey({ ...k({ ctrlKey: true }), target: null }), 'undo');
});
