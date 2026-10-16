// Darstellungslogik für Heizzeit, Verschleiß und Ereignisse (ohne Svelte).

import { ApiError } from '../api';
import type { RevertConflict } from './types';

/** `{ a: '1', b: '' , c: undefined }` wird zu `?a=1`; leere Werte entfallen. */
export function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    q.set(key, String(value));
  }
  const text = q.toString();
  return text ? `?${text}` : '';
}

/** Obergrenze des Suchtexts; der Server lehnt Längeres mit 422 ab. */
export const AUDIT_TEXT_MAX = 100;

/** Suchtext für die Abfrage: Ränder weg, auf die Obergrenze gekürzt. */
export function auditSearchText(text: string): string {
  return text.trim().slice(0, AUDIT_TEXT_MAX);
}

/** Der Objektfilter: ein gewählter Raum (`zone:ID`) geht dem Freitext vor. */
export function auditObjectFilter(zoneId: string, objectText: string): string {
  return /^\d+$/.test(zoneId) ? `zone:${zoneId}` : objectText.trim();
}

export interface AuditFilter {
  fromDate: string;
  toDate: string;
  user: string;
  actionCode: string;
  zoneId: string;
  objectText: string;
  source: string;
  text: string;
}

/** Abfragetext der gefilterten Ereignisse; Seite nur für die Liste, nicht für die CSV. */
export function auditQuery(filter: AuditFilter, page?: number): string {
  return buildQuery({
    from_date: filter.fromDate,
    to_date: filter.toDate,
    user: filter.user,
    action_code: filter.actionCode,
    object: auditObjectFilter(filter.zoneId, filter.objectText),
    source: filter.source,
    q: auditSearchText(filter.text),
    page,
  });
}

/** 5400 wird zu „1 Std. 30 Min.“; unter einer Minute „< 1 Min.“; 0 „0 Min.“. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0 Min.';
  if (seconds < 60) return '< 1 Min.';
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} Min.`;
  return m === 0 ? `${h} Std.` : `${h} Std. ${m} Min.`;
}

export type RevertOutcome =
  | { kind: 'conflict'; message: string; conflicts: RevertConflict[]; forceToken: string }
  | { kind: 'message'; message: string; tone: 'error' | 'warn'; session: boolean };

function asConflicts(value: unknown): RevertConflict[] | null {
  if (!Array.isArray(value)) return null;
  const out: RevertConflict[] = [];
  for (const item of value) {
    if (item === null || typeof item !== 'object') return null;
    const r = item as Record<string, unknown>;
    if (typeof r.field !== 'string') return null;
    const text = (v: unknown) => (typeof v === 'string' ? v : v === null || v === undefined ? null : String(v));
    out.push({ field: r.field, current: text(r.current), expected: text(r.expected) });
  }
  return out;
}

/**
 * Ordnet den Fehler einer Rücknahme ein. Nur ein 409 mit `code: conflict`, lesbaren `conflicts`
 * UND einem `force_token` darf das Erzwingen anbieten -- fehlt eines davon, bleibt es bei einer
 * Fehlermeldung. Es gibt keinen automatischen zweiten Versuch.
 */
export function classifyRevertError(error: unknown): RevertOutcome {
  if (!(error instanceof ApiError)) {
    return { kind: 'message', message: 'Unerwarteter Fehler.', tone: 'error', session: false };
  }
  if (error.kind === 'unauthorized' || error.stale) {
    return {
      kind: 'message',
      message: 'Die Sitzung ist abgelaufen. Bitte erneut anmelden – die Änderung wurde nicht zurückgenommen.',
      tone: 'error',
      session: true,
    };
  }
  if (error.kind === 'network') {
    return {
      kind: 'message',
      message:
        'Der Server ist nicht erreichbar. Ob die Rücknahme angekommen ist, ist unklar – bitte erst die Liste neu laden und prüfen, bevor du es erneut versuchst.',
      tone: 'warn',
      session: false,
    };
  }
  if (error.status === 409 && error.code === 'conflict') {
    const conflicts = asConflicts(error.body?.conflicts);
    const token = error.body?.force_token;
    if (conflicts && typeof token === 'string' && token !== '') {
      return { kind: 'conflict', message: error.message, conflicts, forceToken: token };
    }
  }
  return { kind: 'message', message: error.message || 'Die Rücknahme ist fehlgeschlagen.', tone: 'error', session: false };
}

/** Verschleißstufe des Servers (`normal`, `warning`, `danger`); Unbekanntes bleibt wie geliefert. */
export function wearLabel(level: string): { label: string; tone: 'ok' | 'warn' | 'high' } {
  switch (level) {
    case 'normal':
      return { label: 'Unauffällig', tone: 'ok' };
    case 'warning':
      return { label: 'Erhöht', tone: 'warn' };
    case 'danger':
      return { label: 'Über der Lebensdauer', tone: 'high' };
    default:
      return { label: level || '—', tone: 'warn' };
  }
}

/** Datum `2026-10-16` als `16.10.2026`. */
export function formatWearDay(iso: string): string {
  return iso.split('-').reverse().join('.');
}
