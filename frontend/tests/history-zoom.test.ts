import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  back,
  cancelDrag,
  canStartZoom,
  currentView,
  DRAG_THRESHOLD_PX,
  dragSelection,
  endDrag,
  initialZoom,
  moveDrag,
  reset,
  setKey,
  startDrag,
  type PointerInfo,
} from '../src/lib/history/zoom.ts';

const mouse: PointerInfo = { pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, finePointer: true };
const touch: PointerInfo = { ...mouse, pointerId: 2, pointerType: 'touch' };
const pen: PointerInfo = { ...mouse, pointerId: 3, pointerType: 'pen' };
const base = { from: 0, to: 1_000_000 };
const MIN = 60_000;

function drag(state = initialZoom('k'), p = mouse, x0 = 100, t0 = 200_000, x1 = 300, t1 = 600_000, view = base) {
  let s = startDrag(state, p, x0, t0);
  s = moveDrag(s, p, x1, t1);
  return endDrag(s, p, view, MIN);
}

test('Maus mit feinem Zeiger zoomt durch Ziehen', () => {
  const s = drag();
  assert.deepEqual(s.stack, [{ from: 200_000, to: 600_000 }]);
  assert.equal(s.drag, null);
});

test('Touch und Stift erzeugen nie eine Zoom-Auswahl, auch nicht bei langer Wischbewegung', () => {
  for (const p of [touch, pen]) {
    assert.equal(canStartZoom(p), false);
    let s = startDrag(initialZoom('k'), p, 10, 100);
    assert.equal(s.drag, null);
    s = moveDrag(s, p, 400, 900_000);
    assert.equal(dragSelection(s, base), null);
    s = endDrag(s, p, base, MIN);
    assert.deepEqual(s.stack, []);
  }
});

test('Maus-Ereignisse bei grobem Zeiger zoomen nicht; ebenso nichtprimäre und andere Tasten', () => {
  assert.equal(canStartZoom({ ...mouse, finePointer: false }), false);
  assert.equal(canStartZoom({ ...mouse, isPrimary: false }), false);
  assert.equal(canStartZoom({ ...mouse, button: 2 }), false);
});

test('rückwärts ziehen ergibt dieselbe Auswahl', () => {
  const s = drag(initialZoom('k'), mouse, 300, 600_000, 100, 200_000);
  assert.deepEqual(s.stack, [{ from: 200_000, to: 600_000 }]);
});

test('unter der Schwelle ist es ein Klick: keine Stufe', () => {
  const s = drag(initialZoom('k'), mouse, 100, 200_000, 100 + DRAG_THRESHOLD_PX - 1, 400_000);
  assert.deepEqual(s.stack, []);
});

test('zu kleine Auswahl (unter der Mindestspanne) wird verworfen', () => {
  const s = drag(initialZoom('k'), mouse, 100, 200_000, 200, 200_000 + MIN - 1);
  assert.deepEqual(s.stack, []);
});

test('Auswahl über den ganzen Ausschnitt ändert nichts; Auswahl wird auf den Ausschnitt begrenzt', () => {
  assert.deepEqual(drag(initialZoom('k'), mouse, 0, -5, 500, 2_000_000).stack, []);
  let s = startDrag(initialZoom('k'), mouse, 100, 200_000);
  s = moveDrag(s, mouse, 500, 5_000_000);
  assert.deepEqual(dragSelection(s, base), { from: 200_000, to: 1_000_000 });
});

test('verschachtelte Stufen: jede liegt im aktuellen Ausschnitt, Zurück nimmt eine weg', () => {
  let s = drag();
  const first = currentView(s, base);
  s = drag(s, mouse, 50, 300_000, 250, 400_000, first);
  assert.deepEqual(s.stack, [{ from: 200_000, to: 600_000 }, { from: 300_000, to: 400_000 }]);
  assert.deepEqual(currentView(s, base), { from: 300_000, to: 400_000 });
  s = back(s);
  assert.deepEqual(currentView(s, base), { from: 200_000, to: 600_000 });
  s = back(s);
  assert.deepEqual(currentView(s, base), base);
  assert.equal(back(s), s); // nichts mehr zurück
});

test('Zurücksetzen und Doppelklick: Gesamtzeitraum, auch aus tiefer Stufe', () => {
  let s = drag();
  s = drag(s, mouse, 50, 300_000, 250, 400_000, currentView(s, base));
  s = reset(s);
  assert.deepEqual(s.stack, []);
  assert.equal(reset(s), s);
});

test('Esc bricht die laufende Auswahl ab, lässt bestehende Stufen stehen', () => {
  let s = drag();
  s = startDrag(s, mouse, 10, 250_000);
  s = moveDrag(s, mouse, 90, 300_000);
  s = cancelDrag(s);
  assert.equal(s.drag, null);
  assert.equal(s.stack.length, 1);
  assert.equal(cancelDrag(s), s);
});

test('Raum- oder Zeitraumwechsel (anderer Schlüssel) verwirft Zoom und Auswahl', () => {
  let s = drag();
  s = startDrag(s, mouse, 10, 250_000);
  const same = setKey(s, 'k');
  assert.equal(same, s);
  const other = setKey(s, 'k2');
  assert.deepEqual(other, { key: 'k2', stack: [], drag: null });
});

test('Bewegung eines fremden Zeigers verändert die Auswahl nicht', () => {
  let s = startDrag(initialZoom('k'), mouse, 100, 200_000);
  const before = s;
  s = moveDrag(s, { ...mouse, pointerId: 9 }, 400, 900_000);
  assert.equal(s, before);
  assert.equal(endDrag(s, { ...mouse, pointerId: 9 }, base, MIN), s);
});
