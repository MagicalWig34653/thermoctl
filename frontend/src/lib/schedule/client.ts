// Der gesamte Serververtrag des Wochenplan-Editors an EINER Stelle.
//
// Vorhanden und geprüft (thermoctl/web/ui_api_schedule.py): GET /schedule/{z}, /preview, paint,
// copy-day, copy-week, undo, /vacation, /absence, /modes.
//
// ANGENOMMEN (Branch ui/api-blocks, zur Bauzeit nicht einsehbar): die vier Block-Absichten unten
// (`BLOCK_ENDPOINTS`) samt Körperform (`blockBody`) und Rücknahme-Marken in der Antwort
// (`undoTokensOf`). Weicht der Server ab, wird nur diese Datei angepasst.
//
//   POST /ui-api/schedule/{z}/block/move       {version, weekday, start_minute, end_minute, to_weekday, to_start_minute}
//   POST /ui-api/schedule/{z}/block/duplicate  (gleiche Felder, die Quelle bleibt)
//   POST /ui-api/schedule/{z}/block/resize     {version, weekday, start_minute, end_minute, new_start_minute, new_end_minute, adjacent?}
//   POST /ui-api/schedule/{z}/block/delete     {version, weekday, start_minute, end_minute}
//   Antwort: neuer Plan (wie GET) plus `undo_token` (oder `undo_tokens`); Fehler 409 version_conflict /
//   overlap / undo_stale, 422 mit `field`, 403, 404.
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

/** Marke(n) aus einer Antwort: `undo_tokens` (Liste) oder `undo_token` (einzeln). */
export function undoTokensOf(raw: unknown): string[] {
  if (typeof raw !== 'object' || raw === null) return [];
  const r = raw as { undo_token?: unknown; undo_tokens?: unknown };
  if (Array.isArray(r.undo_tokens)) return r.undo_tokens.filter((t): t is string => typeof t === 'string');
  return typeof r.undo_token === 'string' && r.undo_token ? [r.undo_token] : [];
}

/** Pfad (unter `/ui-api/schedule/{z}`) und Körper einer Absicht. Rein, damit prüfbar. */
export function requestOf(intent: Intent, version: string): { path: string; body: object } {
  switch (intent.type) {
    case 'paint':
      return {
        path: 'paint',
        body: {
          version,
          weekday: intent.weekday,
          start_minute: intent.start,
          end_minute: intent.end,
          mode_id: intent.modeId,
        },
      };
    case 'move':
    case 'duplicate':
      return {
        path: BLOCK_ENDPOINTS[intent.type],
        body: {
          version,
          weekday: intent.block.weekday,
          start_minute: intent.block.start,
          end_minute: intent.block.end,
          to_weekday: intent.toWeekday,
          to_start_minute: intent.toStart,
        },
      };
    case 'resize':
      return {
        path: BLOCK_ENDPOINTS.resize,
        body: {
          version,
          weekday: intent.block.weekday,
          start_minute: intent.block.start,
          end_minute: intent.block.end,
          new_start_minute: intent.newStart,
          new_end_minute: intent.newEnd,
          ...(intent.adjacent ? { adjacent: true } : {}),
        },
      };
    case 'delete':
      return {
        path: BLOCK_ENDPOINTS.delete,
        body: {
          version,
          weekday: intent.block.weekday,
          start_minute: intent.block.start,
          end_minute: intent.block.end,
        },
      };
    case 'copyDay':
      return { path: 'copy-day', body: { version, weekday: intent.weekday, scope: intent.scope } };
    case 'copyWeek':
      return { path: 'copy-week', body: { version, targets: intent.targets } };
  }
}

function change(raw: PlanChange & { undo_token?: string | null }): PlanChange {
  return { ...raw, undo_tokens: undoTokensOf(raw) };
}

export function createScheduleClient(api: Api) {
  return {
    plan: (zoneId: number) => api.get<Plan>(base(zoneId)),
    preview: (zoneId: number) => api.get<Preview>(`${base(zoneId)}/preview`),
    async send(zoneId: number, intent: Intent, version: string): Promise<PlanChange> {
      const { path, body } = requestOf(intent, version);
      return change(await api.post(`${base(zoneId)}/${path}`, body));
    },
    /** Eine Marke zurücknehmen (der Server prüft, dass niemand danach etwas geändert hat). */
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
