// Gelesener Serverzustand mit Nachladen im Takt. Beim Nachladen bleibt die alte Anzeige stehen
// (kein Leeren, kein Spinner); ein Fehler wird daneben gemeldet, ohne die Daten zu verwerfen.
// Ändert sich die Antwort nicht, wird nichts zugewiesen -- die Oberfläche zeichnet nicht neu.

import { createPoller } from './poller';
import { toFailure, type Failure } from './logic';

export const POLL_MS = 30_000;

export function createResource<T>(load: () => Promise<T>) {
  let data = $state<T | null>(null);
  let failure = $state<Failure | null>(null);
  let loading = $state(true);
  let updatedAt = $state<number | null>(null);
  let fingerprint = '';
  let generation = 0;

  function accept(value: T) {
    const next = JSON.stringify(value);
    if (next !== fingerprint) {
      fingerprint = next;
      data = value;
    }
    updatedAt = Date.now();
    failure = null;
  }

  async function refresh(): Promise<void> {
    const mine = ++generation;
    try {
      const value = await load();
      if (mine !== generation) return; // eine neuere Anfrage oder ein Schreibergebnis gilt
      accept(value);
    } catch (error) {
      if (mine !== generation) return;
      failure = toFailure(error);
    } finally {
      if (mine === generation) loading = false;
    }
  }

  const poller = createPoller({
    run: refresh,
    intervalMs: POLL_MS,
    isHidden: () => document.hidden,
  });
  const onVisibility = () => poller.visibilityChanged();

  return {
    get data() {
      return data;
    },
    get failure() {
      return failure;
    },
    get loading() {
      return loading;
    },
    get updatedAt() {
      return updatedAt;
    },
    refresh,
    /** Ergebnis eines Schreibaufrufs übernehmen; laufende Lesungen werden verworfen. */
    adopt(value: T) {
      generation += 1;
      loading = false;
      accept(value);
    },
    /** Takt starten; gibt die Aufräumfunktion zurück (für `$effect`). */
    start(): () => void {
      void refresh();
      poller.start();
      document.addEventListener('visibilitychange', onVisibility);
      return () => {
        poller.stop();
        document.removeEventListener('visibilitychange', onVisibility);
        generation += 1;
      };
    },
  };
}

export type Resource<T> = ReturnType<typeof createResource<T>>;
