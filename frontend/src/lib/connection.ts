// Verbindungszustand: reine Logik ohne Seiteneffekt, damit sie sich mit node:test prüfen lässt.
//
// Zwei Abfragen in Folge ohne Antwort des Servers (Netzfehler, Zeitüberschreitung, 5xx) gelten als
// unterbrochene Verbindung. Jede andere Antwort des Servers -- auch 401, 403, 404 oder 422 -- beweist,
// dass er erreichbar ist: Sie zählt nicht als Fehlschlag und beendet eine Unterbrechung. Die Anzeige
// nennt dann den Zeitpunkt der letzten Antwort als Stand ihrer Daten.

export const FAILURE_THRESHOLD = 2;

export const OFFLINE_WRITE_MESSAGE = 'Keine Verbindung. Änderungen sind gerade nicht möglich.';
export const OFFLINE_HINT = 'Keine Verbindung';

export interface ConnectionState {
  down: boolean;
  failures: number;
  /** Zeitpunkt (ms) der letzten Antwort des Servers, sonst null. */
  lastOkAt: number | null;
}

export interface TrackerDeps {
  now?: () => number;
  threshold?: number;
  /** Wird nur beim Wechsel zwischen verbunden und unterbrochen aufgerufen. */
  onChange?: (state: ConnectionState) => void;
}

export function createConnectionTracker(deps: TrackerDeps = {}) {
  const now = deps.now ?? Date.now;
  const threshold = deps.threshold ?? FAILURE_THRESHOLD;
  let state: ConnectionState = { down: false, failures: 0, lastOkAt: null };

  function set(next: ConnectionState) {
    const changed = next.down !== state.down;
    state = next;
    if (changed) deps.onChange?.(state);
  }

  return {
    get state(): ConnectionState {
      return state;
    },
    success() {
      set({ down: false, failures: 0, lastOkAt: now() });
    },
    failure() {
      const failures = state.failures + 1;
      set({ ...state, failures, down: failures >= threshold });
    },
  };
}

export type Tracker = ReturnType<typeof createConnectionTracker>;

/** Ordnet einen Fehler ein: `down` = Server nicht erreicht, `up` = Server hat geantwortet,
 *  `ignore` = sagt nichts über die Verbindung. Passt für `ApiError` und `KioskError`. */
export function classifyFailure(error: unknown): 'down' | 'up' | 'ignore' {
  if (typeof error !== 'object' || error === null) return 'ignore';
  const { kind, status } = error as { kind?: unknown; status?: unknown };
  if (typeof kind !== 'string' || typeof status !== 'number') return 'ignore';
  if (kind === 'client') return 'ignore';
  if (kind === 'network') return 'down';
  // Nur Gateway-Fehler deuten auf einen nicht erreichbaren Dienst; ein gewöhnliches 500 ist
  // eine Antwort des Servers und darf andere Aktionen nicht sperren.
  return kind === 'http' && (status === 502 || status === 503 || status === 504) ? 'down' : 'up';
}

/** Uhrzeit `HH:MM` in der Ortszeit des Geräts, wie sonst in der Oberfläche. */
export function clockLabel(ms: number): string {
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date(ms));
}

/** Hinweistext; `since` ist die schon formatierte Uhrzeit der letzten Aktualität. */
export function bannerTextSince(since: string): string {
  return `Verbindung unterbrochen – Anzeige veraltet seit ${since}`;
}

/** Ohne Antwort seit dem Laden gilt der Zeitpunkt des Seitenstarts, mit entsprechendem Hinweis. */
export function bannerText(lastOkAt: number | null, startedAt: number): string {
  return lastOkAt === null
    ? `${bannerTextSince(clockLabel(startedAt))} (noch keine Antwort seit dem Laden)`
    : bannerTextSince(clockLabel(lastOkAt));
}

export interface ProberDeps {
  /** Ein billiger lesender Abruf; Fehler werden verschluckt (die Verbindungsmeldung kommt aus `api`). */
  probe: () => Promise<unknown>;
  intervalMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export const PROBE_INTERVAL_MS = 10_000;

/** Wiederholt `probe` in festem Mindestabstand, nie überlappend, bis `stop()`. Der nächste
 *  Takt wird erst nach Ende der Probe geplant. */
export function createProber(deps: ProberDeps) {
  const intervalMs = deps.intervalMs ?? PROBE_INTERVAL_MS;
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let running = false;
  let handle: unknown = null;

  function schedule() {
    if (running && handle === null) handle = setTimer(() => void tick(), intervalMs);
  }

  async function tick() {
    handle = null;
    if (!running) return;
    try {
      await deps.probe();
    } catch {
      // Gescheitert: bleibt unterbrochen, nächster Versuch im Takt.
    }
    schedule();
  }

  return {
    start() {
      if (running) return;
      running = true;
      schedule();
    },
    stop() {
      running = false;
      if (handle !== null) clearTimer(handle);
      handle = null;
    },
  };
}
