// Messquellen eines Raums (rein, getestet): Der Raum verwendet den Mittelwert der brauchbaren
// Fühler; die Liste hier ist nur Auswahl und Beschriftung, entschieden wird auf dem Server.
import type { ZoneDevices } from './types';

export const SOURCES_HINT =
  'Mehrere Fühler je Raum sind möglich. Der Raum nutzt den Mittelwert der brauchbaren Fühler; ein ausgefallener Fühler zählt nicht mit.';
export const NO_SOURCE_TEXT = 'Keine Messquelle gesetzt.';

/** Geräte, die noch nicht Messquelle sind: nur diese bietet das Hinzufügen an. */
export function addableSources(data: Pick<ZoneDevices, 'devices' | 'temperature_sources'>): { id: number; name: string }[] {
  const taken = new Set(data.temperature_sources.map((s) => s.id));
  return data.devices.filter((d) => !taken.has(d.id)).map((d) => ({ id: d.id, name: d.name }));
}

/** Überschrift der Liste mit Anzahl, wenn es mehr als einen Fühler gibt. */
export const sourcesTitle = (count: number): string => (count > 1 ? `Messquellen (${count})` : 'Messquellen');

/** Zweistufiges Entfernen: erster Klick fragt nach, zweiter löst. */
export function removeStep(confirming: number | null, id: number): 'confirm' | 'remove' {
  return confirming === id ? 'remove' : 'confirm';
}
