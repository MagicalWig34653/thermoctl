// Ziehen mit Maus und Finger über Zeigerereignisse am Griff. Der Griff hat `touch-action: none`,
// damit der Finger nicht die Seite scrollt; der Rest der Kachel bleibt zum Scrollen frei.
// Das Ziel wird beim Bewegen aus dem DOM bestimmt (`data-tile-key`, `data-section-key`) und als
// „vor Schlüssel X“ zurückgegeben; die Entscheidung trifft `draft.ts`.

import { pointerBefore } from './keyboard';

export type DragKind = 'tile' | 'section';

export interface DropTarget {
  sectionKey: string;
  beforeKey: string | null;
}

export interface DragCallbacks {
  moveTile(tileKey: string, sectionKey: string, beforeKey: string | null): void;
  moveSection(sectionKey: string, beforeKey: string | null): void;
  announce(text: string): void;
  label(kind: DragKind, key: string): string;
  sectionLabel(sectionKey: string): string;
}

function scrollParent(el: Element | null): Element | null {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY;
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight) return node;
  }
  return null;
}

export function createDrag(cb: DragCallbacks) {
  let active = $state<{ kind: DragKind; key: string } | null>(null);
  let drop = $state<DropTarget | null>(null);
  let sectionDrop = $state<{ beforeKey: string | null } | null>(null);
  let cleanup: (() => void) | null = null;

  function sibling(el: Element, attr: string, skip: string, dir: 'next' | 'previous'): string | null {
    let node = dir === 'next' ? el.nextElementSibling : el.previousElementSibling;
    while (node) {
      const key = (node as HTMLElement).dataset?.[attr];
      if (key && key !== skip) return key;
      node = dir === 'next' ? node.nextElementSibling : node.previousElementSibling;
    }
    return null;
  }

  function locate(x: number, y: number) {
    if (!active) return;
    const hit = document.elementFromPoint(x, y);
    if (active.kind === 'tile') {
      const tileEl = hit?.closest<HTMLElement>('[data-tile-key]');
      const sectionEl = hit?.closest<HTMLElement>('[data-section-key]');
      if (!sectionEl) {
        drop = null;
        return;
      }
      const sectionKey = sectionEl.dataset.sectionKey as string;
      if (tileEl && tileEl.dataset.tileKey !== active.key) {
        const grid = tileEl.parentElement as HTMLElement;
        const before = pointerBefore(tileEl.getBoundingClientRect(), grid.getBoundingClientRect(), x, y);
        drop = {
          sectionKey,
          beforeKey: before ? (tileEl.dataset.tileKey as string) : sibling(tileEl, 'tileKey', active.key, 'next'),
        };
      } else if (tileEl) {
        drop = null; // über sich selbst: keine Änderung
      } else {
        drop = { sectionKey, beforeKey: null };
      }
    } else {
      const sectionEl = hit?.closest<HTMLElement>('[data-section-key]');
      if (!sectionEl || sectionEl.dataset.sectionKey === active.key) {
        sectionDrop = null;
        return;
      }
      const rect = sectionEl.getBoundingClientRect();
      sectionDrop = {
        beforeKey:
          y < rect.top + rect.height / 2
            ? (sectionEl.dataset.sectionKey as string)
            : sibling(sectionEl, 'sectionKey', active.key, 'next'),
      };
    }
  }

  function finish(apply: boolean) {
    const current = active;
    const dropped = drop;
    const sectionTarget = sectionDrop;
    cleanup?.();
    cleanup = null;
    active = null;
    drop = null;
    sectionDrop = null;
    if (!current || !apply) return;
    if (current.kind === 'tile' && dropped) {
      cb.moveTile(current.key, dropped.sectionKey, dropped.beforeKey);
      cb.announce(`${cb.label('tile', current.key)} verschoben nach ${cb.sectionLabel(dropped.sectionKey)}.`);
    } else if (current.kind === 'section' && sectionTarget) {
      cb.moveSection(current.key, sectionTarget.beforeKey);
      cb.announce(`${cb.label('section', current.key)} verschoben.`);
    }
  }

  return {
    get active() {
      return active;
    },
    get drop() {
      return drop;
    },
    get sectionDrop() {
      return sectionDrop;
    },
    start(event: PointerEvent, kind: DragKind, key: string) {
      if (active || (event.pointerType === 'mouse' && event.button !== 0)) return;
      const handle = event.currentTarget as HTMLElement;
      event.preventDefault();
      handle.setPointerCapture?.(event.pointerId);
      active = { kind, key };
      drop = null;
      sectionDrop = null;
      const scroller = scrollParent(handle);
      let pointer = { x: event.clientX, y: event.clientY };
      const edge = 70;
      const timer = window.setInterval(() => {
        const top = scroller ? scroller.getBoundingClientRect().top : 0;
        const bottom = scroller ? scroller.getBoundingClientRect().bottom : window.innerHeight;
        const step = pointer.y < top + edge ? -16 : pointer.y > bottom - edge ? 16 : 0;
        if (step === 0) return;
        if (scroller) scroller.scrollBy(0, step);
        else window.scrollBy(0, step);
        locate(pointer.x, pointer.y);
      }, 30);
      const onMove = (e: PointerEvent) => {
        pointer = { x: e.clientX, y: e.clientY };
        locate(e.clientX, e.clientY);
      };
      const onUp = (e: PointerEvent) => {
        locate(e.clientX, e.clientY);
        finish(true);
      };
      const onCancel = () => finish(false);
      const onKey = (e: KeyboardEvent) => {
        if (e.key === 'Escape') finish(false);
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onCancel);
      window.addEventListener('keydown', onKey);
      cleanup = () => {
        window.clearInterval(timer);
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onCancel);
        window.removeEventListener('keydown', onKey);
        handle.releasePointerCapture?.(event.pointerId);
      };
    },
    cancel: () => finish(false),
  };
}

export type Drag = ReturnType<typeof createDrag>;
