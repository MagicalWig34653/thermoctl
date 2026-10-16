// Geometrie der Gerätekarte (rein, ohne DOM, getestet).
//
// Die Karte rechnet nicht mit gemessenen Elementen, sondern mit festen Maßen, die das
// Stylesheet (`styles/devices.css`) über dieselben Konstanten als CSS-Variablen erzwingt:
// Kopf, Zeilenhöhe und Breite sind Pixelwerte, Texte werden abgeschnitten statt umgebrochen.
// Ein Anschluss sitzt mit seiner Mitte genau auf der linken (Eingang) oder rechten
// (Ausgang) Kante des Knotens, vertikal in der Mitte seiner Zeile. Die Browserprüfung
// vergleicht die Linienenden gegen `getBoundingClientRect` der Anschlüsse.

import type { Direction, MapNode, MapPort } from './types';

export const NODE_W = 248;
export const HEAD_H = 52;
export const ROW_H = 32;
export const FOOT_H = 8;
export const PORT_SIZE = 32;
export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 2;

export interface Point {
  x: number;
  y: number;
}
export interface View {
  x: number;
  y: number;
  k: number;
}
export interface Size {
  w: number;
  h: number;
}
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Anschlüsse, die auf der Karte erscheinen; technische nur auf Wunsch. */
export function visiblePorts(node: MapNode, showTechnical: boolean): MapPort[] {
  return node.ports.filter((p) => showTechnical || !p.technical);
}

export function nodeHeight(portCount: number, collapsed: boolean): number {
  return collapsed ? HEAD_H : HEAD_H + portCount * ROW_H + FOOT_H;
}

/** Mittelpunkt eines Anschlusses in Weltkoordinaten. `index` zählt unter den sichtbaren
 *  Anschlüssen des Knotens. Eingeklappt endet die Linie an der Kopfzeile, auf der Seite
 *  des Anschlusses. */
export function portCenter(origin: Point, direction: Direction, index: number, collapsed: boolean): Point {
  const x = origin.x + (direction === 'out' ? NODE_W : 0);
  const y = collapsed ? origin.y + HEAD_H / 2 : origin.y + HEAD_H + index * ROW_H + ROW_H / 2;
  return { x, y };
}

/** Linie vom Ausgang `a` zum Eingang `b`: Kubische Kurve mit waagerechten Tangenten.
 *  Anfang und Ende sind genau die übergebenen Punkte. */
export function curvePath(a: Point, b: Point): string {
  const reach = Math.max(48, Math.min(160, Math.abs(b.x - a.x) / 2));
  return `M${num(a.x)},${num(a.y)} C${num(a.x + reach)},${num(a.y)} ${num(b.x - reach)},${num(b.y)} ${num(b.x)},${num(b.y)}`;
}

function num(v: number): string {
  return String(Math.round(v * 100) / 100);
}

/** Endpunkte, die ein Pfad aus `curvePath` hat (für Tests und Prüfungen). */
export function pathEnds(d: string): { start: Point; end: Point } | null {
  const m = /^M(-?[\d.]+),(-?[\d.]+) C-?[\d.]+,-?[\d.]+ -?[\d.]+,-?[\d.]+ (-?[\d.]+),(-?[\d.]+)$/.exec(d);
  if (!m) return null;
  return { start: { x: Number(m[1]), y: Number(m[2]) }, end: { x: Number(m[3]), y: Number(m[4]) } };
}

export function clampZoom(k: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));
}

/** Weltpunkt → Pixel im Container. */
export function toScreen(view: View, p: Point): Point {
  return { x: view.x + p.x * view.k, y: view.y + p.y * view.k };
}

/** Pixel im Container → Weltpunkt. */
export function toWorld(view: View, p: Point): Point {
  return { x: (p.x - view.x) / view.k, y: (p.y - view.y) / view.k };
}

/** Zoom um einen Fixpunkt (Pixel im Container): der Weltpunkt darunter bleibt stehen. */
export function zoomAround(view: View, anchor: Point, factor: number): View {
  const k = clampZoom(view.k * factor);
  const world = toWorld(view, anchor);
  return { k, x: anchor.x - world.x * k, y: anchor.y - world.y * k };
}

