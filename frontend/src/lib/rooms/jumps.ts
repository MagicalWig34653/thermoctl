// Sprunglinks vom Raumdetail zu den Seiten, die zu einem Raum gehören (Vorbild: der
// Zonenkopf der alten Oberfläche). Die Sichtbarkeit kommt aus den sichtbaren Bereichen des
// Bootstraps; ein Link, dessen Seite für dieses Konto nicht erscheint, wird nicht angeboten.

import type { VisibleArea } from '../navigation';

export interface RoomJump {
  label: string;
  /** Pfad innerhalb der Oberfläche mit Raumvorwahl, für `appHref`. */
  to: string;
}

const JUMPS: readonly { areaId: string; pageId: string; label: string; query: string }[] = [
  { areaId: 'zeit', pageId: 'wochenplaene', label: 'Zeitplan', query: '' },
  { areaId: 'geraete', pageId: 'liste', label: 'Geräte dieses Raums', query: '' },
  { areaId: 'geraete', pageId: 'karte', label: 'Datenfluss', query: '&tab=datenfluss' },
  { areaId: 'auswertung', pageId: 'verlauf', label: 'Verlauf', query: '' },
];

export function roomJumps(areas: readonly VisibleArea[], roomId: number): RoomJump[] {
  if (!Number.isInteger(roomId) || roomId <= 0) return [];
  return JUMPS.flatMap((j) => {
    const visible = areas.find((a) => a.id === j.areaId)?.pages.some((p) => p.id === j.pageId);
    return visible ? [{ label: j.label, to: `/${j.areaId}/${j.pageId}?zone=${roomId}${j.query}` }] : [];
  });
}
