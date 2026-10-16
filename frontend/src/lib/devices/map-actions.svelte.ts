// Schreibende Handlungen der Gerätekarte, gemeinsam für Karte und Listenansicht: dieselben
// Aufrufe, dieselben Meldungen. Der Server entscheidet (zuerst `check`, dann `connect`); hier
// wird nichts zugelassen oder errechnet. Kein Wiederholen: Nach einem Fehler wird nur gelesen.
import { deviceFailure, failureText } from './errors';
import type { DevicesClient } from './client';
import { modeLabel } from './map-logic';
import type { DeviceMap } from './types';

export type Notice = { tone: 'error' | 'success' | 'info' | 'warn'; text: string };

export function createMapActions(deps: {
  client: DevicesClient;
  map: () => DeviceMap;
  /** Serverstand neu lesen (nach jedem Schreibversuch, auch nach einem Fehler). */
  reload: () => Promise<void>;
}) {
  let busy = $state(false);
  let notice = $state<Notice | null>(null);

  async function connect(a: string, b: string): Promise<boolean> {
    if (busy) return false;
    busy = true;
    notice = null;
    try {
      const verdict = await deps.client.check(a, b);
      if (!verdict.ok || !verdict.out_port || !verdict.in_port) {
        notice = { tone: 'warn', text: verdict.reason ?? 'Diese Anschlüsse passen nicht zusammen.' };
        return false;
      }
      const result = await deps.client.connect(verdict.out_port, verdict.in_port);
      const mode = modeLabel(deps.map(), result.mode);
      notice = { tone: 'success', text: mode ? `Verbunden. Betriebsart des Ventils: ${mode}.` : 'Verbunden.' };
      await deps.reload();
      return true;
    } catch (error) {
      const failure = deviceFailure(error);
      notice = { tone: failure.kind === 'session' ? 'error' : failure.isConflict ? 'warn' : 'error', text: failureText(failure) };
      if (failure.reload) await deps.reload();
      return false;
    } finally {
      busy = false;
    }
  }

  async function disconnect(connectionId: string): Promise<boolean> {
    if (busy) return false;
    busy = true;
    notice = null;
    try {
      const result = await deps.client.disconnect(connectionId);
      const mode = modeLabel(deps.map(), result.mode);
      // Der Text des Servers sagt schon, was gelöst wurde und was das für das Ventil heißt.
      const text = result.message || (mode ? `Verbindung gelöst. Betriebsart des Ventils: ${mode}.` : 'Verbindung gelöst.');
      notice = { tone: 'success', text };
      await deps.reload();
      return true;
    } catch (error) {
      const failure = deviceFailure(error);
      notice = { tone: failure.isConflict ? 'warn' : 'error', text: failureText(failure) };
      if (failure.reload) await deps.reload();
      return false;
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
    set notice(value: Notice | null) {
      notice = value;
    },
    connect,
    disconnect,
  };
}

export type MapActions = ReturnType<typeof createMapActions>;
