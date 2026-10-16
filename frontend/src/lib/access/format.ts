// Darstellung von Zeitpunkten und Zuständen der Zugangsansichten.

/**
 * Zeitpunkt in der angegebenen Zeitzone (die der Anlage; vom Server geliefert). Ohne oder mit
 * unbekannter Zeitzone gilt die des Browsers, damit die Anzeige nie ausfällt.
 */
export function formatDateTime(iso: string | null, never = '—', timeZone?: string | null): string {
  if (iso === null || iso === '') return never;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return never;
  const options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' };
  if (timeZone) {
    try {
      return new Intl.DateTimeFormat('de-DE', { ...options, timeZone }).format(d);
    } catch {
      // Unbekannte Zeitzone: weiter mit der des Browsers.
    }
  }
  return new Intl.DateTimeFormat('de-DE', options).format(d);
}

export type TokenTone = 'ok' | 'muted' | 'warn';

export function tokenState(state: string): { label: string; tone: TokenTone } {
  if (state === 'active') return { label: 'aktiv', tone: 'ok' };
  if (state === 'revoked') return { label: 'widerrufen', tone: 'muted' };
  if (state === 'expired') return { label: 'abgelaufen', tone: 'warn' };
  return { label: state, tone: 'muted' };
}

export function sourceLabel(source: string, clientName: string | null): string {
  if (source === 'oauth') return clientName ? `Zustimmung für „${clientName}“` : 'Über OAuth freigegeben';
  return 'Selbst ausgestellt';
}

/** Kurzform des Browsers aus dem User-Agent; leer, wenn nichts Brauchbares darin steht. */
export function deviceLabel(userAgent: string | null): string {
  if (!userAgent) return 'Unbekanntes Gerät';
  const ua = userAgent;
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : null;
  const system = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : null;
  if (browser && system) return `${browser} auf ${system}`;
  return browser ?? system ?? 'Unbekanntes Gerät';
}

export const VALID_DAYS = [
  { value: '', label: 'Unbegrenzt' },
  { value: '30', label: '30 Tage' },
  { value: '90', label: '90 Tage' },
  { value: '365', label: '1 Jahr' },
] as const;

export function validDays(value: string): number | null {
  return value === '' ? null : Number(value);
}

export const PRESET_LABELS: Record<string, string> = {
  read_only: 'Nur lesen',
  read_write: 'Lesen und ändern',
};
export const presetLabel = (name: string): string => PRESET_LABELS[name] ?? name;
