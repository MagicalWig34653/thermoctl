// Zeitgesteuertes Nachladen ohne Überlappung. Pausiert bei verstecktem Tab und lädt beim
// Zurückkehren sofort nach. Nur für lesende Aufrufe gedacht: schreibende werden nie
// automatisch wiederholt.

export interface PollerDeps {
  run: () => Promise<void>;
  intervalMs: number;
  isHidden: () => boolean;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export function createPoller(deps: PollerDeps) {
  const now = deps.now ?? Date.now;
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let handle: unknown = null;
  let running = false;
  let inFlight = false;
  let lastRun = 0;

  async function tick() {
    handle = null;
    if (!running) return;
    if (deps.isHidden()) return; // wird beim Sichtbarwerden wieder aufgenommen
    if (!inFlight) {
      inFlight = true;
      lastRun = now();
      try {
        await deps.run();
      } catch {
        // Die Fehler zeigt `run` selbst; der Takt läuft weiter.
      } finally {
        inFlight = false;
      }
    }
    schedule(deps.intervalMs);
  }

  function schedule(ms: number) {
    if (!running || handle !== null) return;
    handle = setTimer(() => void tick(), ms);
  }

  return {
    /** Beginnt mit dem Takt; die erste Ladung macht der Aufrufer selbst. */
    start() {
      if (running) return;
      running = true;
      lastRun = now();
      if (!deps.isHidden()) schedule(deps.intervalMs);
    },
    stop() {
      running = false;
      if (handle !== null) clearTimer(handle);
      handle = null;
    },
    /** Aus `visibilitychange` aufrufen. */
    visibilityChanged() {
      if (!running) return;
      if (deps.isHidden()) {
        if (handle !== null) clearTimer(handle);
        handle = null;
        return;
      }
      if (handle === null) schedule(now() - lastRun >= deps.intervalMs ? 0 : deps.intervalMs - (now() - lastRun));
    },
  };
}

export type Poller = ReturnType<typeof createPoller>;
