// Anzeigewerte der Kacheln aus den Antworten der Lese-Endpunkte. Gezählt wird nur, was der
// Server als Tatsache liefert (Entscheidung, Sensorzustand, Übersteuerung); keine Regel.

import { formatInstant, roomStatus } from '../rooms/logic';
import { formatTemperature } from '../rooms/decimal';
import type { Outdoor, Rooms } from '../rooms/types';
import { METRIC_LABELS, entryOf } from './catalog';
import type { Metric, Tile } from './types';

export interface MetricValue {
  title: string;
  value: string;
  note: string;
  available: boolean;
}

/** Außentemperatur der Kennzahl. Ein veralteter Wert oder fehlende Quelle sind eigene Texte statt einer
 *  Zahl, die man mit „draußen ist es warm“ verwechseln könnte; die Wohnungssicht bekommt keinen Wert. */
export function outdoorValue(title: string, outdoor: Outdoor | null): MetricValue {
  if (outdoor === null) return { title, value: '–', note: 'Hier gibt es keine Außenmessung.', available: false };
  if (outdoor.status === 'keine_quelle') return { title, value: '–', note: 'Keine Quelle gewählt', available: false };
  if (outdoor.status === 'veraltet' || outdoor.temperature_c === null) {
    return { title, value: '–', note: 'Wert veraltet', available: false };
  }
  return { title, value: `${formatTemperature(outdoor.temperature_c)} °C`, note: 'Außentemperatur der Anlage', available: true };
}

export function metricValue(metric: Metric, data: Rooms): MetricValue {
  const title = METRIC_LABELS[metric];
  const live = data.actuation.state === 'live';
  switch (metric) {
    case 'rooms':
      return { title, value: String(data.rooms.length), note: data.rooms.length === 1 ? 'Raum sichtbar' : 'Räume sichtbar', available: true };
    case 'heating': {
      const n = data.rooms.filter((r) => r.decision?.would_heat === true).length;
      return {
        title,
        value: String(n),
        note: live ? 'Räume fordern Wärme an' : 'Räume fordern Wärme an · Trockenlauf, nichts geschaltet',
        available: true,
      };
    }
    case 'issues': {
      const n = data.rooms.filter((r) => roomStatus(r).attention || r.emergency !== null).length;
      return { title, value: String(n), note: n === 0 ? 'Keine Auffälligkeiten' : n === 1 ? 'Raum mit Störung oder Fenster' : 'Räume mit Störung oder Fenster', available: true };
    }
    default:
      return outdoorValue(title, data.outdoor);
  }
}

export interface AgendaItem {
  roomId: number;
  roomName: string;
  text: string;
  tone: 'warn' | 'info';
}

/** „Heute relevant“: Auffälligkeiten und Übersteuerungen, wichtigste zuerst. */
export function agendaItems(data: Rooms, limit = 6): AgendaItem[] {
  const warn: AgendaItem[] = [];
  const info: AgendaItem[] = [];
  for (const room of data.rooms) {
    const status = roomStatus(room);
    if (status.attention) warn.push({ roomId: room.id, roomName: room.name, text: status.label, tone: 'warn' });
    if (room.emergency) warn.push({ roomId: room.id, roomName: room.name, text: room.emergency.headline, tone: 'warn' });
    if (room.override) {
      const until = room.override.ends_at ? `bis ${formatInstant(room.override.ends_at, data.timezone)}` : 'ohne Ende';
      info.push({ roomId: room.id, roomName: room.name, text: `Übersteuert ${until}`, tone: 'info' });
    }
  }
  return [...warn, ...info].slice(0, limit);
}

/** Titel der Kachel für Anzeige und Griffbeschriftung. */
export function tileTitle(tile: Pick<Tile, 'kind' | 'title'>): string {
  return tile.title.trim() || entryOf(tile.kind).name;
}
