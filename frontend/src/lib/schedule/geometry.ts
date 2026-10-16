// Reine Geometrie des Wochenplan-Editors: Pixel <-> Minuten, Einrasten, freie Stellen,
// Kollision. Ohne Svelte, mit node:test geprüft.
//
// Das hier ist ausschließlich Vorschau und Bedienhilfe. Ob eine Änderung gilt, welche
// Schaltpunkte daraus werden und wie Sommerzeit oder Überschneidungen behandelt werden, entscheidet
// allein der Server (`domain.schedule`); jede Absicht geht dorthin und kommt als Plan zurück.

import type { PlanMode, PlanSegment } from './types';

export const DAY_END = 1440;
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

export interface BlockRef {
  weekday: number;
  start: number;
  end: number;
}

/** Ein bearbeitbarer Zeitbereich: ein Balken der Woche, dessen Modus nicht der Grundmodus ist. */
export interface Block extends BlockRef {
  modeId: number;
  modeName: string;
  warmth: number;
  temperatureC: string | null;
  /** Rest, der vom Vortag herüberreicht (kein eigener Schaltpunkt): nicht ziehbar. */
  carry: boolean;
}

/** Ergebnis einer Geste oder Tastenabsicht, bevor sie gesendet wird. */
export interface Shape extends BlockRef {
  valid: boolean;
  reason: string;
  /** Die Quelle bleibt unverändert und die Form entsteht daneben (Alt + Ziehen). */
  copy?: boolean;
  /** Eine Modusgrenze zu einem unmittelbar angrenzenden Bereich wird gemeinsam verschoben. */
  adjacent?: boolean;
}

export const OCCUPIED = 'Hier ist bereits eine Zeit geplant.';

export const refOf = (b: BlockRef): BlockRef => ({ weekday: b.weekday, start: b.start, end: b.end });
export const sameRef = (a: BlockRef, b: BlockRef): boolean =>
  a.weekday === b.weekday && a.start === b.start && a.end === b.end;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

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

/**
 * Grundmodus = „freie Zeit“: der kälteste Modus. Der Server kennt kein „frei“; die Oberfläche
 * zeichnet nur die wärmeren Bereiche als Blöcke. Bei Gleichstand gilt der erste der Liste.
 */
export function baseModeId(modes: readonly PlanMode[]): number | null {
  let best: PlanMode | null = null;
  for (const mode of modes) {
    if (best === null || mode.warmth < best.warmth) best = mode;
  }
  return best?.id ?? null;
}

/** Alle Balken der Woche, die nicht der Grundmodus sind, nach Tag und Beginn geordnet. */
export function blocksOf(segments: readonly PlanSegment[], base: number | null): Block[] {
  return segments
    .filter((s) => s.mode_id !== base && s.end_minute > s.start_minute)
    .map((s) => ({
      weekday: s.weekday,
      start: s.start_minute,
      end: s.end_minute,
      modeId: s.mode_id,
      modeName: s.mode_name,
      warmth: s.warmth,
      temperatureC: s.temperature_c,
      carry: s.point_id === null,
    }))
    .sort((a, b) => a.weekday - b.weekday || a.start - b.start);
}

export function findBlock(blocks: readonly Block[], ref: BlockRef | null): Block | null {
  return ref ? (blocks.find((b) => sameRef(b, ref)) ?? null) : null;
}

/** Block, der die Minute überdeckt. */
export function blockAt(blocks: readonly Block[], weekday: number, minute: number): Block | null {
  return blocks.find((b) => b.weekday === weekday && b.start <= minute && minute < b.end) ?? null;
}

/** Block, der den Bereich [start, end) ganz enthält (um nach einer Änderung neu zu markieren). */
export function blockCovering(
  blocks: readonly Block[],
  weekday: number,
  start: number,
  end: number,
): Block | null {
  return blocks.find((b) => b.weekday === weekday && b.start <= start && b.end >= end) ?? null;
}

