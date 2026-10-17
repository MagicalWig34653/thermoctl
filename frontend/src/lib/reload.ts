// Fehlgeschlagene Code-Chunks abfangen. Ein Chunk kann scheitern, weil das Netz kurz weg war oder die
// Anwendung gerade aktualisiert wurde (die geöffnete Seite kennt dann Dateinamen, die es nicht mehr gibt).
// Ein einmaliges Neuladen holt die aktuelle Hülle; damit daraus keine Schleife wird, geschieht es höchstens
// einmal je Zeitfenster (Zeitstempel in sessionStorage; ohne Speicher gibt es kein automatisches Neuladen).

export const RELOAD_KEY = 'thermoctl-chunk-reload';
export const RELOAD_WINDOW_MS = 30_000;

export function mayReload(storage: Pick<Storage, 'getItem' | 'setItem'> | null, now: number): boolean {
  if (!storage) return false;
  try {
    const last = Number(storage.getItem(RELOAD_KEY));
    if (Number.isFinite(last) && last > 0 && now - last < RELOAD_WINDOW_MS) return false;
    storage.setItem(RELOAD_KEY, String(now));
    return true;
  } catch {
    return false;
  }
}

/** Lädt die Seite einmal neu, wenn das Zeitfenster es erlaubt; gibt zurück, ob neu geladen wird. */
export function reloadOnce(): boolean {
  let storage: Storage | null = null;
  try {
    storage = window.sessionStorage;
  } catch {
    storage = null;
  }
  if (!mayReload(storage, Date.now())) return false;
  window.location.reload();
  return true;
}
