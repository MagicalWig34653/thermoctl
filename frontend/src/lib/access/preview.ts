// Reine Logik der Benutzer-Vorschau (ohne Svelte, damit sie unter node:test prüfbar ist).
//
// Die Vorschau zeigt, was ein Konto zu sehen bekommt. Sichtbarkeit wird hier nicht entschieden:
// Eingang ist die Navigation, die der Server für dieses Konto geliefert hat -- dieselbe
// Funktion wie in der echten Sitzung. Hier steht nur die Darstellung als Baum aus Bereichen
// und Seiten, ohne Links.

import { buildVisibleAreas, type ServerNavItem } from '../navigation';
import { buildNavEntries } from '../nav-menu';
import type { PreviewPermission } from './types';

export interface PreviewNode {
  label: string;
  /** Unterseiten; leer bei einem einfachen Eintrag. */
  children: string[];
}

/** Die Navigation eines Kontos als Baum aus Bereichen und deren Seiten (nicht klickbar). */
export function navigationTree(items: readonly ServerNavItem[]): PreviewNode[] {
  return buildNavEntries(buildVisibleAreas(items)).map((entry) =>
    entry.kind === 'link'
      ? { label: entry.label, children: [] }
      : { label: entry.area.label, children: entry.pages.map((p) => p.page.label) },
  );
}

/** Ein Recht als lesbare Zeile: Beschreibung, bei Einschränkung auf einen Raum mit dessen Namen. */
export function permissionLine(p: PreviewPermission): string {
  if (p.zone_id === null) return p.description;
  return `${p.description} (${p.zone_name ?? 'unbekannter Raum'})`;
}

/** Alle Rechte eines Kontos als Zeilen, in der Reihenfolge des Servers, ohne Doppelte. */
export function permissionLines(permissions: readonly PreviewPermission[]): string[] {
  return [...new Set(permissions.map(permissionLine))];
}
