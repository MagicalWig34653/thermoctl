// Reine Geometrie des Wochenplan-Editors: Pixel <-> Minuten, Einrasten, freie Stellen,
// Kollision. Ohne Svelte, mit node:test geprüft.
//
// Das hier ist ausschließlich Vorschau und Bedienhilfe. Ob eine Änderung gilt, welche
// Schaltpunkte daraus werden und wie Sommerzeit oder Überschneidungen behandelt werden, entscheidet
// allein der Server (`domain.schedule`); jede Absicht geht dorthin und kommt als Plan zurück.
//
// Modell des Servers: Der Plan ist ein lückenloser RING von Schaltpunkten. Ein Block ist der Lauf
// von einem Schaltpunkt bis zum nächsten; er darf über Mitternacht und die Wochennaht reichen
// (`end` zählt dann über 1440 hinaus, höchstens `start + 10080`). Es gibt keinen „Grundmodus“ und
// keine Lücken. „Frei“ heißt: im Zielbereich beginnt kein anderer Block.

import type { PlanSegment } from './types';

export const DAY_END = 1440;
export const WEEK = 7 * 1440;
/** Blöcke unter dieser Länge (ein Tagesabschnitt) zieht man mit der Maus; längere sind Hintergrund. */
export const DRAG_MAX = 720;
/** Raster der Bedienung in Minuten (Einrasten beim Ziehen). */
export const SNAP = 5;
export const MIN_BLOCK = 5;
/** Schrittweite der Tastatur (Umschalt = fein). */
export const KEY_STEP = 15;
export const KEY_STEP_FINE = 5;

export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
export const DAY_NAMES: Record<number, string> = {
  1: 'Montag',
  2: 'Dienstag',
  3: 'Mittwoch',
  4: 'Donnerstag',
  5: 'Freitag',
  6: 'Samstag',
  7: 'Sonntag',
};
export const DAY_SHORT: Record<number, string> = {
  1: 'Mo',
  2: 'Di',
  3: 'Mi',
  4: 'Do',
  5: 'Fr',
  6: 'Sa',
  7: 'So',
};

/** Adresse eines Blocks wie beim Server: Wochentag, Beginn (0..1439) und Ende (`end` > 1440 möglich). */
export interface BlockRef {
  weekday: number;
  start: number;
  end: number;
}

/** Der Teil eines Blocks, der auf einen Wochentag fällt (Tagesspur), `end` höchstens 1440. */
export interface Piece {
  weekday: number;
  start: number;
  end: number;
  /** Beginn des Blocks (hier sitzt der Schaltpunkt; nur hier lässt er sich greifen). */
  first: boolean;
  /** Ende des Blocks. */
  last: boolean;
}

/** Ein Block des Plans: Lauf von einem Schaltpunkt bis zum nächsten, in Tagesstücke geschnitten. */
export interface Block extends BlockRef {
  /** Schaltpunkt, der den Block beginnt. */
  pointId: number;
  modeId: number;
  modeName: string;
  warmth: number;
  temperatureC: string | null;
  pieces: Piece[];
}

/** Ergebnis einer Geste oder Tastenabsicht, bevor sie gesendet wird. */
export interface Shape extends BlockRef {
  valid: boolean;
  reason: string;
  /** Die Quelle bleibt unverändert und die Form entsteht daneben (Alt + Ziehen). */
  copy?: boolean;
  /** Die gemeinsame Kante zum angrenzenden Block wandert mit (Server: `adjacent`). */
  adjacent?: boolean;
}

export const OCCUPIED = 'Dort beginnt bereits ein anderer Zeitbereich.';

export const refOf = (b: BlockRef): BlockRef => ({ weekday: b.weekday, start: b.start, end: b.end });
export const sameRef = (a: BlockRef, b: BlockRef): boolean =>
  a.weekday === b.weekday && a.start === b.start && a.end === b.end;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const mod = (v: number, m: number) => ((v % m) + m) % m;

/** Auf das Raster einrasten (Standard 5 Minuten). */
export function snapMinute(minute: number, step: number = SNAP): number {
  return Math.round(minute / step) * step;
}

/** Minute (ungerundet, 0..1440) an einer Pixelposition der Tagesspur. */
export function minuteAt(clientX: number, left: number, width: number): number {
  if (!(width > 0)) return 0;
  return clamp(((clientX - left) / width) * DAY_END, 0, DAY_END);
}

/** Lage in Prozent der Tagesspur. */
export function percentOf(minute: number): number {
  return (clamp(minute, 0, DAY_END) / DAY_END) * 100;
}

/** Wochentag unter einem Punkt aus den Rechtecken der Tagesspuren (oder null). */
export function dayAt(
  y: number,
  rows: readonly { weekday: number; top: number; bottom: number }[],
): number | null {
  return rows.find((r) => y >= r.top && y <= r.bottom)?.weekday ?? null;
}

/** Minute der Woche (0..10079) eines Wochentags (1..7) und einer Minute (auch außerhalb 0..1440). */
export const weekMinute = (weekday: number, minute: number): number => mod((weekday - 1) * DAY_END + minute, WEEK);

