// Anzeigeformate des Verlaufs. Temperaturen werden nie in `number` gerundet: die Ziffern
// des Servers (Decimal-Strings) werden nur umgesetzt (Komma, Einheit).

import { compareDecimal, formatCelsius, formatTemperature } from '../rooms/decimal';
import type { Bucket } from './types';

export type PeriodId = '24h' | '7d' | '30d';

export const PERIODS: readonly { id: PeriodId; label: string; ms: number }[] = [
  { id: '24h', label: '24 Std.', ms: 86_400_000 },
  { id: '7d', label: '7 Tage', ms: 7 * 86_400_000 },
  { id: '30d', label: '30 Tage', ms: 30 * 86_400_000 },
];

export const periodMs = (id: PeriodId): number => (PERIODS.find((p) => p.id === id) ?? PERIODS[0]).ms;
export const periodLabel = (id: PeriodId): string => (PERIODS.find((p) => p.id === id) ?? PERIODS[0]).label;

/** „20,5 °C“ bei gleichen Grenzen, sonst „20,5–21,0 °C“; ohne Wert ein Gedankenstrich. */
export function formatRange(min: string | null, max: string | null): string {
  if (min === null || max === null) return '—';
  if (compareDecimal(min, max) === 0) return formatCelsius(min);
  return `${formatTemperature(min)}–${formatCelsius(max)}`;
}

export function formatSetback(value: string | null): string {
  if (value === null) return 'unbekannt';
  const c = compareDecimal(value, '0');
  if (c === null) return 'unbekannt';
  if (c === 0) return 'keine';
  return `${formatTemperature(value)} K`;
}

export function formatHeat(percent: number | null): string {
  return percent === null ? '—' : `${percent} %`;
}

/** „Abschnitte à 2 Min.“ */
export function formatResolution(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} Tag(e)`;
  if (seconds % 3600 === 0) return `${seconds / 3600} Std.`;
  if (seconds % 60 === 0) return `${seconds / 60} Min.`;
  return `${seconds} Sek.`;
}

/** Eine Zeile des Verlaufs als Text, für Tooltip-Alternative und Vorlesen. */
export function describeBucket(b: Bucket, when: string): string {
  if (b.rows === 0) return `${when}: Messlücke, keine Messwerte.`;
  return (
    `${when}: Messwert ${formatCelsius(b.temperature_c)}, ` +
    `Wirksamer Sollwert ${formatRange(b.effective_min_c, b.effective_max_c)}, ` +
    `Geplanter Sollwert ${formatRange(b.scheduled_min_c, b.scheduled_max_c)}, ` +
    `Heizanforderung ${formatHeat(b.heat_percent)}.`
  );
}
