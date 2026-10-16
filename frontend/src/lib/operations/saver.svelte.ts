// Zustand eines Speichervorgangs: läuft, Fehler, Erfolg. Ein Aufruf zur Zeit, nie automatisch
// wiederholt; Eingaben bleiben bei jedem Fehler unangetastet (der Aufrufer verändert sie nicht).
import { opsFailure, type OpsFailure } from './logic';

export function createSaver() {
  let busy = $state(false);
  let failure = $state<OpsFailure | null>(null);
  let done = $state<string | null>(null);
  return {
    get busy() {
      return busy;
    },
    get failure() {
      return failure;
    },
    get done() {
      return done;
    },
    /** Führt `action` genau einmal aus. Gibt das Ergebnis zurück, bei Fehler `undefined`. */
    async run<T>(action: () => Promise<T>, success: (result: T) => string | null): Promise<T | undefined> {
      if (busy) return undefined;
      busy = true;
      failure = null;
      done = null;
      try {
        const result = await action();
        done = success(result);
        return result;
      } catch (error) {
        failure = opsFailure(error);
        return undefined;
      } finally {
        busy = false;
      }
    },
    /** Meldungen verwerfen, sobald weitergearbeitet wird. */
    clear() {
      failure = null;
      done = null;
    },
  };
}

export type Saver = ReturnType<typeof createSaver>;
