// Lesende Hilfen der Karte auf den Serverdaten (rein, getestet). Entschieden wird nie hier:
// Ob zwei Anschlüsse zusammenpassen, sagt der Server (`/devices/map/check`); die Tabelle der
// Ablehnungen dient nur dazu, Passendes beim Ziehen schon vorab zu markieren.

import type { DeviceMap, MapConnection, MapPort, ValveAssignment } from './types';

export function portIndex(map: DeviceMap): Map<string, MapPort> {
  const index = new Map<string, MapPort>();
  for (const node of map.nodes) for (const port of node.ports) index.set(port.id, port);
  return index;
}

/** Warum `from` nicht mit `to` verbunden werden darf -- aus den Tabellen des Servers --,
 *  oder `null`, wenn sie passen. */
export function rejectionReason(map: DeviceMap, from: MapPort, to: MapPort): string | null {
  if (from.id === to.id) return 'Ein Anschluss kann nicht mit sich selbst verbunden werden.';
  if (to.technical || from.technical) return map.technical_reason;
  if (from.direction === to.direction) return map.same_side[from.direction];
  const row = map.rejections[from.id];
  const at = row ? row[to.id] : undefined;
  return at === undefined ? null : (map.reasons[at] ?? null);
}

/** Eine Linie, die auf der Karte gezeichnet wird (beide Anschlüsse bekannt und sichtbar). */
export interface DrawnLine {
  id: string;
  out: string;
  in: string;
  title: string;
  editable: boolean;
  kind: string | null;
}

export function drawnLines(map: DeviceMap, visible: ReadonlySet<string>, ports: Map<string, MapPort>): DrawnLine[] {
  const lines: DrawnLine[] = [];
  for (const c of map.connections) {
    if (!c.out_port || !c.in_port) continue;
    if (!visible.has(c.out_port) || !visible.has(c.in_port)) continue;
    lines.push({
      id: c.id,
      out: c.out_port,
      in: c.in_port,
      title: c.title,
      editable: c.editable,
      kind: ports.get(c.out_port)?.kind ?? null,
    });
  }
  return lines;
}

/** Gespeicherte Verbindungen ohne passenden Anschluss am Gerät (die Karte zeichnet sie nicht). */
export function undrawnConnections(map: DeviceMap): MapConnection[] {
  return map.connections.filter((c) => !c.out_port || !c.in_port);
}

export function modeLabel(map: DeviceMap, code: string | null | undefined): string {
  if (!code) return '';
  return map.valve_modes.find((m) => m.code === code)?.label ?? code;
}

/** Ventile eines Geräts mit ihrer Betriebsart (aus dem Server, nie aus Linien errechnet). */
export function valvesOfDevice(map: DeviceMap, deviceId: number | null): ValveAssignment[] {
  if (deviceId === null) return [];
  return map.valve_assignments.filter((v) => v.device_id === deviceId);
}

export function zoneName(map: DeviceMap, zoneId: number): string {
  return map.nodes.find((n) => n.kind === 'zone' && n.ports.some((p) => p.zone_id === zoneId))?.name ?? `Raum ${zoneId}`;
}

export function nodeDeviceId(node: { ports: MapPort[] }): number | null {
  return node.ports.find((p) => p.device_id !== null)?.device_id ?? null;
}
