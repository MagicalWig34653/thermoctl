import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, createApi } from '../src/lib/api.ts';
import { createDevicesClient } from '../src/lib/devices/client.ts';
import { assignColumns, flowDiagram, layoutDiagram, plantDiagram } from '../src/lib/devices/diagram.ts';
import { deviceFailure, failureText } from '../src/lib/devices/errors.ts';
import { drawnLines, portIndex } from '../src/lib/devices/map-logic.ts';
import { addableSources, removeStep, sourcesTitle } from '../src/lib/devices/sources.ts';
import type { FlowZone, PlantDevice, PlantZone } from '../src/lib/devices/types.ts';
import { map, port, zone } from './devices-fixture.ts';

const dev = (id: number, name: string, over: Partial<PlantDevice> = {}): PlantDevice => ({
  id, name, model: null, capabilities: ['temperature'], active: true, unsuitable: null, assignment_id: id, ...over,
});

test('Liste: Überschrift zählt ab zwei Fühlern, angeboten werden nur noch nicht gesetzte Geräte', () => {
  assert.equal(sourcesTitle(0), 'Messquellen');
  assert.equal(sourcesTitle(1), 'Messquellen');
  assert.equal(sourcesTitle(3), 'Messquellen (3)');
  const data = {
    temperature_sources: [{ id: 1, name: 'A' }, { id: 3, name: 'C' }],
    devices: [1, 2, 3, 4].map((id) => ({ id, name: `G${id}`, capabilities: [] })),
  };
  assert.deepEqual(addableSources(data).map((d) => d.id), [2, 4]);
  assert.deepEqual(addableSources({ ...data, temperature_sources: [] }).map((d) => d.id), [1, 2, 3, 4]);
});

test('Entfernen braucht eine Bestätigung je Fühler', () => {
  assert.equal(removeStep(null, 1), 'confirm');
  assert.equal(removeStep(2, 1), 'confirm');
  assert.equal(removeStep(1, 1), 'remove');
});

test('Client: Hinzufügen per POST /sources, Entfernen per DELETE /sources/{id}, je mit CSRF', async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  const fakeFetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response('{}', { status: 200 });
  }) as unknown as typeof fetch;
  const api = createApi({ fetch: fakeFetch, prefix: '/p', cookies: () => 'thermoctl_csrf=tok' });
  const client = createDevicesClient(api);
  await client.addSource(7, 12);
  await client.removeSource(7, 12);
  assert.deepEqual(calls.map((c) => [c.init.method, c.url]), [
    ['POST', '/p/ui-api/zones/7/devices/sources'],
    ['DELETE', '/p/ui-api/zones/7/devices/sources/12'],
  ]);
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { device_id: 12 });
  assert.ok(calls.every((c) => (c.init.headers as Record<string, string>)['X-CSRF-Token'] === 'tok'));
});

test('Fehler: Thermostat (422) und Doppelter (409) zeigen den Servertext verständlich an', () => {
  const thermostat = deviceFailure(new ApiError('http', 422, 'Ein Thermostat kann nicht Messquelle sein.', false, 'capability_missing'));
  assert.equal(thermostat.kind, 'validation');
  assert.equal(thermostat.code, 'capability_missing');
  assert.equal(failureText(thermostat), 'Ein Thermostat kann nicht Messquelle sein.');
  assert.equal(thermostat.isConflict, false);
  const twice = deviceFailure(new ApiError('http', 409, 'Dieses Gerät ist bereits Messquelle dieser Zone.', false, 'already_assigned'));
  assert.equal(twice.isConflict, true);
  assert.equal(twice.reload, true);
  assert.equal(failureText(twice), 'Dieses Gerät ist bereits Messquelle dieser Zone.');
});