/** Freie Lücke (zwischen Blöcken) um eine Minute, sonst null wenn dort ein Block liegt. */
export function gapAround(
  blocks: readonly Block[],
  weekday: number,
  minute: number,
): { start: number; end: number } | null {
  const m = clamp(minute, 0, DAY_END - 1);
  if (blockAt(blocks, weekday, m)) return null;
  const day = blocks.filter((b) => b.weekday === weekday);
  const before = day.filter((b) => b.end <= m).reduce((acc, b) => Math.max(acc, b.end), 0);
  const after = day.filter((b) => b.start > m).reduce((acc, b) => Math.min(acc, b.start), DAY_END);
  return { start: before, end: after };
}

/** Der erste Block, der [start, end) am Wochentag überschneidet (ohne `except`). */
export function collision(
  blocks: readonly Block[],
  weekday: number,
  start: number,
  end: number,
  except: BlockRef | null = null,
): Block | null {
  return (
    blocks.find(
      (b) =>
        b.weekday === weekday &&
        b.start < end &&
        b.end > start &&
        !(except !== null && sameRef(b, except)),
    ) ?? null
  );
}

/** Neue Zeit durch Ziehen auf freier Fläche; mit `overpaint` (Umschalt) auch über Blöcke. */
export function drawShape(
  blocks: readonly Block[],
  weekday: number,
  anchor: number,
  current: number,
  overpaint: boolean,
): Shape {
  const a = snapMinute(anchor);
  const c = snapMinute(current);
  let start = Math.min(a, c);
  let end = Math.max(a, c);
  if (!overpaint) {
    const gap = gapAround(blocks, weekday, anchor);
    if (gap === null) return { weekday, start, end, valid: false, reason: OCCUPIED };
    start = Math.max(start, gap.start);
    end = Math.min(end, gap.end);
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
  const latest = Math.floor((DAY_END - length) / SNAP) * SNAP;
  const start = clamp(snapMinute(pointerMinute - grabOffset), 0, latest);
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

/** Spielraum einer Kante: bis wohin sie darf und ob dort ein Block unmittelbar angrenzt. */
export function edgeLimits(
  block: BlockRef,
  edge: Edge,
  blocks: readonly Block[],
): { min: number; max: number; neighbor: Block | null } {
  const day = blocks.filter((b) => b.weekday === block.weekday && !sameRef(b, block));
  if (edge === 'start') {
    const neighbor = day.find((b) => b.end === block.start) ?? null;
    if (neighbor) return { min: neighbor.start + MIN_BLOCK, max: block.end - MIN_BLOCK, neighbor };
    const min = day.filter((b) => b.end <= block.start).reduce((acc, b) => Math.max(acc, b.end), 0);
    return { min, max: block.end - MIN_BLOCK, neighbor: null };
  }
  const neighbor = day.find((b) => b.start === block.end) ?? null;
  if (neighbor) return { min: block.start + MIN_BLOCK, max: neighbor.end - MIN_BLOCK, neighbor };
  const max = day.filter((b) => b.start >= block.end).reduce((acc, b) => Math.min(acc, b.start), DAY_END);
  return { min: block.start + MIN_BLOCK, max, neighbor: null };
}

/** Eine Kante auf eine Minute ziehen (auf das Raster und in den Spielraum begrenzt). */
export function resizeShape(
  block: BlockRef,
  edge: Edge,
  blocks: readonly Block[],
  pointerMinute: number,
): Shape {
  const limits = edgeLimits(block, edge, blocks);
  const value = clamp(snapMinute(pointerMinute), limits.min, limits.max);
  const start = edge === 'start' ? value : block.start;
  const end = edge === 'end' ? value : block.end;
  const changed = start !== block.start || end !== block.end;
  return {
    weekday: block.weekday,
    start,
    end,
    valid: end - start >= MIN_BLOCK,
    reason: '',
    adjacent: changed && limits.neighbor !== null,
  };
}

/** Anteil „0..1“ der Wärme als Farbmischung (`--chart-1` kalt, `--chart-2` warm), abgedunkelt,
 *  damit weiße Schrift darauf lesbar bleibt. */
export function modeColor(warmth: number): string {
  const pct = Math.round(clamp(warmth, 0, 1) * 100);
  return `color-mix(in oklch, color-mix(in oklch, var(--chart-2) ${pct}%, var(--chart-1)) 68%, black)`;
}
