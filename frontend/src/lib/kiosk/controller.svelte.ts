// Zustand der Kioskansicht: Lesen im 20-s-Takt, drei schreibende Aktionen je Raum.
//
// Regeln, die hier gelten und nicht im Aussehen stecken:
//  - Ein schreibender Aufruf wird nie wiederholt, auch nicht nach einem Netzfehler. Stattdessen
//    wird danach der Zustand gelesen, damit die Anzeige zeigt, was wirklich gilt.
//  - Je Raum läuft höchstens ein schreibender Aufruf; weitere Taps sind währenddessen gesperrt.
//  - Eine Leseantwort, die vor einem Schreibvorgang gestartet wurde, überschreibt nichts
//    Neueres (`epoch`).
//  - 401 (Zugang ungültig) hält den Takt an und sperrt die Ansicht. Es gibt keine Anmeldung.

import { createPoller } from '../rooms/poller';
import type { KioskClient } from './client';
import { describeFailure, replaceRoom, POLL_INTERVAL_MS, type KioskAction } from './logic';
import type { KioskRoom, KioskState } from './types';

export type KioskStatus = 'loading' | 'ready' | 'invalid' | 'unavailable';

export interface KioskDeps {
  client: KioskClient;
  isHidden: () => boolean;
  intervalMs?: number;
}

export function createKiosk(deps: KioskDeps) {
  let status = $state<KioskStatus>('loading');
  let data = $state<KioskState | null>(null);
  let lastOk = $state<string | null>(null);
  let offline = $state(false);
  let notice = $state<string | null>(null);
  let announcement = $state('');
  let pending = $state<Record<number, KioskAction | undefined>>({});
  let errors = $state<Record<number, string | undefined>>({});
  let epoch = 0;

  function lock(message: string) {
    status = 'invalid';
    notice = message;
    poller.stop();
  }

  async function load(): Promise<void> {
    const started = epoch;
    try {
      const next = await deps.client.state();
      if (started !== epoch) return; // ein Schreibvorgang war schneller und neuer
      data = next;
      lastOk = next.now;
      offline = false;
      notice = null;
      status = 'ready';
    } catch (error) {
      const outcome = describeFailure(error, 'load');
      if (outcome.invalid) return lock(outcome.message);
      if (data === null) {
        status = 'unavailable';
        notice = outcome.message;
      } else {
        offline = true;
        notice = outcome.message;
      }
    }
  }

  const poller = createPoller({
    run: load,
    intervalMs: deps.intervalMs ?? POLL_INTERVAL_MS,
    isHidden: deps.isHidden,
  });

  async function act(
    room: KioskRoom,
    action: KioskAction,
    call: () => Promise<KioskRoom>,
    done: (updated: KioskRoom) => string,
  ): Promise<void> {
    if (status !== 'ready' || pending[room.id] !== undefined) return;
    pending[room.id] = action;
    errors[room.id] = undefined;
    try {
      const updated = await call();
      epoch += 1;
      if (data !== null) data = { ...data, rooms: replaceRoom(data.rooms, updated) };
      announcement = done(updated);
    } catch (error) {
      const outcome = describeFailure(error, action);
      if (outcome.invalid) {
        lock(outcome.message);
      } else {
        errors[room.id] = outcome.message;
        epoch += 1;
        if (outcome.refresh) void load();
      }
    } finally {
      pending[room.id] = undefined;
    }
  }

  return {
    get status() {
      return status;
    },
    get data() {
      return data;
    },
    get lastOk() {
      return lastOk;
    },
    get offline() {
      return offline;
    },
    get notice() {
      return notice;
    },
    get announcement() {
      return announcement;
    },
    pendingFor: (id: number) => pending[id],
    errorFor: (id: number) => errors[id],
    dismissError(id: number) {
      errors[id] = undefined;
    },
    /** Erste Ladung und Takt. */
    start() {
      void load();
      poller.start();
    },
    stop() {
      poller.stop();
    },
    visibilityChanged() {
      poller.visibilityChanged();
    },
    reload: load,
    step: (room: KioskRoom, direction: 'up' | 'down') =>
      act(
        room,
        'step',
        () => deps.client.step(room.id, direction),
        (u) => `${u.name}: Sollwert ${u.setpoint.temperature.replace('.', ',')} Grad`,
      ),
    boost: (room: KioskRoom) =>
      act(room, 'boost', () => deps.client.boost(room.id), (u) => `${u.name}: nächste Schaltung vorgezogen`),
    cancelOverride: (room: KioskRoom) =>
      act(room, 'cancel', () => deps.client.cancelOverride(room.id), (u) => `${u.name}: zurück zum Zeitplan`),
  };
}

export type Kiosk = ReturnType<typeof createKiosk>;
