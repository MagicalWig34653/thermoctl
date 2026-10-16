// Abbildung von Serverfehlern auf Meldungen und Folgen für die Ansicht (rein, getestet).
// Grundsatz: Nach jedem Fehler gilt wieder der Serverstand (die Ansicht lädt neu), außer beim
// Versionskonflikt -- dort entscheidet die Person per „Neu laden“, weil sie sonst
// ihren Bearbeitungsstand unbemerkt verliert.

import { ApiError } from '../api';

export type FailureKind =
  | 'conflict' // 409 version_conflict
  | 'overlap' // 409 overlap
  | 'stale' // 409 undo_stale
  | 'validation' // 422
  | 'forbidden' // 403
  | 'missing' // 404
  | 'session' // 401 / ungültige Sitzung
  | 'network' // Ausgang unklar
  | 'other';

export interface ScheduleFailure {
  kind: FailureKind;
  message: string;
  field: string | null;
  /** Nach diesem Fehler den Serverstand neu lesen (Lesen ist gefahrlos). */
  reload: boolean;
}

export const CONFLICT_MESSAGE = 'Der Plan wurde in einer anderen Sitzung geändert.';
export const STALE_MESSAGE =
  'Die Rücknahme ist nicht mehr möglich: Der Plan wurde inzwischen geändert.';
export const NETWORK_MESSAGE =
  'Der Server hat nicht geantwortet. Ob die Änderung gespeichert wurde, ist unklar -- bitte den Plan prüfen.';

export function scheduleFailure(error: unknown): ScheduleFailure {
  if (!(error instanceof ApiError)) {
    return { kind: 'other', message: 'Unerwarteter Fehler.', field: null, reload: true };
  }
  const { code, field, status } = error;
  if (error.kind === 'network') {
    return { kind: 'network', message: NETWORK_MESSAGE, field: null, reload: true };
  }
  if (error.kind === 'unauthorized' || error.stale) {
    return { kind: 'session', message: error.message, field: null, reload: false };
  }
  if (status === 409 && code === 'version_conflict') {
    return { kind: 'conflict', message: CONFLICT_MESSAGE, field, reload: false };
  }
  if (status === 409 && code === 'undo_stale') {
    return { kind: 'stale', message: STALE_MESSAGE, field, reload: true };
  }
  if (status === 409 && code === 'overlap') {
    return { kind: 'overlap', message: error.message, field, reload: true };
  }
  if (status === 422) return { kind: 'validation', message: error.message, field, reload: true };
  if (status === 403) return { kind: 'forbidden', message: error.message, field, reload: true };
  if (status === 404) return { kind: 'missing', message: error.message, field, reload: true };
  return { kind: 'other', message: error.message || `Fehler ${status}`, field, reload: true };
}
