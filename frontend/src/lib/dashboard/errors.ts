// Abbildung der Serverfehler des Dashboards auf Meldungen und Folgen (rein, getestet).
// Schreibende Aufrufe werden nie wiederholt (api.ts). Nach einem Fehler bleibt der Entwurf
// stehen; nur der Versionskonflikt bietet ausdrücklich ein Neuladen an, ohne ihn zu verwerfen.

import { ApiError } from '../api';

export type DashboardFailureKind =
  | 'conflict' // 409: anderswo geändert
  | 'validation' // 422
  | 'forbidden' // 403
  | 'session' // 401 / ungültige Sitzung
  | 'network' // Ausgang unklar
  | 'other';

export interface DashboardFailure {
  kind: DashboardFailureKind;
  message: string;
  field: string | null;
}

export const CONFLICT_MESSAGE = 'Das Layout wurde woanders geändert.';
export const NETWORK_MESSAGE =
  'Der Server hat nicht geantwortet. Ob gespeichert wurde, ist unklar – dein Entwurf bleibt erhalten. Bitte neu laden und prüfen, bevor du erneut speicherst.';

const FIELD_LABELS: Record<string, string> = {
  zone_id: 'Raum',
  metric: 'Kennzahl',
  destination: 'Ziel',
  kind: 'Kachelart',
  title: 'Titel',
  note: 'Notiz',
  version: 'Version',
};

export const fieldLabel = (field: string | null): string | null =>
  field === null ? null : (FIELD_LABELS[field] ?? field);

export function dashboardFailure(error: unknown): DashboardFailure {
  if (!(error instanceof ApiError)) return { kind: 'other', message: 'Unerwarteter Fehler.', field: null };
  const { code, field, status } = error;
  if (error.kind === 'network') return { kind: 'network', message: NETWORK_MESSAGE, field: null };
  if (error.kind === 'unauthorized' || error.stale) {
    return { kind: 'session', message: 'Die Sitzung ist abgelaufen. Bitte erneut anmelden – nichts wurde gespeichert.', field: null };
  }
  if (status === 409) {
    return { kind: 'conflict', message: CONFLICT_MESSAGE, field: field ?? (code === 'conflict' ? 'version' : null) };
  }
  if (status === 422) {
    const label = fieldLabel(field);
    return { kind: 'validation', message: label ? `${label}: ${error.message}` : error.message, field };
  }
  if (error.kind === 'forbidden') {
    return { kind: 'forbidden', message: error.message || 'Dafür fehlt die Berechtigung.', field };
  }
  return { kind: 'other', message: error.message || `Fehler ${status}`, field };
}
