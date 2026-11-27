// Anzeige des I-Anteils (PI-Regler), nur lesend: Raumseite, Verlauf und Regler-Kachel.

import { shareAsPercent } from '../operations/logic';
import { formatInstant } from './logic';
import type { PiPending, PiReset, Room } from './types';

export const NO_RESET_TEXT = 'Keiner im aufbewahrten Schaltprotokoll.';

const reasonOf = (reset: PiReset): string => reset.reason_label ?? reset.reason ?? 'ohne Angabe eines Grunds';

export function lastResetText(reset: PiReset | null, timeZone: string, now: Date = new Date()): string {
  if (reset === null) return NO_RESET_TEXT;
  return `${formatInstant(reset.at, timeZone, now)}: ${reasonOf(reset)}`;
}

export interface ResetMark {
  t: number;
  reason: string;
}

/** Resets im sichtbaren Ausschnitt (Ränder eingeschlossen). */
export function resetMarks(resets: readonly PiReset[] | null, view: { start: number; end: number }): ResetMark[] {
  return (resets ?? [])
    .map((r) => ({ t: new Date(r.at).getTime(), reason: reasonOf(r) }))
    .filter((m) => m.t >= view.start && m.t <= view.end);
}

/** Resets zum gewählten Fach: die darin liegenden, und für Zeiger auch solche innerhalb von
 *  `reachMs` neben der Fachmitte -- ein Fach ist oft schmaler als ein Pixel. */
export function resetsAt(
  marks: readonly ResetMark[],
  bucket: { start: number; end: number; t: number },
  reachMs: number,
): ResetMark[] {
  return marks.filter((m) => (m.t >= bucket.start && m.t < bucket.end) || Math.abs(m.t - bucket.t) <= reachMs);
}

export interface ControllerRow {
  id: number;
  name: string;
  /** 0..100 für den Balken; `null`: noch kein Wert gespeichert. */
  percent: number | null;
  text: string;
  reset: PiReset | null;
  /** Ziel in der Oberfläche: der Raum, Ansicht Technik. */
  path: string;
}

export function controllerRows(rooms: readonly Room[]): ControllerRow[] {
  return rooms.flatMap((room) => {
    if (!room.pi) return [];
    const value = room.pi.integral === null ? Number.NaN : Number(room.pi.integral);
    return [
      {
        id: room.id,
        name: room.name,
        percent: Number.isFinite(value) ? Math.round(Math.max(0, Math.min(1, value)) * 1000) / 10 : null,
        text: shareAsPercent(room.pi.integral),
        reset: room.pi.last_reset,
        path: `/raeume/${room.id}?ansicht=technik`,
      },
    ];
  });
}

/** Eingabe „I setzen“ in Prozent (wie die Karte sie zeigt) als Wert 0..1 für den Server.
 *  Der Server prüft den Bereich noch einmal; hier nur, damit ein Tippfehler gar nicht erst
 *  hingeschickt wird. Dezimalkomma und -punkt, höchstens vier Nachkommastellen, 0 bis 100. */
export function parseIntegralPercent(text: string): { ok: true; value: string } | { ok: false; message: string } {
  const bad = { ok: false, message: 'Bitte einen Wert von 0 bis 100 Prozent angeben.' } as const;
  const trimmed = text.trim();
  if (!/^\d{1,3}([.,]\d{1,4})?$/.test(trimmed)) return bad;
  const percent = Number(trimmed.replace(',', '.'));
  if (!Number.isFinite(percent) || percent < 0 || percent > 100) return bad;
  return { ok: true, value: (percent / 100).toFixed(6) };
}

export const PENDING_TEXT = 'Vorgemerkt, wird im nächsten Regelzyklus übernommen';

export function pendingText(pending: PiPending): string {
  const what = pending.reason === 'manuell_reset' ? 'I-Anteil zurücksetzen' : `I-Anteil auf ${shareAsPercent(pending.integral)} setzen`;
  return `${what}. ${PENDING_TEXT}.`;
}
