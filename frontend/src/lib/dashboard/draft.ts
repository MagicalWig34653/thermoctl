// Entwurfslogik des Dashboards: reine Funktionen auf unveränderlichen Listen (getestet).
//
// Jede Änderung liefert eine neue Liste; wird eine Änderung abgelehnt (Schranke erreicht,
// unbekannter Schlüssel, nichts zu tun), kommt dieselbe Liste zurück -- der Aufrufer erkennt
// das an der Identität und legt dann keinen Rücknahmeschritt an. Die Schranken sind die des
// Servers (`types.ts`); der Server prüft beim Speichern trotzdem erneut.
//
// Eine Kachel entfernen löscht nur den Eintrag im Entwurf, nie einen Raum: Kacheln verweisen
// auf Räume, sie besitzen sie nicht.

import { clampPeriod, entryOf, periodsFor, type CatalogEntry } from './catalog';
import {
  MAX_NOTE_LENGTH,
  MAX_SECTIONS,
  MAX_TILES_PER_SECTION,
  MAX_TITLE_LENGTH,
  type DraftSection,
  type DraftTile,
  type Layout,
  type Section,
  type Tile,
} from './types';

export type Sections = readonly DraftSection[];

let counter = 0;
export const newKey = (prefix = 'k'): string => `${prefix}${++counter}`;

export function toDraft(sections: readonly Section[]): DraftSection[] {
  return sections.map((s) => ({
    key: newKey('s'),
    title: s.title,
    tiles: s.tiles.map((t) => ({ ...t, key: newKey('t') })),
  }));
}

export function fromDraft(sections: Sections): Section[] {
  return sections.map((s) => ({
    title: s.title,
    tiles: s.tiles.map(({ key: _key, ...tile }) => tile),
  }));
}

/** Körper für `PUT /ui-api/dashboard`. Titel werden wie beim Server von Leerraum befreit. */
export function toPayload(version: number, sections: Sections): { version: number; sections: Section[] } {
  return {
    version,
    sections: fromDraft(sections).map((s) => ({
      title: s.title.trim(),
      tiles: s.tiles.map((t) => ({ ...t, title: t.title.trim() })),
    })),
  };
}

/** Unterscheidet sich der Entwurf vom gespeicherten Stand? (Schlüssel zählen nicht.) */
export function isDirty(base: Layout | null, draft: Sections): boolean {
  if (!base) return draft.length > 0;
  return JSON.stringify(fromDraft(draft)) !== JSON.stringify(base.sections);
}

// -- Schranken -------------------------------------------------------------------------------

export const canAddSection = (s: Sections): boolean => s.length < MAX_SECTIONS;

export function canAddTile(s: Sections, sectionKey: string): boolean {
  const section = s.find((x) => x.key === sectionKey);
  return !!section && section.tiles.length < MAX_TILES_PER_SECTION;
}

export function makeTile(entry: CatalogEntry, zoneId: number | null): DraftTile {
  return {
    key: newKey('t'),
    kind: entry.kind,
    zone_id: entry.needsZone ? zoneId : null,
    title: '',
    period_days: clampPeriod(entry.kind, entry.kind === 'heating' ? 7 : 1),
    width: entry.defaultWidth,
    metric: entry.kind === 'metric' ? 'rooms' : null,
    destination: entry.kind === 'shortcut' ? 'schedule' : null,
    note: '',
  };
}

// -- Abschnitte ------------------------------------------------------------------------------

export function addSection(s: Sections, title: string): Sections {
  if (!canAddSection(s)) return s;
  return [...s, { key: newKey('s'), title: title.slice(0, MAX_TITLE_LENGTH), tiles: [] }];
}

export function renameSection(s: Sections, key: string, title: string): Sections {
  const cut = title.slice(0, MAX_TITLE_LENGTH);
  const section = s.find((x) => x.key === key);
  if (!section || section.title === cut) return s;
  return s.map((x) => (x.key === key ? { ...x, title: cut } : x));
}

export function removeSection(s: Sections, key: string): Sections {
  return s.some((x) => x.key === key) ? s.filter((x) => x.key !== key) : s;
}

