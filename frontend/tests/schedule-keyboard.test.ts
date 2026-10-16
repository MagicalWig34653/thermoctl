import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Block } from '../src/lib/schedule/geometry.ts';
import { blockKeyAction, deleteKeyAllowed, historyKey, type KeyLike } from '../src/lib/schedule/keyboard.ts';

const block = (weekday: number, start: number, end: number, carry = false): Block => ({
  weekday,
  start,
  end,
  modeId: 1,
  modeName: 'Tag',
  warmth: 0.8,
  temperatureC: null,
  carry,
});
const key = (k: string, extra: Partial<KeyLike> = {}): KeyLike => ({
  key: k,
  altKey: false,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  ...extra,
});
const a = block(2, 420, 540);

test('Enter und Leertaste öffnen den Inspektor', () => {
  assert.deepEqual(blockKeyAction(key('Enter'), a, [a]), { type: 'open' });
  assert.deepEqual(blockKeyAction(key(' '), a, [a]), { type: 'open' });
  assert.deepEqual(blockKeyAction(key('Tab'), a, [a]), { type: 'none' });
});

test('Pfeile verschieben um 15 Minuten, mit Umschalt um 5, hoch/runter um einen Tag', () => {
  const right = blockKeyAction(key('ArrowRight'), a, [a]);
  assert.deepEqual(right, { type: 'intent', intent: { type: 'move', block: a, toWeekday: 2, toStart: 435 } });
  const left = blockKeyAction(key('ArrowLeft', { shiftKey: true }), a, [a]);
  assert.deepEqual(left, { type: 'intent', intent: { type: 'move', block: a, toWeekday: 2, toStart: 415 } });
  const down = blockKeyAction(key('ArrowDown'), a, [a]);
  assert.deepEqual(down, { type: 'intent', intent: { type: 'move', block: a, toWeekday: 3, toStart: 420 } });
  const up = blockKeyAction(key('ArrowUp'), a, [a]);
  assert.equal((up as { intent: { toWeekday: number } }).intent.toWeekday, 1);
});

test('Pfeile klemmen an Wochenrand und Mitternacht (kein Umbruch)', () => {
  assert.deepEqual(blockKeyAction(key('ArrowUp'), block(1, 420, 540), []), { type: 'none' });
  assert.deepEqual(blockKeyAction(key('ArrowDown'), block(7, 420, 540), []), { type: 'none' });
  assert.deepEqual(blockKeyAction(key('ArrowLeft'), block(2, 0, 60), []), { type: 'none' });
  const late = block(2, 1380, 1440);
  assert.deepEqual(blockKeyAction(key('ArrowRight'), late, [late]), { type: 'none' });
  const nearEnd = block(2, 1370, 1430);
  const r = blockKeyAction(key('ArrowRight'), nearEnd, [nearEnd]);
  assert.equal((r as { intent: { toStart: number } }).intent.toStart, 1380);
  const early = block(2, 5, 65);
  const l = blockKeyAction(key('ArrowLeft'), early, [early]);
  assert.equal((l as { intent: { toStart: number } }).intent.toStart, 0);
});

test('Kollision beim Verschieben per Tastatur wird gemeldet statt gesendet', () => {
  const other = block(3, 400, 600);
  const r = blockKeyAction(key('ArrowDown'), a, [a, other]);
  assert.deepEqual(r, { type: 'blocked', reason: 'Hier ist bereits eine Zeit geplant.' });
  const neighbor = block(2, 540, 600);
  const touching = blockKeyAction(key('ArrowRight', { shiftKey: true }), a, [a, neighbor]);
  assert.equal(touching.type, 'blocked');
});

test('Alt+Pfeile ändern Größe: links/rechts Ende, hoch/runter Beginn', () => {
  const end = blockKeyAction(key('ArrowRight', { altKey: true }), a, [a]);
  assert.deepEqual(end, {
    type: 'intent',
    intent: { type: 'resize', block: a, newStart: 420, newEnd: 555, adjacent: false },
  });
  const shrink = blockKeyAction(key('ArrowLeft', { altKey: true }), a, [a]);
  assert.equal((shrink as { intent: { newEnd: number } }).intent.newEnd, 525);
  const startUp = blockKeyAction(key('ArrowUp', { altKey: true }), a, [a]);
  assert.equal((startUp as { intent: { newStart: number } }).intent.newStart, 405);
  const startDown = blockKeyAction(key('ArrowDown', { altKey: true }), a, [a]);
  assert.equal((startDown as { intent: { newStart: number } }).intent.newStart, 435);
});

test('Alt+Pfeile bleiben im Spielraum; angrenzende Bereiche wandern gemeinsam', () => {
  const next = { ...block(2, 540, 600), modeId: 2 };
  const grow = blockKeyAction(key('ArrowRight', { altKey: true }), a, [a, next]);
  assert.deepEqual((grow as { intent: unknown }).intent, {
    type: 'resize',
    block: a,
    newStart: 420,
    newEnd: 555,
    adjacent: true,
  });
  const tight = block(2, 420, 425);
  assert.deepEqual(blockKeyAction(key('ArrowLeft', { altKey: true }), tight, [tight]), { type: 'none' });
  const late = block(2, 1400, 1440);
  assert.deepEqual(blockKeyAction(key('ArrowRight', { altKey: true }), late, [late]), { type: 'none' });
  const wall = block(2, 560, 600);
  const limited = blockKeyAction(key('ArrowUp', { altKey: true, shiftKey: true }), wall, [wall, { ...a, end: 555 }]);
  assert.equal((limited as { intent: { newStart: number } }).intent.newStart, 555);
});

test('Reste vom Vortag lassen sich nicht per Tastatur ändern', () => {
  const carry = block(2, 0, 360, true);
  assert.equal(blockKeyAction(key('ArrowRight'), carry, [carry]).type, 'blocked');
  assert.deepEqual(blockKeyAction(key('Enter'), carry, [carry]), { type: 'open' });
});

test('Strg/Cmd mit Pfeil ist nicht belegt', () => {
  assert.deepEqual(blockKeyAction(key('ArrowRight', { ctrlKey: true }), a, [a]), { type: 'none' });
  assert.deepEqual(blockKeyAction(key('ArrowRight', { metaKey: true }), a, [a]), { type: 'none' });
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
