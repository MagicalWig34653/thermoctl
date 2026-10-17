// Darstellung pro Browser merken (Positionen, Ansicht, eingeklappte Knoten, technische Werte).
// Der Server führt die Anordnung (`layout.ts`); dies hier ist der Rückfall ohne Netz und der
// Zwischenspeicher für Änderungen, die noch nicht angekommen sind (`dirty`). „Technische Werte“
// bleibt ausschließlich hier. Jeder Zugriff ist abgesichert; ohne Speicher (privates Fenster,
// gesperrt, kaputt) startet die Karte mit der Anordnung vom Server oder der automatischen.

import type { Placement, View } from './geometry';

export const STORAGE_KEY = 'thermoctl.devicemap.v2';

export interface MapPrefs {
  pos: Placement;
  view: View | null;
  collapsed: string[];
  technical: boolean;
  /** Es gibt Änderungen, die der Server noch nicht bestätigt hat; sie haben dann Vorrang. */
  dirty: boolean;
}

export const EMPTY_PREFS: MapPrefs = { pos: {}, view: null, collapsed: [], technical: false, dirty: false };

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
    // Ältere Stände (vor der Serveranordnung) kennen das Merkmal nicht: Mit Inhalt gelten sie
    // als nicht gesendet und wandern beim ersten Öffnen zum Server.
    const content = Object.keys(pos).length > 0 || collapsed.length > 0 || view !== null;
    const dirty = typeof data.dirty === 'boolean' ? data.dirty : content;
    return { pos, view, collapsed, technical: data.technical === true, dirty };
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
