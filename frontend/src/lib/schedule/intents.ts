// Fachliche Absichten des Wochenplan-Editors. Mausgeste, Inspektor und Tastatur erzeugen
// dieselben Absichten; sie gehen über denselben Weg an den Server und werden dort gleich
// geprüft. Hier steht nur, was die Oberfläche über die Absicht wissen muss: wie sie in der
// Vorschau (Ghost) aussieht und was danach markiert sein soll.

import { DAY_NAMES, refOf, type BlockRef } from './geometry';
import { formatMinute } from '../rooms/logic';

export type Intent =
  | { type: 'paint'; weekday: number; start: number; end: number; modeId: number }
  | { type: 'move'; block: BlockRef; toWeekday: number; toStart: number }
  | { type: 'duplicate'; block: BlockRef; toWeekday: number; toStart: number }
  | { type: 'resize'; block: BlockRef; newStart: number; newEnd: number; adjacent: boolean }
  | { type: 'delete'; block: BlockRef }
  | { type: 'copyDay'; weekday: number; scope: 'workdays' | 'all' }
  | { type: 'copyWeek'; targets: { zone_id: number; version: string }[] };

/** Was während eines laufenden Schreibaufrufs als Ghost gezeigt wird. */
export interface Ghost {
  weekday: number;
  start: number;
  end: number;
  /** Die Quelle wird nach dem Speichern verschwinden (Verschieben, Größe, Löschen). */
  source: BlockRef | null;
  label: string;
}

export function ghostOf(intent: Intent): Ghost | null {
  switch (intent.type) {
    case 'paint':
      return {
        weekday: intent.weekday,
        start: intent.start,
        end: intent.end,
        source: null,
        label: 'Wird gespeichert',
      };
    case 'move':
    case 'duplicate': {
      const length = intent.block.end - intent.block.start;
      return {
        weekday: intent.toWeekday,
        start: intent.toStart,
        end: intent.toStart + length,
        source: intent.type === 'move' ? intent.block : null,
        label: 'Wird gespeichert',
      };
    }
    case 'resize':
      return {
        weekday: intent.block.weekday,
        start: intent.newStart,
        end: intent.newEnd,
        source: intent.block,
        label: 'Wird gespeichert',
      };
    case 'delete':
      return { ...intent.block, source: intent.block, label: 'Wird entfernt' };
    default:
      return null;
  }
}

/** Nach dem Speichern zu markierender Bereich (der Block, der ihn enthält), sonst nichts. */
export function selectionAfter(intent: Intent): BlockRef | null {
  switch (intent.type) {
    case 'paint':
      return { weekday: intent.weekday, start: intent.start, end: intent.end };
    case 'move':
    case 'duplicate':
      return {
        weekday: intent.toWeekday,
        start: intent.toStart,
        end: intent.toStart + (intent.block.end - intent.block.start),
      };
    case 'resize':
      return { weekday: intent.block.weekday, start: intent.newStart, end: intent.newEnd };
    default:
      return null;
  }
}

const range = (r: BlockRef) => `${DAY_NAMES[r.weekday]}, ${formatMinute(r.start)} bis ${formatMinute(r.end)}`;

/** Satz für Statuszeile und Vorlesen. */
export function describeIntent(intent: Intent): string {
  switch (intent.type) {
    case 'paint':
      return `Zeit angelegt: ${range({ weekday: intent.weekday, start: intent.start, end: intent.end })}`;
    case 'move':
      return `Verschoben nach ${range({ ...refOf(intent.block), weekday: intent.toWeekday, start: intent.toStart, end: intent.toStart + intent.block.end - intent.block.start })}`;
    case 'duplicate':
      return `Dupliziert nach ${range({ ...refOf(intent.block), weekday: intent.toWeekday, start: intent.toStart, end: intent.toStart + intent.block.end - intent.block.start })}`;
    case 'resize':
      return `Zeit geändert: ${range({ weekday: intent.block.weekday, start: intent.newStart, end: intent.newEnd })}`;
    case 'delete':
      return `Zeit entfernt: ${range(intent.block)}`;
    case 'copyDay':
      return `${DAY_NAMES[intent.weekday]} kopiert (${intent.scope === 'workdays' ? 'Montag bis Freitag' : 'alle Tage'})`;
    case 'copyWeek':
      return `Wochenplan in ${intent.targets.length} ${intent.targets.length === 1 ? 'Raum' : 'Räume'} übernommen`;
  }
}

/** Absicht ohne Wirkung (Zielstelle gleich der Quelle)? Dann wird nichts gesendet. */
export function isNoop(intent: Intent): boolean {
  switch (intent.type) {
    case 'move':
      return intent.toWeekday === intent.block.weekday && intent.toStart === intent.block.start;
    case 'resize':
      return intent.newStart === intent.block.start && intent.newEnd === intent.block.end;
    default:
      return false;
  }
}
