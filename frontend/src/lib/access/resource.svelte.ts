// Lesender Ladezustand einer Ansicht: laden, Fehler zeigen, bei Neulesen die alte Ansicht behalten.
import { accessFailure } from './errors';

export function createResource<T>(fetcher: () => Promise<T>) {
  let data = $state<T | null>(null);
  let error = $state<string | null>(null);
  let forbidden = $state(false);
  let generation = 0;

  async function load() {
    const mine = ++generation;
    try {
      const value = await fetcher();
      if (mine !== generation) return;
      data = value;
      error = null;
      forbidden = false;
    } catch (e) {
      if (mine !== generation) return;
      const f = accessFailure(e);
      forbidden = f.kind === 'forbidden';
      error = f.message;
    }
  }
  return {
    get data() {
      return data;
    },
    get error() {
      return error;
    },
    /** Der Server hat den Zugriff verweigert: Ansicht als „nicht erlaubt“ zeigen, nicht als Störung. */
    get forbidden() {
      return forbidden;
    },
    load,
    set(value: T) {
      generation += 1;
      data = value;
    },
    stop() {
      generation += 1;
    },
  };
}
