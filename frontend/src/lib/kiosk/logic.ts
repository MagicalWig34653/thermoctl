// Reine Logik der Kioskansicht (ohne Svelte, mit node:test prüfbar).

import { KioskError } from './client';
import type { KioskRoom } from './types';

/** Takt des Nachladens. Wie der HTML-Kiosk: alle 20 Sekunden. */
export const POLL_INTERVAL_MS = 20_000;

export type KioskAction = 'step' | 'boost' | 'cancel' | 'load';

export interface FailureOutcome {
  message: string;
  /** Zugang ungültig: Ansicht sperren, Takt anhalten, keine Anmeldeseite. */
  invalid: boolean;
  /** Danach den Zustand neu lesen, damit die Anzeige die Wahrheit zeigt. */
  refresh: boolean;
}

export const INVALID_TEXT =
  'Dieser Kiosk-Zugang ist nicht mehr gültig. Er wurde widerrufen oder ist abgelaufen. ' +
  'Bitte bei der Verwaltung einen neuen Zugang anfordern.';

const ACTION_LABEL: Record<KioskAction, string> = {
  step: 'Der Sollwert',
  boost: 'Das Vorziehen',
  cancel: 'Das Aufheben',
  load: 'Das Laden',
};

/** Verständlicher Text und Folgeverhalten für einen Fehler. Nichts davon enthält Rohmeldungen
 *  von Netz oder Server, außer dem fachlichen Ablehnungsgrund der Regelung (`rejected`). */
export function describeFailure(error: unknown, action: KioskAction): FailureOutcome {
  if (!(error instanceof KioskError)) {
    return { message: 'Unerwarteter Fehler. Die Anzeige wird neu geladen.', invalid: false, refresh: true };
  }
  const what = ACTION_LABEL[action];
  switch (error.kind) {
    case 'invalid':
      return { message: INVALID_TEXT, invalid: true, refresh: false };
    case 'forbidden':
      return {
        message: `${what} ist für diesen Zugang nicht erlaubt. Die Anzeige wird aktualisiert.`,
        invalid: false,
        refresh: true,
      };
    case 'not_found':
      return {
        message: 'Dieser Raum ist für diesen Zugang nicht mehr verfügbar. Die Anzeige wird aktualisiert.',
        invalid: false,
        refresh: true,
      };
    case 'csrf':
    case 'client':
      return {
        message: 'Der Sicherheitswert dieses Zugangs stimmt nicht. Bitte den Kiosk-Link erneut öffnen.',
        invalid: false,
        refresh: false,
      };
    case 'rejected':
      return { message: error.message, invalid: false, refresh: false };
    case 'setup':
      return { message: 'Die Anlage ist noch nicht fertig eingerichtet.', invalid: false, refresh: false };
    case 'network':
      return {
        message:
          action === 'load'
            ? 'Keine Verbindung zum Server. Es wird weiter versucht.'
            : `Keine Verbindung zum Server. ${what} wurde nicht bestätigt; die Anzeige wird geprüft.`,
        invalid: false,
        refresh: action !== 'load',
      };
    default:
      return {
        message: `${what} ließ sich nicht ausführen (Serverfehler ${error.status}). Die Anzeige wird geprüft.`,
        invalid: false,
        refresh: action !== 'load',
      };
  }
}

/** Ersetzt einen Raum anhand seiner Nummer; unbekannte Räume werden nicht hinzugefügt
 *  (die Liste bestimmt der Server beim Lesen). */
export function replaceRoom(rooms: readonly KioskRoom[], updated: KioskRoom): KioskRoom[] {
  return rooms.map((room) => (room.id === updated.id ? updated : room));
}

/** Alter eines Messwerts relativ zu `now` (beides Serverzeit), z. B. „vor 3 Min.“. */
export function ageText(measuredAt: string | null, now: string): string {
  if (measuredAt === null) return 'kein Messwert';
  const diff = Date.parse(now) - Date.parse(measuredAt);
  if (!Number.isFinite(diff)) return 'kein Messwert';
  const minutes = Math.floor(Math.max(diff, 0) / 60_000);
  if (minutes < 1) return 'gerade eben';
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  return `vor ${Math.floor(hours / 24)} Tg.`;
}

/** Uhrzeit `HH:MM` in der Zeitzone der Anlage; bei ungültiger Zone in der des Geräts. */
export function clockText(iso: string, timezone: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
  try {
    return new Intl.DateTimeFormat('de-DE', { ...options, timeZone: timezone }).format(date);
  } catch {
    return new Intl.DateTimeFormat('de-DE', options).format(date);
  }
}

/** Text zur laufenden Übersteuerung, z. B. „Übersteuert bis 14:30“. */
export function overrideText(override: KioskRoom['override'], timezone: string): string | null {
  if (override === null) return null;
  if (override.ends_at === null) return 'Übersteuert, ohne Ende';
  const until = clockText(override.ends_at, timezone);
  return until === '' ? 'Übersteuert' : `Übersteuert bis ${until}`;
}

/** Zustand der Heizanforderung als Text (nie nur als Farbe). */
export function heatText(wouldHeat: boolean | null): string {
  if (wouldHeat === true) return 'Heizanforderung';
  if (wouldHeat === false) return 'Aus';
  return 'Noch nicht entschieden';
}

export function isKioskActionable(room: KioskRoom): boolean {
  return room.actions.setpoint || room.actions.boost || room.actions.cancel_override;
}
