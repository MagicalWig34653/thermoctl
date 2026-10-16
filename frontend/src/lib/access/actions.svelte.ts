// Gemeinsamer Ablauf für schreibende Aktionen der Zugangsansichten: genau ein Aufruf zur Zeit,
// nie automatisch wiederholt, Fehler verständlich, bei veraltetem Stand wird neu gelesen.
import { accessFailure, type AccessFailure } from './errors';

export interface ActionNotice {
  tone: 'error' | 'success' | 'warn';
  text: string;
  /** Wo die Meldung hingehört (Bereich oder Zeile), damit sie nicht an der falschen Stelle steht. */
  where: string;
  failure?: AccessFailure;
}

export type ActionResult<T> = { ok: true; value: T } | { ok: false; failure: AccessFailure };

export function createActions(reload: () => Promise<void> | void) {
  let busy = $state(false);
  let notice = $state<ActionNotice | null>(null);

  async function run<T>(where: string, action: () => Promise<T>, success: string | null): Promise<ActionResult<T>> {
    if (busy) return { ok: false, failure: accessFailure(new Error('busy')) };
    busy = true;
    notice = null;
    try {
      const value = await action();
      if (success !== null) notice = { tone: 'success', text: success, where };
      return { ok: true, value };
    } catch (error) {
      const failure = accessFailure(error);
      notice = { tone: failure.kind === 'conflict' && failure.reload ? 'warn' : 'error', text: failure.message, where, failure };
      // Lesen ist gefahrlos; Schreiben wird nie wiederholt.
      if (failure.reload) await reload();
      return { ok: false, failure };
    } finally {
      busy = false;
    }
  }

  return {
    get busy() {
      return busy;
    },
    get notice() {
      return notice;
    },
    clear() {
      notice = null;
    },
    run,
  };
}

export type Actions = ReturnType<typeof createActions>;
