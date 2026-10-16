// Eingaben des Inspektors (exakte Zeiten, Tag wechseln) als Absichten -- rein und getestet.
// Dieselben Grenzen wie beim Ziehen und bei der Tastatur (`edgeLimits`, `collision`); die
// endgültige Prüfung bleibt beim Server.

import { collision, DAY_END, edgeLimits, MIN_BLOCK, OCCUPIED, type Block, type BlockRef } from './geometry';
import type { Intent } from './intents';

/** „HH:MM“ -> Minuten. Ein Ende „00:00“ bedeutet Mitternacht (1440), weil ein Zeitfeld kein 24:00 kennt. */
export function parseClock(text: string, asEnd = false): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  const value = hours * 60 + minutes;
  return asEnd && value === 0 ? DAY_END : value;
}

/** Minuten -> Wert für ein Zeitfeld (1440 wird zu „00:00“). */
export function clockValue(minute: number): string {
  const m = minute >= DAY_END ? 0 : Math.max(0, minute);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

export type Planned = { intent: Intent } | { error: string };

/** Beginn und Ende exakt setzen. Wirkungslos (gleiche Zeiten) ergibt `{ intent }` mit demselben Bereich. */
export function exactTimes(
  block: BlockRef,
  blocks: readonly Block[],
  newStart: number,
  newEnd: number,
): Planned {
  if (!Number.isInteger(newStart) || !Number.isInteger(newEnd)) return { error: 'Bitte gültige Uhrzeiten eingeben.' };
  if (newEnd - newStart < MIN_BLOCK) return { error: 'Das Ende muss nach dem Beginn liegen.' };
  if (newStart !== block.start && newEnd !== block.end) {
    return { error: 'Beginn und Ende bitte nacheinander ändern: Der Server ändert immer nur eine Kante.' };
  }
  const startLimits = edgeLimits(block, 'start', blocks);
  const endLimits = edgeLimits(block, 'end', blocks);
  const startChanged = newStart !== block.start;
  const endChanged = newEnd !== block.end;
  if (
    (startChanged && (newStart < startLimits.min || newStart > startLimits.max)) ||
    (endChanged && (newEnd > endLimits.max || newEnd < endLimits.min))
  ) {
    return {
      error: startLimits.neighbor || endLimits.neighbor
        ? 'Der angrenzende Zeitbereich muss mindestens 5 Minuten behalten.'
        : OCCUPIED,
    };
  }
  const adjacent =
    (startChanged && startLimits.neighbor !== null) || (endChanged && endLimits.neighbor !== null);
  return { intent: { type: 'resize', block, newStart, newEnd, adjacent } };
}

/** Auf einen anderen Tag verschieben oder dort eine Kopie anlegen (gleiche Uhrzeit). */
export function toDay(block: BlockRef, blocks: readonly Block[], weekday: number, copy: boolean): Planned {
  if (!copy && weekday === block.weekday) return { error: 'Das ist bereits dieser Tag.' };
  if (collision(blocks, weekday, block.start, block.end, copy ? null : block) !== null) {
    return { error: OCCUPIED };
  }
  return {
    intent: { type: copy ? 'duplicate' : 'move', block, toWeekday: weekday, toStart: block.start },
  };
}

/** Beginn/Ende um Minuten verschieben (Knöpfe „±15 Min.“). */
export function nudge(block: BlockRef, blocks: readonly Block[], edge: 'start' | 'end', delta: number): Planned {
  return exactTimes(
    block,
    blocks,
    edge === 'start' ? block.start + delta : block.start,
    edge === 'end' ? block.end + delta : block.end,
  );
}