test('Karte: mehrere Fühler an einem Zonen-Eingang ergeben mehrere Linien', () => {
  const second = { ...map.connections[0], id: 'c3', out_port: 'd5.temp', device_id: 5, device_name: 'Fühler Bad' };
  const sensor2 = { id: 'device:5', kind: 'device', name: 'Fühler Bad', ports: [port({ id: 'd5.temp', node_id: 'device:5', direction: 'out', device_id: 5 })] };
  const many = { ...map, nodes: [...map.nodes, sensor2], connections: [...map.connections, second] };
  const input = zone.ports.find((p) => p.id === 'z7.source')!;
  assert.equal(input.single, false);
  const visible = new Set(many.nodes.flatMap((n) => n.ports.map((p) => p.id)));
  const lines = drawnLines(many, visible, portIndex(many)).filter((l) => l.in === 'z7.source');
  assert.deepEqual(lines.map((l) => l.out).sort(), ['d1.temp', 'd5.temp']);
});

test('Anlagenbild: jeder Fühler ein Knoten links mit Kante zum Raum', () => {
  const z: PlantZone = {
    zone_id: 7, name: 'Wohnzimmer', temperature_sources: [dev(1, 'Fühler A'), dev(2, 'Fühler B'), dev(3, 'Fühler C', { active: false })],
    window_contacts: [], actuators: [dev(9, 'Ventil')], controllers: [], defects: [],
  };
  const d = plantDiagram(z);
  assert.deepEqual(d.nodes.filter((n) => n.id.startsWith('source:')).map((n) => n.id), ['source:1', 'source:2', 'source:3']);
  assert.equal(d.edges.filter((e) => e.target === 'zone:7' && e.label === 'Messwert').length, 3);
  assert.equal(d.nodes.find((n) => n.id === 'source:3')!.dashed, true);
  const layout = layoutDiagram(d);
  const ys = layout.nodes.filter((n) => n.id.startsWith('source:')).map((n) => n.y);
  assert.equal(new Set(ys).size, 3);
});

test('Datenfluss: sensor0..n laufen in den Raum, der Istwert zum Ventil kommt vom Raum', () => {
  const f: Pick<FlowZone, 'nodes' | 'edges'> = {
    nodes: [
      { id: 'sensor0', kind: 'sensor', label: 'Fühler A', detail: 'Messquelle (Raumtemperatur)', device_id: 1 },
      { id: 'sensor1', kind: 'sensor', label: 'Fühler B', detail: 'Messquelle (Raumtemperatur)', device_id: 2 },
      { id: 'zone', kind: 'zone', label: 'Wohnzimmer', detail: null, device_id: null },
      { id: 'valve', kind: 'actuator', label: 'Ventil', detail: null, device_id: 9 },
    ],
    edges: [
      { source: 'sensor0', target: 'zone', kind: 'actual', label: 'Istwert (Raumtemperatur)', property_name: null, dashed: false, text: '' },
      { source: 'sensor1', target: 'zone', kind: 'actual', label: 'Istwert (Raumtemperatur)', property_name: null, dashed: false, text: '' },
      { source: 'zone', target: 'valve', kind: 'actual', label: 'Istwert (Raumtemperatur)', property_name: null, dashed: false, text: '' },
    ],
  };
  const d = flowDiagram(f);
  assert.equal(d.nodes.filter((n) => n.tone === 'source').length, 2);
  const cols = assignColumns(d);
  assert.equal(cols.get('sensor0'), 0);
  assert.equal(cols.get('sensor1'), 0);
  assert.equal(cols.get('zone'), 1);
  assert.equal(cols.get('valve'), 2);
  assert.equal(layoutDiagram(d).edges.length, 3);
});

test('Datenfluss: ohne Fühler steht der Knoten sensor als Lücke da', () => {
  const d = flowDiagram({
    nodes: [
      { id: 'sensor', kind: 'missing', label: 'Keine Messquelle', detail: null, device_id: null },
      { id: 'zone', kind: 'zone', label: 'Raum', detail: null, device_id: null },
    ],
    edges: [{ source: 'sensor', target: 'zone', kind: 'missing', label: 'ohne Messquelle kein Istwert', property_name: null, dashed: true, text: '' }],
  });
  assert.equal(d.nodes[0].tone, 'missing');
  assert.equal(d.nodes[0].dashed, true);
});
