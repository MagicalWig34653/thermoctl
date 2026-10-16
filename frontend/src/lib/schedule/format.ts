// Darstellungshilfen des Wochenplans (rein, getestet).

import type { ForecastSegment } from './types';

/**
 * Hinweis über dem Wochenplan, wenn man ihn nur ansehen darf. Nennt die Räume, in denen man
 * schreiben darf (aus `copy_targets`, also vom Server nach `schedule.manage` bestimmt).
 */
export function readOnlyNotice(plan: { copy_targets: { name: string }[] }): string {
  const base = 'Du kannst diesen Plan ansehen, aber nicht ändern.';
  if (!plan.copy_targets.length) return base;
  return `${base} Ändern kannst du den Plan in: ${plan.copy_targets.map((t) => t.name).join(', ')}.`;
}

/** „Europe/Berlin · MESZ (UTC+2)“ -- die Zeitzone der Anlage mit aktuellem Namen und Versatz. */
export function zoneLabel(timeZone: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('de-DE', { timeZone, timeZoneName: 'short' }).formatToParts(now);
    const name = parts.find((p) => p.type === 'timeZoneName')?.value;
    return name ? `${timeZone} · ${name}` : timeZone;
  } catch {
    return timeZone;
  }
}

/** Uhrzeit „HH:MM“ aus dem lokalen ISO-Zeitpunkt des Servers (`local_starts_at`), ohne Umrechnung. */
export function localClock(iso: string): string {
  const match = /T(\d{2}:\d{2})/.exec(iso);
  return match ? match[1] : '—';
}

export interface StripPart {
  left: number;
  width: number;
  segment: ForecastSegment;
}

/** Anteile der 24-Stunden-Vorschau; Breiten ergeben zusammen 100 %, auch bei krummer Gesamtdauer. */
export function stripParts(segments: readonly ForecastSegment[]): StripPart[] {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.duration_minutes), 0);
  if (total <= 0) return [];
  let used = 0;
  return segments
    .filter((s) => s.duration_minutes > 0)
    .map((segment) => {
      const left = (used / total) * 100;
      used += segment.duration_minutes;
      return { left, width: (segment.duration_minutes / total) * 100, segment };
    });
}
