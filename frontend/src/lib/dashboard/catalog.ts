// Die elf Kacheltypen: Beschriftung, Kategorie, was sie brauchen. Reine Darstellungsdaten.
// Ob eine Kachel gezeigt werden darf, entscheidet der Server bei jedem Abruf.

import type { Destination, Metric, PeriodDays, TileKind, Width } from './types';

export type CategoryId = 'rooms' | 'time' | 'plant' | 'own';

export const CATEGORIES: readonly { id: CategoryId; label: string }[] = [
  { id: 'rooms', label: 'Räume' },
  { id: 'time', label: 'Verlauf & Zeit' },
  { id: 'plant', label: 'Anlage' },
  { id: 'own', label: 'Eigenes' },
];

export interface CatalogEntry {
  kind: TileKind;
  name: string;
  description: string;
  category: CategoryId;
  /** Braucht einen Raum (`zone_id`). */
  needsZone: boolean;
  /** Nur in der Anlagensicht (Verwaltungsprofil) sinnvoll; der Server entscheidet endgültig. */
  adminOnly: boolean;
  keywords: string;
  defaultWidth: Width;
}

export const CATALOG: readonly CatalogEntry[] = [
  { kind: 'room', name: 'Raum', description: 'Temperatur, Sollwert und Zustand eines Raums.', category: 'rooms', needsZone: true, adminOnly: false, keywords: 'zimmer ist soll temperatur', defaultWidth: 4 },
  { kind: 'control', name: 'Raum bedienen', description: 'Sollwert eines Raums direkt ändern und eine Übersteuerung beenden.', category: 'rooms', needsZone: true, adminOnly: false, keywords: 'thermostat bedienen sollwert wärmer kälter regeln steuern', defaultWidth: 4 },
  { kind: 'chart', name: 'Temperaturverlauf', description: 'Verlauf eines Raums über den gewählten Zeitraum.', category: 'time', needsZone: true, adminOnly: false, keywords: 'diagramm kurve historie', defaultWidth: 8 },
  { kind: 'metric', name: 'Kennzahl', description: 'Räume, Heizanforderung, Auffälligkeiten oder Außentemperatur.', category: 'plant', needsZone: false, adminOnly: false, keywords: 'zahl wert anzahl', defaultWidth: 3 },
  { kind: 'agenda', name: 'Heute relevant', description: 'Auffälligkeiten und nächste Wechsel deiner Räume.', category: 'rooms', needsZone: false, adminOnly: false, keywords: 'hinweise störung fenster übersteuert', defaultWidth: 4 },
  { kind: 'schedule', name: 'Nächste Schaltzeiten', description: 'Die kommenden Wechsel eines Raums in den nächsten 24 Stunden.', category: 'time', needsZone: true, adminOnly: false, keywords: 'wochenplan zeitplan wechsel', defaultWidth: 4 },
  { kind: 'heating', name: 'Heizzeit', description: 'Wie lange ein Raum Wärme angefordert hat.', category: 'time', needsZone: true, adminOnly: false, keywords: 'heizanforderung dauer stunden', defaultWidth: 4 },
  { kind: 'devices', name: 'Gerätestatus', description: 'Zugang zu Sensoren und Aktoren.', category: 'plant', needsZone: false, adminOnly: true, keywords: 'sensor aktor geräte', defaultWidth: 4 },
  { kind: 'system', name: 'Anlagenstatus', description: 'Freigabe, Versand und Primärrolle als drei getrennte Zustände.', category: 'plant', needsZone: false, adminOnly: true, keywords: 'trockenlauf mqtt freigabe führung betrieb', defaultWidth: 6 },
  { kind: 'note', name: 'Notiz', description: 'Eigener Text auf der Übersicht, nur für dich.', category: 'own', needsZone: false, adminOnly: false, keywords: 'text erinnerung hinweis', defaultWidth: 4 },
  { kind: 'shortcut', name: 'Schnellzugriff', description: 'Eine häufig genutzte Seite direkt öffnen.', category: 'own', needsZone: false, adminOnly: false, keywords: 'link verknüpfung seite', defaultWidth: 4 },
];

export const entryOf = (kind: TileKind): CatalogEntry => CATALOG.find((e) => e.kind === kind) ?? CATALOG[0];

export const WIDTH_LABELS: Record<Width, string> = {
  3: 'Viertel',
  4: 'Drittel',
  6: 'Halbe Breite',
  8: 'Zwei Drittel',
  12: 'Ganze Breite',
};

export const METRIC_LABELS: Record<Metric, string> = {
  rooms: 'Sichtbare Räume',
  heating: 'Heizanforderung',
  issues: 'Auffälligkeiten',
  outdoor: 'Außentemperatur',
};

export const DESTINATION_LABELS: Record<Destination, string> = {
  schedule: 'Wochenpläne',
  absence: 'Urlaub & Abwesenheit',
  history: 'Temperatur & Heizzeit',
  devices: 'Geräte',
  mapping: 'Gerätekarte',
  solar: 'Regelvorgaben (Sonne)',
  operation: 'Betrieb & Freigaben',
  account: 'Konto',
};

/** Ziel in der Oberfläche (relativ zu `/app`). */
export const DESTINATION_PATHS: Record<Destination, string> = {
  schedule: '/zeit/wochenplaene',
  absence: '/zeit/anlagenurlaub',
  history: '/auswertung/verlauf',
  devices: '/geraete/liste',
  mapping: '/geraete/karte',
  solar: '/heizung/regelvorgaben',
  operation: '/system/betrieb',
  account: '/konto',
};

/** Zeiträume je Kachelart: der Verlauf kennt 24 Std./7/30 Tage, die Heizzeit 7/30/90 Tage. */
export function periodsFor(kind: TileKind): readonly PeriodDays[] | null {
  if (kind === 'chart') return [1, 7, 30];
  if (kind === 'heating') return [7, 30, 90];
  return null;
}

export const PERIOD_LABELS: Record<PeriodDays, string> = {
  1: '24 Stunden',
  7: '7 Tage',
  30: '30 Tage',
  90: '90 Tage',
};

/** Gültiger Zeitraum für eine Art (nach Artwechsel oder aus einem fremden Layout). */
export function clampPeriod(kind: TileKind, days: PeriodDays): PeriodDays {
  const allowed = periodsFor(kind);
  if (!allowed) return 1;
  if (allowed.includes(days)) return days;
  return allowed.reduce((best, d) => (Math.abs(d - days) < Math.abs(best - days) ? d : best), allowed[0]);
}

export function matchesQuery(entry: CatalogEntry, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase('de');
  if (!needle) return true;
  const hay = `${entry.name} ${entry.description} ${entry.keywords}`.toLocaleLowerCase('de');
  return needle.split(/\s+/).every((w) => hay.includes(w));
}
