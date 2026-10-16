// Tastaturlogik des Wochenplan-Editors (rein, mit node:test geprüft).
//
//  - Pfeile am Block: verschieben (links/rechts um 15 Minuten, Umschalt 5; hoch/runter auf den
//    Vor- bzw. Folgetag, ohne Umbruch).
//  - Alt + links/rechts: Ende, Alt + hoch/runter: Beginn ändern (derselbe Spielraum wie beim Ziehen).
//  - Enter/Leertaste: Inspektor öffnen.
//  - Backspace/Entf: markierten Block löschen -- nicht in Eingaben, Auswahlfeldern,
//    editierbarem Inhalt oder Dialogen.
//  - Strg/Cmd+Z: rücknehmen, mit Umschalt: wiederholen -- nicht in Eingaben.

import { collision, DAY_END, edgeLimits, KEY_STEP, KEY_STEP_FINE, OCCUPIED, type Block } from './geometry';
import type { Intent } from './intents';

export interface KeyLike {
  key: string;
  altKey: boolean;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

export type KeyAction =
  | { type: 'none' }
  | { type: 'open' }
  | { type: 'blocked'; reason: string }
  | { type: 'intent'; intent: Intent };

const NONE: KeyAction = { type: 'none' };
export const CARRY_REASON =
  'Dieser Bereich setzt sich vom Vortag fort. Bearbeite seinen Beginn am Vortag.';

export function blockKeyAction(e: KeyLike, block: Block, blocks: readonly Block[]): KeyAction {
  if (e.key === 'Enter' || e.key === ' ') return { type: 'open' };
  const arrow = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key);
  if (!arrow || e.ctrlKey || e.metaKey) return NONE;
  if (block.carry) return { type: 'blocked', reason: CARRY_REASON };
  const step = e.shiftKey ? KEY_STEP_FINE : KEY_STEP;
  const length = block.end - block.start;

  if (e.altKey) {
    const edge = e.key === 'ArrowLeft' || e.key === 'ArrowRight' ? 'end' : 'start';
    const sign = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
    const limits = edgeLimits(block, edge, blocks);
    const value = Math.max(
      limits.min,
      Math.min(limits.max, (edge === 'end' ? block.end : block.start) + sign * step),
    );
    const newStart = edge === 'start' ? value : block.start;
    const newEnd = edge === 'end' ? value : block.end;
    if (newStart === block.start && newEnd === block.end) return NONE;
    return {
      type: 'intent',
      intent: { type: 'resize', block, newStart, newEnd, adjacent: limits.neighbor !== null },
    };
  }

  let toWeekday = block.weekday;
  let toStart = block.start;
  if (e.key === 'ArrowLeft') toStart = Math.max(0, block.start - step);
  if (e.key === 'ArrowRight') toStart = Math.min(DAY_END - length, block.start + step);
  if (e.key === 'ArrowUp') toWeekday = Math.max(1, block.weekday - 1);
  if (e.key === 'ArrowDown') toWeekday = Math.min(7, block.weekday + 1);
  if (toWeekday === block.weekday && toStart === block.start) return NONE;
  if (collision(blocks, toWeekday, toStart, toStart + length, block) !== null) {
    return { type: 'blocked', reason: OCCUPIED };
  }
  return { type: 'intent', intent: { type: 'move', block, toWeekday, toStart } };
}

/** Das minimal Nötige eines Ereignisziels; echte `Element`s erfüllen es. */
export interface TargetLike {
  closest(selector: string): unknown;
}

/** Orte, an denen Backspace/Entf und Strg+Z ihre eigene Bedeutung haben. */
export const TEXT_CONTEXT =
  'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="dialog"], [role="alertdialog"]';

export interface DeleteKeyLike extends KeyLike {
  repeat: boolean;
  defaultPrevented: boolean;
  target: TargetLike | null;
}

export function deleteKeyAllowed(e: DeleteKeyLike): boolean {
  if (e.key !== 'Backspace' && e.key !== 'Delete') return false;
  if (e.repeat || e.defaultPrevented) return false;
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false;
  return !(e.target && e.target.closest(TEXT_CONTEXT));
}

/** Strg/Cmd+Z: `undo`, mit Umschalt `redo`; sonst null. Nicht in Textfeldern und Dialogen. */
export function historyKey(e: KeyLike & { target: TargetLike | null }): 'undo' | 'redo' | null {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  if (e.key.toLowerCase() !== 'z') return null;
  if (e.target && e.target.closest(TEXT_CONTEXT)) return null;
  return e.shiftKey ? 'redo' : 'undo';
}