/** Beginn eines Blocks als Minute der Woche. */
export const absOf = (r: BlockRef): number => weekMinute(r.weekday, r.start);

/** Wochentag und Minute (0..1439) zu einer Minute der Woche. */
export function dayMinute(week: number): { weekday: number; minute: number } {
  const w = mod(week, WEEK);
  return { weekday: Math.floor(w / DAY_END) + 1, minute: w % DAY_END };
}

/** Adresse eines Bereichs, normalisiert (Beginn 0..1439, Wochentag entsprechend verschoben). */
export function normalizeRef(weekday: number, start: number, end: number): BlockRef {
  const { weekday: w, minute } = dayMinute(weekMinute(weekday, start));
  return { weekday: w, start: minute, end: minute + (end - start) };
}

/** Einen (ggf. über Mitternacht reichenden) Bereich in Tagesstücke schneiden. */
export function splitRange(weekday: number, start: number, end: number): Piece[] {
  const out: Piece[] = [];
  const ref = normalizeRef(weekday, start, end);
  let day = ref.weekday;
  let from = ref.start;
  let left = Math.min(ref.end - ref.start, WEEK);
  let first = true;
  while (left > 0) {
    const take = Math.min(DAY_END - from, left);
    left -= take;
    out.push({ weekday: day, start: from, end: from + take, first, last: left === 0 });
    first = false;
    day = day === 7 ? 1 : day + 1;
    from = 0;
  }
  return out;
}

/**
 * Die Blöcke des Plans aus den Balken des Servers (Tagesstücke): ein Balken mit `point_id` beginnt
 * einen Block, die folgenden ohne `point_id` (Rest vom Vortag, auch über die Wochennaht) gehören
 * dazu. Was der Server liefert, ist die Wahrheit: es entsteht nichts dazu, nichts fällt weg.
 */
export function blocksOf(segments: readonly PlanSegment[]): Block[] {
  const list = segments
    .filter((s) => s.end_minute > s.start_minute)
    .slice()
    .sort((a, b) => a.weekday - b.weekday || a.start_minute - b.start_minute);
  const heads = list.map((s, i) => (s.point_id !== null ? i : -1)).filter((i) => i >= 0);
  const blocks: Block[] = [];
  for (const i of heads) {
    const head = list[i];
    const group = [head];
    for (let k = 1; k < list.length; k += 1) {
      const next = list[(i + k) % list.length];
      if (next.point_id !== null) break;
      group.push(next);
    }
    const length = group.reduce((sum, s) => sum + (s.end_minute - s.start_minute), 0);
    blocks.push({
      weekday: head.weekday,
      start: head.start_minute,
      end: head.start_minute + length,
      pointId: head.point_id as number,
      modeId: head.mode_id,
      modeName: head.mode_name,
      warmth: head.warmth,
      temperatureC: head.temperature_c,
      pieces: group.map((s, k) => ({
        weekday: s.weekday,
        start: s.start_minute,
        end: s.end_minute,
        first: k === 0,
        last: k === group.length - 1,
      })),
    });
  }
  return blocks.sort((a, b) => a.weekday - b.weekday || a.start - b.start);
}

export function findBlock(blocks: readonly Block[], ref: BlockRef | null): Block | null {
  return ref ? (blocks.find((b) => sameRef(b, ref)) ?? null) : null;
}

/** Block, der an diesem Wochentag zu dieser Minute beginnt (Beginn genügt, das Ende kann sich geändert haben). */
export function blockStartingAt(blocks: readonly Block[], ref: BlockRef | null): Block | null {
  return ref ? (blocks.find((b) => b.weekday === ref.weekday && b.start === ref.start) ?? null) : null;
}

/** Block, der die Minute (eines Wochentags) überdeckt. */
export function blockAt(blocks: readonly Block[], weekday: number, minute: number): Block | null {
  const at = weekMinute(weekday, minute);
  return blocks.find((b) => mod(at - absOf(b), WEEK) < b.end - b.start) ?? null;
}

/** Der erste Block (ohne `except`), der innerhalb von [start, start + Länge) beginnt -- „dort ist besetzt“. */
export function collision(
  blocks: readonly Block[],
  weekday: number,
  start: number,
  end: number,
  except: BlockRef | null = null,
): Block | null {
  const from = weekMinute(weekday, start);
  const length = end - start;
  return (
    blocks.find(
      (b) => !(except !== null && sameRef(b, except)) && mod(absOf(b) - from, WEEK) < length,
    ) ?? null
  );
}

/**
 * Neue Zeit durch Ziehen. Ohne `overpaint` darf im Bereich kein anderer Block beginnen (er liegt
 * im Inneren des Blocks unter dem Ankerpunkt, den er teilt); mit `overpaint` (Umschalt) übermalt
 * er die Blöcke, die dort beginnen.
 */
