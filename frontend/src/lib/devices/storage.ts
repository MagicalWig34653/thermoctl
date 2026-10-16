// Darstellung pro Browser merken (Positionen, Ansicht, eingeklappte Knoten, technische Werte).
// Nur Darstellung: Nichts davon geht an den Server. Jeder Zugriff ist abgesichert; ohne
// Speicher (privates Fenster, gesperrt, kaputt) startet die Karte mit der automatischen Anordnung.

import type { Placement, View } from './geometry';

export const STORAGE_KEY = 'thermoctl.devicemap.v2';

export interface MapPrefs {
  pos: Placement;
  view: View | null;
  collapsed: string[];
  technical: boolean;
}

export const EMPTY_PREFS: MapPrefs = { pos: {}, view: null, collapsed: [], technical: false };

type Store = Pick<Storage, 'getItem' | 'setItem'>;

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

export function parsePrefs(raw: string | null): MapPrefs {
  if (!raw) return { ...EMPTY_PREFS, pos: {}, collapsed: [] };
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const pos: Placement = {};
    if (data.pos && typeof data.pos === 'object') {
      for (const [id, p] of Object.entries(data.pos as Record<string, unknown>)) {
        const q = p as { x?: unknown; y?: unknown } | null;
        if (q && finite(q.x) && finite(q.y)) pos[id] = { x: q.x, y: q.y };
      }
    }
    const v = data.view as Partial<View> | null | undefined;
    const view = v && finite(v.x) && finite(v.y) && finite(v.k) && v.k > 0 ? { x: v.x, y: v.y, k: v.k } : null;
    const collapsed = Array.isArray(data.collapsed) ? data.collapsed.filter((c): c is string => typeof c === 'string') : [];
    return { pos, view, collapsed, technical: data.technical === true };
  } catch {
    return { ...EMPTY_PREFS, pos: {}, collapsed: [] };
  }
}

export function loadPrefs(store: () => Store | null = defaultStore): MapPrefs {
  try {
    return parsePrefs(store()?.getItem(STORAGE_KEY) ?? null);
  } catch {
    return { ...EMPTY_PREFS, pos: {}, collapsed: [] };
  }
}

export function savePrefs(prefs: MapPrefs, store: () => Store | null = defaultStore): void {
  try {
    store()?.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Speicher voll oder gesperrt: Die Karte funktioniert ohne.
  }
}

function defaultStore(): Store | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
