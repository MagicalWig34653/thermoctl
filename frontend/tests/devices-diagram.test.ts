import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assignColumns, clip, COLUMN_GAP, edgeSentence, flowDiagram, layoutDiagram, MARGIN, NODE_WIDTH, plantDiagram, summary, toneOf } from '../src/lib/devices/diagram.ts';
import { bindableControllers, buttonBody, draftChanged, draftKey, draftOf } from '../src/lib/devices/buttons.ts';
import type { FlowZone, PlantDevice, PlantZone } from '../src/lib/devices/types.ts';

const dev = (id: number, name: string, over: Partial<PlantDevice> = {}): PlantDevice => ({
  id, name, model: null, capabilities: ['temperature'], active: true, unsuitable: null, assignment_id: id, ...over,
});

const full: PlantZone = {
  zone_id: 7, name: 'Wohnzimmer', temperature_source: dev(1, 'Fühler'),
  window_contacts: [dev(2, 'Fenster', { capabilities: ['contact'] })],
  actuators: [dev(3, 'Ventil'), dev(4, 'Ventil 2', { active: false })],
  controllers: [dev(5, 'Taster')], defects: [],
};

const flow: Pick<FlowZone, 'nodes' | 'edges'> = {
  nodes: [
    { id: 's', kind: 'sensor', label: 'Fühler', detail: null, device_id: 1 },
    { id: 'z', kind: 'zone', label: 'Raum', detail: null, device_id: null },
    { id: 'a', kind: 'actuator', label: 'Ventil', detail: 'Zigbee', device_id: 3 },
    { id: 'm', kind: 'missing', label: 'kein Fenster', detail: null, device_id: null },
  ],
  edges: [
    { source: 's', target: 'z', kind: 'actual', label: 'Istwert', property_name: null, dashed: false, text: '' },
    { source: 'z', target: 'a', kind: 'switch', label: 'Ein/Aus', property_name: null, dashed: false, text: '' },
    { source: 'm', target: 'z', kind: 'missing', label: 'fehlt', property_name: null, dashed: true, text: '' },
  ],
};

test('Knotenarten werden auf Töne abgebildet, Unbekanntes bleibt neutral', () => {
  assert.equal(toneOf('sensor'), 'source');
  assert.equal(toneOf('zone'), 'zone');
  assert.equal(toneOf('actuator'), 'target');
  assert.equal(toneOf('missing'), 'missing');
  assert.equal(toneOf('irgendwas'), 'neutral');
});

test('Spalten aus den Kanten: Quelle links, Ziel rechts, Zyklen laufen nicht endlos', () => {
  const cols = assignColumns(flowDiagram(flow));
  assert.deepEqual([cols.get('s'), cols.get('m'), cols.get('z'), cols.get('a')], [0, 0, 1, 2]);
  const loop = assignColumns({
    nodes: ['a', 'b'].map((id) => ({ id, label: id, detail: null, tone: 'neutral' as const, column: null, dashed: false })),
    edges: [{ source: 'a', target: 'b', label: '', dashed: false }, { source: 'b', target: 'a', label: '', dashed: false }],
  });
  assert.ok(loop.get('a')! >= 0 && loop.get('b')! >= 0);
});

test('Anlagenbild: Messquelle und Fenster links, Raum Mitte, Aktoren rechts; Bediengerät ohne Kante', () => {
  const d = plantDiagram(full);
  const layout = layoutDiagram(d);
  const x = (id: string) => layout.nodes.find((n) => n.id === id)!.x;
  assert.equal(x('source:1'), MARGIN);
  assert.equal(x('window:2'), MARGIN);
  assert.equal(x('zone:7'), MARGIN + NODE_WIDTH + COLUMN_GAP);
  assert.equal(x('actuator:3'), MARGIN + 2 * (NODE_WIDTH + COLUMN_GAP));
  assert.equal(x('controller:5'), x('actuator:3'));
  assert.equal(d.edges.length, 4);
  assert.equal(d.edges.some((e) => e.source === 'controller:5' || e.target === 'controller:5'), false);
  const off = d.nodes.find((n) => n.id === 'actuator:4')!;
  assert.equal(off.dashed, true);
  assert.match(off.detail!, /abgeschaltet/);
});