export function drawShape(
  blocks: readonly Block[],
  weekday: number,
  anchor: number,
  current: number,
  overpaint: boolean,
): Shape {
  let start = Math.min(snapMinute(anchor), snapMinute(current));
  let end = Math.max(snapMinute(anchor), snapMinute(current));
  if (!overpaint) {
    const starts = blocks.filter((b) => b.weekday === weekday).map((b) => b.start);
    const before = starts.filter((m) => m <= anchor).reduce((acc, m) => Math.max(acc, m), -1);
    const after = starts.filter((m) => m > anchor).reduce((acc, m) => Math.min(acc, m), DAY_END);
    // Beginn muss echt hinter dem vorigen Schaltpunkt liegen (ein Schaltpunkt dort wäre Überschneidung).
    const lowest = before < 0 ? 0 : Math.floor(before / SNAP) * SNAP + SNAP;
    start = Math.max(start, lowest);
    end = Math.min(end, after);
  }
  start = clamp(start, 0, DAY_END);
  end = clamp(end, 0, DAY_END);
  const valid = end - start >= MIN_BLOCK;
  return { weekday, start, end, valid, reason: valid ? '' : 'Die Zeit ist zu kurz.' };
}

/** Verschieben oder (copy) Duplizieren an eine neue Stelle; Kollision macht die Form ungültig. */
export function moveShape(
  block: BlockRef,
  blocks: readonly Block[],
  pointerMinute: number,
  grabOffset: number,
  toWeekday: number,
  copy: boolean,
): Shape {
  const length = block.end - block.start;
  const start = clamp(snapMinute(pointerMinute - grabOffset), 0, DAY_END - SNAP);
  const end = start + length;
  const hit = collision(blocks, toWeekday, start, end, copy ? null : block);
  return {
    weekday: toWeekday,
    start,
    end,
    valid: hit === null,
    reason: hit === null ? '' : OCCUPIED,
    copy,
  };
}

export type Edge = 'start' | 'end';

/**
 * Spielraum einer Kante in Minuten ab Mitternacht des Block-Wochentags (kann negativ sein oder über
 * 1440 liegen). `neighbor` ist der Block, der an dieser Kante unmittelbar anschließt (die gemeinsame
 * Kante wandert mit); `null` nur beim einzigen Block.
 */
export function edgeLimits(
  block: BlockRef,
  edge: Edge,
  blocks: readonly Block[],
): { min: number; max: number; neighbor: Block | null } {
  const length = block.end - block.start;
  const offsets = blocks
    .filter((b) => !sameRef(b, block))
    .map((b) => ({ b, at: mod(absOf(b) - absOf(block), WEEK) }))
    .filter((o) => o.at > 0);
  if (edge === 'start') {
    const prev = offsets.reduce<{ b: Block; at: number } | null>((acc, o) => (!acc || o.at > acc.at ? o : acc), null);
    const low = prev ? prev.at - WEEK + MIN_BLOCK : length - WEEK;
    return {
      min: block.start + Math.max(low, length - WEEK),
      max: block.end - MIN_BLOCK,
      neighbor: prev?.b ?? null,
    };
  }
  const next = offsets.find((o) => o.at === length) ?? null;
  let high = length;
  if (next) {
    const second = offsets
      .filter((o) => o !== next && o.at > length)
      .reduce((acc, o) => Math.min(acc, o.at), WEEK);
    high = second - MIN_BLOCK;
  }
  return { min: block.start + MIN_BLOCK, max: block.start + high, neighbor: next?.b ?? null };
}

/** Eine Kante auf eine Minute der Woche ziehen (eingerastet, im Spielraum). `pointerWeek` = Minute der Woche. */
export function resizeShape(
  block: BlockRef,
  edge: Edge,
  blocks: readonly Block[],
  pointerWeek: number,
  independent = false,
): Shape {
  const limits = edgeLimits(block, edge, blocks);
  const edgeAt = edge === 'start' ? block.start : block.end;
  // Die Woche ist ein Ring: den Zeigerpunkt in die Nähe der Kante rücken.
  const delta = mod(pointerWeek - weekMinute(block.weekday, edgeAt) + WEEK / 2, WEEK) - WEEK / 2;
  const value = clamp(snapMinute(edgeAt + delta), limits.min, limits.max);
  const start = edge === 'start' ? value : block.start;
  const end = edge === 'end' ? value : block.end;
  const changed = start !== block.start || end !== block.end;
  const shrink = edge === 'start' ? start > block.start : end < block.end;
  return {
    weekday: block.weekday,
    start,
    end,
    valid: end - start >= MIN_BLOCK && end - start <= WEEK,
    reason: '',
    // Verlängern geht nur mit gemeinsamer Kante; Verkleinern wahlweise ohne (Alt): der freie Rest
    // erbt dann den Modus davor statt dem Nachbarn.
    adjacent: changed && limits.neighbor !== null && !(shrink && independent),
  };
}

/** Anteil „0..1“ der Wärme als Farbmischung (`--chart-1` kalt, `--chart-2` warm), abgedunkelt,
 *  damit weiße Schrift darauf lesbar bleibt. */
export function modeColor(warmth: number): string {
  const pct = Math.round(clamp(warmth, 0, 1) * 100);
  return `color-mix(in oklch, color-mix(in oklch, var(--chart-2) ${pct}%, var(--chart-1)) 68%, black)`;
}
