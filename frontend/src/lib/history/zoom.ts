// Zustandsmaschine des Zoomens. Ohne Svelte, damit sie getestet werden kann.
//
// Zoomen geschieht NUR durch Ziehen mit der Maus bei feinem Zeiger. Touch und Stift erzeugen
// nie eine Zoom-Auswahl: Wischen soll die Seite scrollen. Die Stufen sind verschachtelt
// (jede liegt innerhalb der vorigen); "Zurück" nimmt die letzte weg, "Zurücksetzen" alle.

export interface Range {
  from: number;
  to: number;
}

export interface Drag {
  pointerId: number;
  originX: number;
  x: number;
  t0: number;
  t1: number;
  moved: boolean;
}

export interface ZoomState {
  /** Schlüssel aus Raum und Zeitraum; ein anderer Schlüssel verwirft alle Stufen. */
  key: string;
  stack: Range[];
  drag: Drag | null;
}

export interface PointerInfo {
  pointerId: number;
  pointerType: string;
  isPrimary: boolean;
  button: number;
  /** `matchMedia('(pointer: fine)')` -- ein Gerät mit grobem Zeiger zoomt auch mit Maus-Ereignissen nicht. */
  finePointer: boolean;
}

/** Pixel, ab denen eine Bewegung als Ziehen gilt (darunter ist es ein Klick). */
export const DRAG_THRESHOLD_PX = 6;

export const initialZoom = (key = ''): ZoomState => ({ key, stack: [], drag: null });

export function canStartZoom(p: PointerInfo): boolean {
  return p.pointerType === 'mouse' && p.finePointer && p.isPrimary && p.button === 0;
}

export function setKey(state: ZoomState, key: string): ZoomState {
  return state.key === key ? state : initialZoom(key);
}

export function currentView(state: ZoomState, base: Range): Range {
  return state.stack.length ? state.stack[state.stack.length - 1] : base;
}

export function startDrag(state: ZoomState, p: PointerInfo, x: number, t: number): ZoomState {
  if (!canStartZoom(p)) return state;
  return { ...state, drag: { pointerId: p.pointerId, originX: x, x, t0: t, t1: t, moved: false } };
}

export function moveDrag(state: ZoomState, p: PointerInfo, x: number, t: number): ZoomState {
  const drag = state.drag;
  if (!drag || drag.pointerId !== p.pointerId) return state;
  return {
    ...state,
    drag: { ...drag, x, t1: t, moved: drag.moved || Math.abs(x - drag.originX) >= DRAG_THRESHOLD_PX },
  };
}

/** Die laufende Auswahl in beide Richtungen, auf den Ausschnitt begrenzt. */
export function dragSelection(state: ZoomState, view: Range): Range | null {
  const d = state.drag;
  if (!d || !d.moved) return null;
  return {
    from: Math.max(view.from, Math.min(d.t0, d.t1)),
    to: Math.min(view.to, Math.max(d.t0, d.t1)),
  };
}

/** Loslassen: eine neue Stufe, wenn gezogen wurde und die Auswahl mindestens `minSpanMs` umfasst
 *  und kleiner als der Ausschnitt ist. */
export function endDrag(state: ZoomState, p: PointerInfo, view: Range, minSpanMs: number): ZoomState {
  const d = state.drag;
  if (!d || d.pointerId !== p.pointerId) return state;
  const selection = dragSelection(state, view);
  const next: ZoomState = { ...state, drag: null };
  if (!selection) return next;
  const span = selection.to - selection.from;
  if (span < minSpanMs || span >= view.to - view.from) return next;
  return { ...next, stack: [...next.stack, selection] };
}

/** Esc und Abbruch (pointercancel): nur die laufende Auswahl wird verworfen. */
export function cancelDrag(state: ZoomState): ZoomState {
  return state.drag ? { ...state, drag: null } : state;
}

export function back(state: ZoomState): ZoomState {
  return state.stack.length ? { ...state, stack: state.stack.slice(0, -1), drag: null } : state;
}

/** „Zurücksetzen“ und Doppelklick: Gesamtzeitraum. */
export function reset(state: ZoomState): ZoomState {
  return state.stack.length || state.drag ? { ...state, stack: [], drag: null } : state;
}
