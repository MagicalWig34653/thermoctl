// Einmaliges oder wiederholt angestoßenes Lesen mit Lade- und Fehlerzustand. Der letzte Stand
// bleibt bei einem Fehler stehen; ältere Antworten überschreiben nie neuere.
import { toFailure, type Failure } from '../rooms/logic';

export function createQuery<T>(fetcher: () => Promise<T>) {
  let data = $state<T | null>(null);
  let failure = $state<Failure | null>(null);
  let loading = $state(false);
  let generation = 0;

  async function load(): Promise<void> {
    const mine = ++generation;
    loading = true;
    try {
      const value = await fetcher();
      if (mine !== generation) return;
      data = value;
      failure = null;
    } catch (error) {
      if (mine !== generation) return;
      failure = toFailure(error);
    } finally {
      if (mine === generation) loading = false;
    }
  }

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
    load,
    /** Beim Verlassen der Seite: laufende Antworten verwerfen. */
    cancel() {
      generation += 1;
    },
  };
}

export type Query<T> = ReturnType<typeof createQuery<T>>;