export function bounds(rects: readonly Rect[]): Rect | null {
  if (!rects.length) return null;
  const left = Math.min(...rects.map((r) => r.x));
  const top = Math.min(...rects.map((r) => r.y));
  const right = Math.max(...rects.map((r) => r.x + r.w));
  const bottom = Math.max(...rects.map((r) => r.y + r.h));
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/** Ansicht, die `box` in `size` einpasst (zentriert, mit Rand). `null`, solange der
 *  Container keine echten Maße hat -- dann wird nichts berechnet. */
export function fitView(box: Rect | null, size: Size, pad = 24): View | null {
  if (size.w < 40 || size.h < 40) return null;
  if (!box || box.w <= 0 || box.h <= 0) return { x: 0, y: 0, k: 1 };
  const k = clampZoom(Math.min(1, (size.w - 2 * pad) / box.w, (size.h - 2 * pad) / box.h));
  return { k, x: (size.w - box.w * k) / 2 - box.x * k, y: (size.h - box.h * k) / 2 - box.y * k };
}

/** Startansicht: einpassen, aber nie kleiner als `minK`. Passt die Karte lesbar hinein, ist es
 *  `fitView`; sonst (schmaler Container, mobil) bleibt der Maßstab lesbar und die Karte beginnt
 *  links oben im Container -- verschoben wird per Wischen. Nichts liegt winzig in einer Ecke. */
export function readableView(box: Rect | null, size: Size, pad = 16, minK = 0.55): View | null {
  const fit = fitView(box, size, pad);
  if (!fit || !box || box.w <= 0 || box.h <= 0 || fit.k >= minK) return fit;
  return { k: minK, x: pad - box.x * minK, y: pad - box.y * minK };
}

// -- Anordnung ---------------------------------------------------------------------------

export type Placement = Record<string, Point>;

/** Spalte eines Knotens: Zonen Mitte, reine Empfänger rechts, alles andere links. */
export function columnOf(node: MapNode, showTechnical: boolean): 'left' | 'mid' | 'right' {
  if (node.kind === 'zone') return 'mid';
  const ports = visiblePorts(node, showTechnical);
  const hasOut = ports.some((p) => p.direction === 'out');
  const hasIn = ports.some((p) => p.direction === 'in');
  return hasIn && !hasOut ? 'right' : 'left';
}

/** „Automatisch anordnen“: reine Darstellung, deterministisch, ändert nichts am Server.
 *  Fühler links, Zonen Mitte, Empfänger rechts; lange Spalten brechen in Nebenspalten um. */
export function autoLayout(
  nodes: readonly MapNode[],
  collapsed: ReadonlySet<string>,
  showTechnical: boolean,
  columnHeight = 900,
): Placement {
  const gapX = 150;
  const gapSub = 40;
  const gapY = 16;
  const groups: Record<'left' | 'mid' | 'right', MapNode[]> = { left: [], mid: [], right: [] };
  for (const n of nodes) groups[columnOf(n, showTechnical)].push(n);
  const out: Placement = {};
  const fill = (list: MapNode[], baseX: number, step: 1 | -1): number => {
    let y = 0;
    let sub = 0;
    for (const n of list) {
      const h = nodeHeight(visiblePorts(n, showTechnical).length, collapsed.has(n.id));
      if (y > 0 && y + h > columnHeight) {
        sub += 1;
        y = 0;
      }
      out[n.id] = { x: baseX + step * sub * (NODE_W + gapSub), y };
      y += h + gapY;
    }
    return sub + 1;
  };
  fill(groups.left, 0, -1);
  const midCols = fill(groups.mid, NODE_W + gapX, 1);
  fill(groups.right, NODE_W + gapX + (midCols - 1) * (NODE_W + gapSub) + NODE_W + gapX, 1);
  return out;
}

/** Gemerkte Positionen haben Vorrang; Knoten ohne gemerkte Position kommen aus der
 *  automatischen Anordnung. */
export function resolvePlacement(auto: Placement, saved: Placement): Placement {
  const out: Placement = {};
  for (const id of Object.keys(auto)) out[id] = saved[id] ?? auto[id];
  return out;
}

export function nodeRects(
  nodes: readonly MapNode[],
  at: Placement,
  collapsed: ReadonlySet<string>,
  showTechnical: boolean,
): Rect[] {
  return nodes
    .filter((n) => at[n.id])
    .map((n) => ({
      x: at[n.id].x,
      y: at[n.id].y,
      w: NODE_W,
      h: nodeHeight(visiblePorts(n, showTechnical).length, collapsed.has(n.id)),
    }));
}

export interface Anchors {
  /** Anschluss-ID → Mittelpunkt (Welt), nur für sichtbare Knoten und Anschlüsse. */
  [portId: string]: Point;
}

export function anchors(
  nodes: readonly MapNode[],
  at: Placement,
  collapsed: ReadonlySet<string>,
  showTechnical: boolean,
): Anchors {
  const out: Anchors = {};
  for (const n of nodes) {
    const origin = at[n.id];
    if (!origin) continue;
    const isCollapsed = collapsed.has(n.id);
    visiblePorts(n, showTechnical).forEach((p, i) => {
      out[p.id] = portCenter(origin, p.direction, i, isCollapsed);
    });
  }
  return out;
}

/** Knoten, die zur Suche passen (Name, Art, Anschlussbeschriftung). Leer: alle. */
export function matchesSearch(node: MapNode, query: string): boolean {
  const q = query.trim().toLocaleLowerCase('de-DE');
  if (!q) return true;
  return (
    node.name.toLocaleLowerCase('de-DE').includes(q) ||
    node.ports.some((p) => p.label.toLocaleLowerCase('de-DE').includes(q))
  );
}