/** Abschnitt an die Stelle `toIndex` (Index in der Liste nach dem Herausnehmen) setzen. */
export function moveSection(s: Sections, key: string, toIndex: number): Sections {
  const from = s.findIndex((x) => x.key === key);
  if (from < 0) return s;
  const to = Math.max(0, Math.min(s.length - 1, toIndex));
  if (to === from) return s;
  const next = [...s];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export const moveSectionBy = (s: Sections, key: string, delta: number): Sections => {
  const from = s.findIndex((x) => x.key === key);
  return from < 0 ? s : moveSection(s, key, from + delta);
};

// -- Kacheln ---------------------------------------------------------------------------------

/** Kachel einfügen; ohne `index` am Ende des Abschnitts. */
export function addTile(s: Sections, sectionKey: string, tile: DraftTile, index?: number): Sections {
  if (!canAddTile(s, sectionKey)) return s;
  return s.map((x) => {
    if (x.key !== sectionKey) return x;
    const at = index === undefined ? x.tiles.length : Math.max(0, Math.min(x.tiles.length, index));
    const tiles = [...x.tiles];
    tiles.splice(at, 0, tile);
    return { ...x, tiles };
  });
}

export function findTile(s: Sections, tileKey: string): { section: number; index: number } | null {
  for (let si = 0; si < s.length; si++) {
    const index = s[si].tiles.findIndex((t) => t.key === tileKey);
    if (index >= 0) return { section: si, index };
  }
  return null;
}

export type TilePatch = Partial<Omit<Tile, 'kind'>>;

export function updateTile(s: Sections, tileKey: string, patch: TilePatch): Sections {
  const at = findTile(s, tileKey);
  if (!at) return s;
  const current = s[at.section].tiles[at.index];
  const merged: DraftTile = { ...current, ...patch };
  if (patch.title !== undefined) merged.title = patch.title.slice(0, MAX_TITLE_LENGTH);
  if (patch.note !== undefined) merged.note = patch.note.slice(0, MAX_NOTE_LENGTH);
  if (periodsFor(merged.kind) !== null) merged.period_days = clampPeriod(merged.kind, merged.period_days);
  if (JSON.stringify(merged) === JSON.stringify(current)) return s;
  return s.map((x, si) =>
    si !== at.section ? x : { ...x, tiles: x.tiles.map((t, i) => (i === at.index ? merged : t)) },
  );
}

export function removeTile(s: Sections, tileKey: string): Sections {
  const at = findTile(s, tileKey);
  if (!at) return s;
  return s.map((x, si) => (si !== at.section ? x : { ...x, tiles: x.tiles.filter((t) => t.key !== tileKey) }));
}

/**
 * Kachel in einen Abschnitt setzen. `toIndex` zählt in der Liste des Zielabschnitts nach dem
 * Herausnehmen. In einen vollen anderen Abschnitt wird nicht verschoben.
 */
export function moveTile(s: Sections, tileKey: string, toSectionKey: string, toIndex: number): Sections {
  const from = findTile(s, tileKey);
  const target = s.findIndex((x) => x.key === toSectionKey);
  if (!from || target < 0) return s;
  const tile = s[from.section].tiles[from.index];
  if (target !== from.section && s[target].tiles.length >= MAX_TILES_PER_SECTION) return s;
  const base = s.map((x, si) => (si !== from.section ? x : { ...x, tiles: x.tiles.filter((t) => t.key !== tileKey) }));
  const at = Math.max(0, Math.min(base[target].tiles.length, toIndex));
  if (target === from.section && at === from.index) return s;
  return base.map((x, si) => {
    if (si !== target) return x;
    const tiles = [...x.tiles];
    tiles.splice(at, 0, tile);
    return { ...x, tiles };
  });
}

/** Eine Stelle weiter (Tastatur): am Rand eines Abschnitts in den Nachbarabschnitt. */
export function moveTileBy(s: Sections, tileKey: string, delta: -1 | 1): Sections {
  const at = findTile(s, tileKey);
  if (!at) return s;
  const section = s[at.section];
  const inside = at.index + delta;
  if (inside >= 0 && inside < section.tiles.length) return moveTile(s, tileKey, section.key, inside);
  const neighbour = s[at.section + delta];
  if (!neighbour) return s;
  return moveTile(s, tileKey, neighbour.key, delta < 0 ? neighbour.tiles.length : 0);
}

export interface TilePosition {
  sectionTitle: string;
  sectionIndex: number;
  sectionCount: number;
  index: number;
  count: number;
}

/** Wo steht die Kachel? (für Ansagen und die „Landet hier“-Anzeige) */
export function positionOf(s: Sections, tileKey: string): TilePosition | null {
  const at = findTile(s, tileKey);
  if (!at) return null;
  const section = s[at.section];
  return {
    sectionTitle: section.title,
    sectionIndex: at.section,
    sectionCount: s.length,
    index: at.index,
    count: section.tiles.length,
  };
}

// -- Prüfung ---------------------------------------------------------------------------------

export interface Problem {
  /** Schlüssel des Abschnitts oder der Kachel, zu der das Problem gehört. */
  key: string;
  field: string;
  message: string;
}

/** Findet, was der Server ablehnen würde -- vor dem Senden, mit Bezug auf die Stelle. */
export function validate(s: Sections): Problem[] {
  const out: Problem[] = [];
  if (s.length > MAX_SECTIONS) {
    out.push({ key: '', field: 'sections', message: `Höchstens ${MAX_SECTIONS} Abschnitte.` });
  }
  for (const section of s) {
    const title = section.title.trim();
    if (title.length === 0) out.push({ key: section.key, field: 'title', message: 'Der Abschnitt braucht einen Titel.' });
    if (title.length > MAX_TITLE_LENGTH) out.push({ key: section.key, field: 'title', message: `Der Titel darf höchstens ${MAX_TITLE_LENGTH} Zeichen haben.` });
    if (section.tiles.length > MAX_TILES_PER_SECTION) {
      out.push({ key: section.key, field: 'tiles', message: `Höchstens ${MAX_TILES_PER_SECTION} Kacheln je Abschnitt.` });
    }
    for (const tile of section.tiles) {
      const name = entryOf(tile.kind).name;
      if (tile.title.trim().length > MAX_TITLE_LENGTH) out.push({ key: tile.key, field: 'title', message: `${name}: Der Titel darf höchstens ${MAX_TITLE_LENGTH} Zeichen haben.` });
      if (tile.note.length > MAX_NOTE_LENGTH) out.push({ key: tile.key, field: 'note', message: `${name}: Die Notiz darf höchstens ${MAX_NOTE_LENGTH} Zeichen haben.` });
      if (entryOf(tile.kind).needsZone && tile.zone_id === null) out.push({ key: tile.key, field: 'zone_id', message: `${name}: Bitte einen Raum wählen.` });
      if (tile.kind === 'metric' && tile.metric === null) out.push({ key: tile.key, field: 'metric', message: 'Kennzahl: Bitte eine Kennzahl wählen.' });
      if (tile.kind === 'shortcut' && tile.destination === null) out.push({ key: tile.key, field: 'destination', message: 'Schnellzugriff: Bitte ein Ziel wählen.' });
    }
  }
  return out;
}

// -- Rücknahme und Wiederholen ---------------------------------------------------------------

export interface History {
  past: Sections[];
  present: Sections;
  future: Sections[];
  /** Schlüssel der letzten zusammengefassten Eingabe (Tippen in einem Feld = ein Schritt). */
  lastKey: string | null;
}

export const HISTORY_LIMIT = 100;

export const createHistory = (present: Sections): History => ({ past: [], present, future: [], lastKey: null });

/** Neuen Stand übernehmen. Gleiche Liste = nichts zu tun. Mit `coalesce` verschmelzen
 *  aufeinanderfolgende Änderungen desselben Felds zu einem Schritt. */
export function commit(h: History, next: Sections, coalesce: string | null = null): History {
  if (next === h.present) return h;
  if (coalesce !== null && h.lastKey === coalesce && h.past.length > 0) {
    return { ...h, present: next, future: [] };
  }
  const past = [...h.past, h.present].slice(-HISTORY_LIMIT);
  return { past, present: next, future: [], lastKey: coalesce };
}

export const canUndo = (h: History): boolean => h.past.length > 0;
export const canRedo = (h: History): boolean => h.future.length > 0;

export function undo(h: History): History {
  if (!canUndo(h)) return h;
  const past = h.past.slice(0, -1);
  return { past, present: h.past[h.past.length - 1], future: [h.present, ...h.future], lastKey: null };
}

export function redo(h: History): History {
  if (!canRedo(h)) return h;
  const [present, ...future] = h.future;
  return { past: [...h.past, h.present], present, future, lastKey: null };
}

// -- Ziehen: Ziel als „vor Schlüssel X“ ----------------------------------------------------

/** Kachel vor die Kachel `beforeKey` des Abschnitts setzen (`null`: ans Ende). */
export function moveTileBefore(s: Sections, tileKey: string, sectionKey: string, beforeKey: string | null): Sections {
  const target = s.find((x) => x.key === sectionKey);
  if (!target || beforeKey === tileKey) return s;
  const others = target.tiles.filter((t) => t.key !== tileKey);
  const at = beforeKey === null ? others.length : others.findIndex((t) => t.key === beforeKey);
  if (at < 0) return s;
  return moveTile(s, tileKey, sectionKey, at);
}

/** Abschnitt vor den Abschnitt `beforeKey` setzen (`null`: ans Ende). */
export function moveSectionBefore(s: Sections, key: string, beforeKey: string | null): Sections {
  if (beforeKey === key) return s;
  const others = s.filter((x) => x.key !== key);
  const at = beforeKey === null ? others.length : others.findIndex((x) => x.key === beforeKey);
  if (at < 0) return s;
  return moveSection(s, key, at);
}
