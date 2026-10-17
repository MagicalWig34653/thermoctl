// Reine Logik der aufklappbaren Seitenleiste (ohne Svelte, damit sie unter node:test prüfbar ist).
//
// Sichtbarkeit wird hier NICHT entschieden: Eingang sind die bereits gefilterten
// `VisibleArea`s aus `buildVisibleAreas` (also ausschließlich `bootstrap.navigation`). Diese Datei
// entscheidet nur Darstellung: Gruppe mit Unterseiten oder einfacher Link, was offen ist,
// was der Browser sich merkt.

import { areaPath, type VisibleArea, type VisiblePage } from './navigation';

export type NavEntry =
  | { kind: 'link'; area: VisibleArea; to: string; label: string }
  | { kind: 'group'; area: VisibleArea; pages: { page: Pick<VisiblePage, 'id' | 'label'>; to: string; home?: boolean }[] };

export function pageTo(areaId: string, pageId: string): string {
  return `/${areaId}/${pageId}`;
}

/**
 * Bereiche mit höchstens einer sichtbaren Seite werden zum einfachen Link (auf die Seite selbst,
 * ohne Zwischenstation); „Übersicht“ führt auf die Startseite und wird erst zur Gruppe, wenn es Seiten dazu gibt (Räume). Bereiche mit mehreren
 * sichtbaren Seiten werden zur aufklappbaren Gruppe. Bereiche ohne Seite (außer Übersicht)
 * erscheinen nicht -- `buildVisibleAreas` liefert sie ohnehin nicht, hier zur Sicherheit doppelt.
 */
export function buildNavEntries(areas: readonly VisibleArea[]): NavEntry[] {
  const out: NavEntry[] = [];
  for (const area of areas) {
    if (area.id === 'uebersicht') {
      if (area.pages.length === 0) {
        out.push({ kind: 'link', area, to: areaPath(area.id), label: area.label });
      } else {
        // Wer Räume verwalten darf, bekommt neben der Startseite die Seite „Räume“; ohne Gruppe wäre sie
        // nach dem ersten Raum nirgends mehr verlinkt.
        out.push({
          kind: 'group',
          area,
          pages: [
            { page: { id: 'start', label: area.label }, to: areaPath(area.id), home: true },
            ...area.pages.map((page) => ({ page, to: pageTo(area.id, page.id) })),
          ],
        });
      }
    } else if (area.pages.length === 1) {
      out.push({ kind: 'link', area, to: pageTo(area.id, area.pages[0].id), label: area.label });
    } else if (area.pages.length > 1) {
      out.push({
        kind: 'group',
        area,
        pages: area.pages.map((page) => ({ page, to: pageTo(area.id, page.id) })),
      });
    }
  }
  return out;
}

/** Kennungen aller aufklappbaren Gruppen. */
export function groupIds(entries: readonly NavEntry[]): string[] {
  return entries.flatMap((e) => (e.kind === 'group' ? [e.area.id] : []));
}

/**
 * Offene Gruppen: gemerkte Auswahl, geschnitten mit den vorhandenen Gruppen (eine Kennung, die es
 * nicht mehr gibt oder die die Person nicht sehen darf, bleibt draußen), dazu immer die Gruppe
 * der aktuellen Seite.
 */
export function openGroups(
  stored: readonly string[] | null,
  groups: readonly string[],
  activeAreaId: string | null,
): Set<string> {
  const known = new Set(groups);
  const open = new Set((stored ?? []).filter((id) => known.has(id)));
  if (activeAreaId && known.has(activeAreaId)) open.add(activeAreaId);
  return open;
}

export function toggleGroup(open: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(open);
  if (!next.delete(id)) next.add(id);
  return next;
}

export const STORAGE_KEY = 'thermoctl.nav.open';

/** Liest die gemerkte Auswahl; jede Abweichung vom erwarteten Format ergibt `null`. */
export function parseStored(raw: string | null): string[] | null {
  if (raw === null) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return null;
    return value.filter((v): v is string => typeof v === 'string');
  } catch {
    return null;
  }
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

/** Browser-Speicher kann fehlen oder werfen (Privatmodus, gesperrt): dann einfach nichts merken. */
export function loadOpen(storage: () => StorageLike): string[] | null {
  try {
    return parseStored(storage().getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

export function saveOpen(storage: () => StorageLike, open: ReadonlySet<string>): void {
  try {
    storage().setItem(STORAGE_KEY, JSON.stringify([...open].sort()));
  } catch {
    /* ohne Speicher funktioniert die Leiste weiter, sie merkt sich nur nichts */
  }
}

/** Pfeiltasten in der Liste: Index des nächsten Elements; kein Umbruch an den Rändern. */
export function nextFocusIndex(count: number, current: number, key: string): number {
  if (count <= 0) return -1;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  const base = current < 0 ? (key === 'ArrowUp' ? count : -1) : current;
  if (key === 'ArrowDown') return Math.min(count - 1, base + 1);
  if (key === 'ArrowUp') return Math.max(0, base - 1);
  return current;
}
