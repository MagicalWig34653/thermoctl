// Reaktive Hülle um den Verbindungszustand (Logik und Tests: `connection.ts`).

import { createConnectionTracker, type ConnectionState } from './connection';

export function createConnection() {
  let state = $state<ConnectionState>({ down: false, failures: 0, lastOkAt: null });
  const tracker = createConnectionTracker();
  const startedAt = Date.now();
  return {
    /** Zeitpunkt des Seitenstarts: Stand der Anzeige, solange noch keine Antwort kam. */
    startedAt,
    get down() {
      return state.down;
    },
    get lastOkAt() {
      return state.lastOkAt;
    },
    /** Aus der API-Schicht: hat der Server geantwortet? */
    report(reachable: boolean) {
      if (reachable) tracker.success();
      else tracker.failure();
      state = tracker.state;
    },
  };
}

export type Connection = ReturnType<typeof createConnection>;
