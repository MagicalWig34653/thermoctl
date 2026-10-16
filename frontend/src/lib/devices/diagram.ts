// Diagramm der Anlage (Anlagenbild) und des Datenflusses als SVG-Zeichnung.
//
// Reine Darstellung: Knoten und Kanten kommen strukturiert vom Server
// (`/ui-api/plant`, `/ui-api/zones/{id}/data-flow`). Hier wird nichts entschieden, nur
// angeordnet (Spalten von links nach rechts) und mit Kurven verbunden. Beschriftungen bleiben
// Text; es gibt keine HTML-Einschleusung -- Svelte setzt sie als Textknoten.
import type { FlowEdge, FlowNode, FlowZone, PlantZone } from './types';

export type Tone = 'source' | 'zone' | 'target' | 'missing' | 'neutral';

export interface DiagramNode {
  id: string;
  label: string;
  detail: string | null;
  tone: Tone;
  /** Spalte von links; wird bei `null` aus den Kanten bestimmt. */
  column: number | null;
  /** Gestrichelter Rahmen: das Gerät ist abgeschaltet oder fehlt. */
  dashed: boolean;
}

export interface DiagramEdge {
  source: string;
  target: string;
  label: string;
  dashed: boolean;
}

export interface Diagram {
  nodes: DiagramNode[];
  edges: DiagramEdge[];
}

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 56;
export const COLUMN_GAP = 200;
export const ROW_GAP = 18;
export const MARGIN = 12;

const TONES: Record<string, Tone> = {
  sensor: 'source',
  window: 'source',
  setpoint: 'source',
  zone: 'zone',
  actuator: 'target',
  missing: 'missing',
};

export const toneOf = (kind: string): Tone => TONES[kind] ?? 'neutral';

/** Kürzt für die Zeichnung; der volle Text steht im `<title>` des Knotens. */
export function clip(text: string, max: number): string {
  const chars = [...text];
  return chars.length <= max ? text : `${chars.slice(0, Math.max(1, max - 1)).join('')}…`;
}

export function flowDiagram(zone: Pick<FlowZone, 'nodes' | 'edges'>): Diagram {
  const nodes = zone.nodes.map((n: FlowNode) => ({
    id: n.id,
    label: n.label,
    detail: n.detail,
    tone: toneOf(n.kind),
    column: null,
    dashed: n.kind === 'missing',
  }));
  const edges = zone.edges.map((e: FlowEdge) => ({ source: e.source, target: e.target, label: e.label, dashed: e.dashed }));
  return { nodes, edges };
}

const dash = (list: string[]) => (list.length ? list.join(', ') : null);

/** Die drei Stufen des Anlagenbilds: Messen und Unterbrechen, Entscheiden, Aktor und Anzeige. */
export function plantDiagram(zone: PlantZone): Diagram {
  const nodes: DiagramNode[] = [];
  const edges: DiagramEdge[] = [];
  const zoneId = `zone:${zone.zone_id}`;
  nodes.push({ id: zoneId, label: zone.name, detail: 'Ist gegen Soll, mit Hysterese', tone: 'zone', column: 1, dashed: false });
  const device = (d: PlantZone['actuators'][number], slot: string, column: number): DiagramNode => ({
    id: `${slot}:${d.id}`,
    label: d.name,
    detail: [dash(d.capabilities), d.active ? null : 'abgeschaltet', d.unsuitable].filter(Boolean).join(' · ') || null,
    tone: column === 0 ? 'source' : 'target',
    column,
    dashed: !d.active || Boolean(d.unsuitable),
  });
  if (zone.temperature_source) {
    const n = device(zone.temperature_source, 'source', 0);
    nodes.push(n);
    edges.push({ source: n.id, target: zoneId, label: 'Messwert', dashed: n.dashed });
  } else {
    nodes.push({ id: 'source:none', label: 'keine Messquelle', detail: null, tone: 'missing', column: 0, dashed: true });
    edges.push({ source: 'source:none', target: zoneId, label: 'Messwert fehlt', dashed: true });
  }
  for (const d of zone.window_contacts) {
    const n = device(d, 'window', 0);
    nodes.push(n);
    edges.push({ source: n.id, target: zoneId, label: 'Fensterzustand', dashed: n.dashed });
  }
  if (zone.actuators.length) {
    for (const d of zone.actuators) {
      const n = device(d, 'actuator', 2);
      nodes.push(n);
      edges.push({ source: zoneId, target: n.id, label: 'Heizbefehl', dashed: n.dashed });
    }
  } else {
    nodes.push({ id: 'actuator:none', label: 'kein Aktor', detail: null, tone: 'missing', column: 2, dashed: true });
    edges.push({ source: zoneId, target: 'actuator:none', label: 'Heizbefehl fehlt', dashed: true });
  }
  // Bediengeräte zeigen an, sie steuern nichts: Knoten ohne Kante, wie im Alt-Anlagenbild.
  for (const d of zone.controllers) nodes.push(device(d, 'controller', 2));
  return { nodes, edges };
}

