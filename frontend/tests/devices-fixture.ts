import type { DeviceMap, MapNode, MapPort } from '../src/lib/devices/types.ts';

export function port(over: Partial<MapPort> & Pick<MapPort, 'id' | 'node_id' | 'direction'>): MapPort {
  return {
    kind: 'temperature', label: over.id, tooltip: '', capability: null, accepts: [], accepts_text: '',
    zone_id: null, slot: null, device_id: null, property_name: null, technical: false, single: false,
    blocked_reason: null, editable: true, valve_channel: null, requires_actual: false, actuator_elsewhere: [],
    ...over,
  };
}

export const sensor: MapNode = {
  id: 'device:1', kind: 'device', name: 'Fühler Wohnzimmer',
  ports: [
    port({ id: 'd1.temp', node_id: 'device:1', direction: 'out', device_id: 1 }),
    port({ id: 'd1.battery', node_id: 'device:1', direction: 'out', technical: true, device_id: 1 }),
    port({ id: 'd1.hum', node_id: 'device:1', direction: 'out', kind: 'humidity', device_id: 1 }),
  ],
};
export const zone: MapNode = {
  id: 'zone:7', kind: 'zone', name: 'Wohnzimmer',
  ports: [
    port({ id: 'z7.source', node_id: 'zone:7', direction: 'in', zone_id: 7, slot: 'temperature_source' }),
    port({ id: 'z7.heat', node_id: 'zone:7', direction: 'out', kind: 'command', zone_id: 7, slot: 'actuator' }),
  ],
};
export const valve: MapNode = {
  id: 'device:2', kind: 'device', name: 'Ventil',
  ports: [port({ id: 'd2.cmd', node_id: 'device:2', direction: 'in', kind: 'command', device_id: 2 })],
};

export const map: DeviceMap = {
  may_edit: true,
  nodes: [sensor, zone, valve],
  connections: [
    { id: 'c1', out_port: 'd1.temp', in_port: 'z7.source', zone_port: 'z7.source', device_id: 1, device_name: 'Fühler Wohnzimmer', text: 'Messquelle', zone_id: 7, slot: 'temperature_source', editable: true, from_zone: false, title: 'Fühler Wohnzimmer → Messquelle' },
    { id: 'c2', out_port: null, in_port: null, zone_port: 'z7.heat', device_id: 2, device_name: 'Ventil', text: 'Heizbefehl', zone_id: 7, slot: 'actuator', editable: true, from_zone: true, title: 'Heizbefehl → Ventil' },
  ],
  reasons: ['Falsche Wertart.'],
  rejections: { 'd1.hum': { 'z7.source': 0 } },
  same_side: { out: 'Beides sind Ausgänge.', in: 'Beides sind Eingänge.' },
  technical_reason: 'Technische Werte sind nicht verbindbar.',
  single_reason: 'Nur eine Verbindung.',
  kind_labels: { temperature: 'Temperatur' },
  valve_modes: [{ code: 'controlled', label: 'Geregelt', description: '', lines: ['actuator'] }],
  valve_assignments: [{ assignment_id: 1, zone_id: 7, device_id: 2, mode: 'controlled', mode_label: 'Geregelt' }],
};
