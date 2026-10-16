// Der gesamte Serververtrag des Wochenplan-Editors an EINER Stelle
// (thermoctl/web/ui_api_schedule.py, geprüft gegen den echten Server).
//
//   GET  /ui-api/schedule/{z}, /preview
//   POST /ui-api/schedule/{z}/paint               {version, weekday, start_minute, end_minute, mode_id}
//   POST /ui-api/schedule/{z}/block/move|duplicate {version, weekday, start_minute, end_minute, to_weekday, to_start_minute}
//   POST /ui-api/schedule/{z}/block/resize        {..., new_start_minute, new_end_minute, adjacent?}
//   POST /ui-api/schedule/{z}/block/delete        {version, weekday, start_minute, end_minute}
//   PUT  /ui-api/schedule/{z}/points/{id}/mode    {version, mode_id}   (Modus eines ganzen Blocks)
//   POST /ui-api/schedule/{z}/day-times           {version, weekday, first_minute, second_minute}   (zwei Zeiten eines Tages)
//   POST /ui-api/schedule/{z}/copy-day, copy-week, undo
//   Jede Schreibanfrage (außer copy-week) nimmt `previous_undo_token`: die zuletzt erhaltene Marke
//   verlängert die Rücknahme-Kette. Antwort: neuer Plan plus `undo_token` (oder null); `undo` liefert
//   die Marke für den nächsten Schritt oder null. `end_minute` zählt über 1440 hinaus (bis start + 10080).
//   Fehler: 409 version_conflict / overlap (Feld time_range) / undo_stale, 404 block_not_found, 422 mit `field`, 403.
//
// Schreibende Aufrufe werden nie wiederholt (api.ts).

import type { Api } from '../api';
import type { Intent } from './intents';
import type {
  AbsenceState,
  ModeAdmin,
  Modes,
  Plan,
  PlanChange,
  Preview,
  VacationState,
} from './types';

export const BLOCK_ENDPOINTS = {
  move: 'block/move',
  duplicate: 'block/duplicate',
  resize: 'block/resize',
  delete: 'block/delete',
} as const;

const base = (zoneId: number) => `/ui-api/schedule/${zoneId}`;

/** Rücknahme-Marke einer Antwort (oder null: nichts zurückzunehmen). */
export function undoTokenOf(raw: unknown): string | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const token = (raw as { undo_token?: unknown }).undo_token;
  return typeof token === 'string' && token ? token : null;
}

export interface Request {
  method: 'POST' | 'PUT';
  path: string;
  body: object;
}

/** Methode, Pfad (unter `/ui-api/schedule/{z}`) und Körper einer Absicht. Rein, damit prüfbar. */
export function requestOf(intent: Intent, version: string, previousUndoToken: string | null = null): Request {
  const chain = previousUndoToken ? { previous_undo_token: previousUndoToken } : {};
  const make = (path: string, body: object, method: Request['method'] = 'POST'): Request => ({
    method,
    path,
    body: { version, ...body, ...chain },
  });
  switch (intent.type) {
    case 'paint':
      return make('paint', {
        weekday: intent.weekday,
        start_minute: intent.start,
        end_minute: intent.end,
        mode_id: intent.modeId,
      });
    case 'move':
    case 'duplicate':
      return make(BLOCK_ENDPOINTS[intent.type], {
        weekday: intent.block.weekday,
        start_minute: intent.block.start,
        end_minute: intent.block.end,
        to_weekday: intent.toWeekday,
        to_start_minute: intent.toStart,
      });
    case 'resize':
      return make(BLOCK_ENDPOINTS.resize, {
        weekday: intent.block.weekday,
        start_minute: intent.block.start,
        end_minute: intent.block.end,
        new_start_minute: intent.newStart,
        new_end_minute: intent.newEnd,
        ...(intent.adjacent ? { adjacent: true } : {}),
      });
    case 'delete':
      return make(BLOCK_ENDPOINTS.delete, {
        weekday: intent.block.weekday,
        start_minute: intent.block.start,
        end_minute: intent.block.end,
      });
    case 'setMode':
      return make(`points/${intent.pointId}/mode`, { mode_id: intent.modeId }, 'PUT');
    case 'dayTimes':
      return make('day-times', { weekday: intent.weekday, first_minute: intent.first, second_minute: intent.second });
    case 'copyDay':
      return make('copy-day', { weekday: intent.weekday, scope: intent.scope });
    case 'copyWeek':
      // Ohne Kette: `copy-week` nimmt keine `previous_undo_token` und liefert keine Marke.
      return { method: 'POST', path: 'copy-week', body: { version, targets: intent.targets } };
  }
}

function change(raw: PlanChange): PlanChange {
  return { ...raw, undo_token: undoTokenOf(raw) };
}

export function createScheduleClient(api: Api) {
  return {
    plan: (zoneId: number) => api.get<Plan>(base(zoneId)),
    preview: (zoneId: number) => api.get<Preview>(`${base(zoneId)}/preview`),
    async send(
      zoneId: number,
      intent: Intent,
      version: string,
      previousUndoToken: string | null = null,
    ): Promise<PlanChange> {
      const { method, path, body } = requestOf(intent, version, previousUndoToken);
      const url = `${base(zoneId)}/${path}`;
      return change(await (method === 'PUT' ? api.put<PlanChange>(url, body) : api.post<PlanChange>(url, body)));
    },
    /** Eine Marke zurücknehmen (der Server prüft, dass niemand danach etwas geändert hat). Die Antwort
     *  trägt die Marke für den nächsten Schritt zurück (oder null am Ende der Kette). */
    async undo(zoneId: number, token: string): Promise<PlanChange> {
      return change(await api.post(`${base(zoneId)}/undo`, { undo_token: token }));
    },

    vacation: () => api.get<VacationState>('/ui-api/vacation'),
    setVacation: (body: { start_date: string; end_date: string; setback_temperature_c: string }) =>
      api.post<VacationState>('/ui-api/vacation', body),
    endVacation: () => api.delete<VacationState>('/ui-api/vacation'),

    absence: () => api.get<AbsenceState>('/ui-api/absence'),
    startAbsence: (body: { temperature_c: string; return_on: string }) =>
      api.post<AbsenceState>('/ui-api/absence', body),
    endAbsence: () => api.post<AbsenceState>('/ui-api/absence/end'),

    modes: () => api.get<Modes>('/ui-api/modes'),
    addMode: (body: Pick<ModeAdmin, 'code' | 'name' | 'sort_order'>) =>
      api.post<Modes>('/ui-api/modes', body),
    renameMode: (id: number, body: Pick<ModeAdmin, 'code' | 'name' | 'sort_order'>) =>
      api.put<Modes>(`/ui-api/modes/${id}`, body),
    removeMode: (id: number) => api.delete<Modes>(`/ui-api/modes/${id}`),
  };
}

export type ScheduleClient = ReturnType<typeof createScheduleClient>;