test('Anlagenbild: fehlende Messquelle und fehlender Aktor stehen als Lücke da', () => {
  const d = plantDiagram({ ...full, temperature_source: null, actuators: [], window_contacts: [], controllers: [] });
  assert.deepEqual(d.nodes.filter((n) => n.tone === 'missing').map((n) => n.id), ['source:none', 'actuator:none']);
  assert.ok(d.edges.every((e) => e.source === 'zone:7' || e.dashed));
  assert.match(summary(d, 'Küche'), /2 fehlend/);
  assert.match(edgeSentence(d, d.edges[0]), /keine Messquelle → Wohnzimmer: Messwert fehlt \(fehlt oder eingeschränkt\)/);
});

test('Layout: Kanten liegen an den Rändern der Knoten, keine Zahl ist NaN', () => {
  const layout = layoutDiagram(flowDiagram(flow));
  assert.equal(layout.edges.length, 3);
  for (const e of layout.edges) {
    assert.doesNotMatch(e.path, /NaN|undefined/);
    assert.ok(Number.isFinite(e.labelX) && Number.isFinite(e.labelY));
  }
  const s = layout.nodes.find((n) => n.id === 's')!;
  const z = layout.nodes.find((n) => n.id === 'z')!;
  const first = layout.edges[0];
  assert.ok(first.path.startsWith(`M ${s.x + s.width} ${s.y + s.height / 2}`));
  assert.ok(first.path.endsWith(`${z.x} ${z.y + z.height / 2}`));
  assert.ok(layout.nodes.every((n) => n.y >= MARGIN && n.y + n.height <= layout.height - MARGIN + 0.001));
  assert.ok(layout.nodes.every((n) => n.x + n.width <= layout.width));
});

test('Layout: leere Zeichnung und Kante zu unbekanntem Knoten brechen nicht', () => {
  const empty = layoutDiagram({ nodes: [], edges: [] });
  assert.ok(empty.width > 0 && empty.height > 0);
  const odd = layoutDiagram({
    nodes: [{ id: 'a', label: 'A', detail: null, tone: 'neutral', column: 0, dashed: false }],
    edges: [{ source: 'a', target: 'x', label: '', dashed: false }],
  });
  assert.equal(odd.edges.length, 0);
});

test('Beschriftungen werden gekürzt, ohne Zeichen zu zerreißen', () => {
  assert.equal(clip('kurz', 10), 'kurz');
  assert.equal(clip('abcdefghij', 5), 'abcd…');
  assert.equal(clip('😀😀😀😀😀😀', 4), '😀😀😀…');
});

test('Tastenzuordnung: leere Felder werden null, Entwurf wird verglichen', () => {
  assert.deepEqual(buttonBody(5, 'single', { command: ' ', step: ' ' }), { device_id: 5, action_code: 'single', command: null, step_k: null });
  assert.deepEqual(buttonBody(5, 'single', { command: 'setpoint_up', step: ' 0,5 ' }), { device_id: 5, action_code: 'single', command: 'setpoint_up', step_k: '0,5' });
  const b = { action: 'single', command_code: 'boost', command_name: 'Boost', step_k: '1', last_seen: null };
  assert.deepEqual(draftOf(b), { command: 'boost', step: '1' });
  assert.equal(draftChanged(b, { command: 'boost', step: '1 ' }), false);
  assert.equal(draftChanged(b, { command: '', step: '1' }), true);
  assert.deepEqual(draftOf({ ...b, command_code: null, step_k: null }), { command: '', step: '' });
  assert.equal(draftKey(5, 'single'), '5|single');
});

test('nur Bediengeräte mit gesehenen Tasten sind belegbar', () => {
  const b = { action: 'single', command_code: null, command_name: null, step_k: null, last_seen: null };
  const zone = { controllers: [{ device: { id: 1, name: 'A' }, bindings: [b] }, { device: { id: 2, name: 'B' }, bindings: [] }] };
  assert.deepEqual(bindableControllers(zone).map((c) => c.device.id), [1]);
});

test('Datenfluss: Trockenlauf-Hinweis im Alt-Wortlaut, ohne Übergabe zu behaupten', async () => {
  const { FLOW_NOTE } = await import('../src/lib/devices/diagram.ts');
  assert.match(FLOW_NOTE, /Im Trockenlauf wird nichts gesendet/);
  assert.match(FLOW_NOTE, /Notbetrieb-Übergabe ist hier nicht dargestellt/);
  const { readFileSync } = await import('node:fs');
  const panel = readFileSync(new URL('../src/lib/components/devices/DataFlowPanel.svelte', import.meta.url), 'utf8');
  assert.match(panel, /\{FLOW_NOTE\}/);
});