/** Spalten für Knoten ohne feste Spalte: so weit rechts wie der längste Weg zu ihnen. */
export function assignColumns(diagram: Diagram): Map<string, number> {
  const column = new Map<string, number>();
  for (const n of diagram.nodes) column.set(n.id, n.column ?? 0);
  const fixed = new Set(diagram.nodes.filter((n) => n.column !== null).map((n) => n.id));
  // Höchstens so viele Durchläufe wie Knoten: Zyklen laufen nicht ins Leere.
  for (let pass = 0; pass < diagram.nodes.length; pass += 1) {
    let changed = false;
    for (const e of diagram.edges) {
      const from = column.get(e.source);
      const to = column.get(e.target);
      if (from === undefined || to === undefined || fixed.has(e.target) || e.source === e.target) continue;
      if (to < from + 1) {
        column.set(e.target, from + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return column;
}

export interface PlacedNode extends DiagramNode {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PlacedEdge extends DiagramEdge {
  key: string;
  /** SVG-Pfad (Kurve vom rechten Rand der Quelle zum linken Rand des Ziels). */
  path: string;
  labelX: number;
  labelY: number;
}

export interface Layout {
  width: number;
  height: number;
  nodes: PlacedNode[];
  edges: PlacedEdge[];
}

export function layoutDiagram(diagram: Diagram): Layout {
  const columns = assignColumns(diagram);
  const used = [...new Set(columns.values())].sort((a, b) => a - b);
  const index = new Map(used.map((c, i) => [c, i] as const));
  const rows = new Map<number, number>();
  const placed: PlacedNode[] = diagram.nodes.map((n) => {
    const col = index.get(columns.get(n.id) ?? 0) ?? 0;
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    return {
      ...n,
      x: MARGIN + col * (NODE_WIDTH + COLUMN_GAP),
      y: MARGIN + row * (NODE_HEIGHT + ROW_GAP),
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    };
  });
  const tallest = Math.max(1, ...rows.values());
  const height = MARGIN * 2 + tallest * NODE_HEIGHT + (tallest - 1) * ROW_GAP;
  // Spalten mit weniger Knoten stehen mittig zur höchsten Spalte.
  for (const col of rows.keys()) {
    const count = rows.get(col) ?? 0;
    const shift = ((tallest - count) * (NODE_HEIGHT + ROW_GAP)) / 2;
    for (const n of placed) {
      if (n.x === MARGIN + col * (NODE_WIDTH + COLUMN_GAP)) n.y += shift;
    }
  }
  const byId = new Map(placed.map((n) => [n.id, n] as const));
  const edges: PlacedEdge[] = [];
  diagram.edges.forEach((e, i) => {
    const a = byId.get(e.source);
    const b = byId.get(e.target);
    if (!a || !b) return;
    const sameColumn = a.x === b.x;
    const x1 = sameColumn ? a.x + a.width : a.x < b.x ? a.x + a.width : a.x;
    const x2 = sameColumn ? b.x + b.width : a.x < b.x ? b.x : b.x + b.width;
    const y1 = a.y + a.height / 2;
    const y2 = b.y + b.height / 2;
    const bend = sameColumn ? 40 : Math.max(24, Math.abs(x2 - x1) / 2);
    const dir = sameColumn || a.x < b.x ? 1 : -1;
    const path = `M ${x1} ${y1} C ${x1 + dir * bend} ${y1}, ${x2 - dir * bend} ${y2}, ${x2} ${y2}`;
    edges.push({ ...e, key: `${e.source}>${e.target}#${i}`, path, labelX: (x1 + x2) / 2, labelY: (y1 + y2) / 2 });
  });
  const width = MARGIN * 2 + used.length * NODE_WIDTH + Math.max(0, used.length - 1) * COLUMN_GAP;
  return { width: Math.max(width, MARGIN * 2 + NODE_WIDTH), height, nodes: placed, edges };
}

/** Textfassung einer Kante für die Listenalternative des Anlagenbilds. */
export function edgeSentence(diagram: Diagram, edge: DiagramEdge): string {
  const name = (id: string) => diagram.nodes.find((n) => n.id === id)?.label ?? id;
  return `${name(edge.source)} → ${name(edge.target)}: ${edge.label}${edge.dashed ? ' (fehlt oder eingeschränkt)' : ''}`;
}

/** Kurzbeschreibung für Hilfstechnik: was das Bild zeigt, ohne die Zeichnung lesen zu müssen. */
export function summary(diagram: Diagram, title: string): string {
  const missing = diagram.nodes.filter((n) => n.tone === 'missing').length;
  const base = `${title}: ${diagram.nodes.length} Beteiligte, ${diagram.edges.length} Verbindungen`;
  return missing ? `${base}, ${missing} fehlend` : base;
}
