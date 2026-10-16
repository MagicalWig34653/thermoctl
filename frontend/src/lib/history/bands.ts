// Sonnenabsenkung im Verlauf: was gezeichnet wird, damit die Zeichnung nichts behauptet.
//
// Regel (wie im Alt-Verlauf, `zone_history_chart.solar_*_for`): Ein Band hat genau die Breite der
// Zeitspanne, die es belegt -- keine Mindestbreite, die Zeit vortäuscht. Ist die Spanne so schmal,
// dass sie als Fläche kaum zu sehen ist, bleibt das Band dünn (oder entfällt ganz, wenn es kleiner
// als ein Bruchteil eines Bildpunkts ist) und ein Dreieck über dem Diagramm zeigt, dass hier
// etwas liegt. Das Dreieck ist ein Hinweis und sagt nichts über die Dauer.

import { compareDecimal } from '../rooms/decimal';
import type { Point } from './series';

/** Unter dieser Breite (Bildpunkte) kommt zusätzlich ein Dreieck. */
export const MARK_BELOW = 3;
/** Unter dieser Breite wird gar keine Fläche mehr gezeichnet, nur noch das Dreieck. */
export const MIN_DRAWN = 0.25;
/** Geschätzte Breite eines Zeichens der Bandbeschriftung (Bildpunkte). */
export const LABEL_CHAR_PX = 6.4;

export interface SolarRun {
  start: number;
  end: number;
  /** Kleinste und größte Absenkung im Lauf (Strings des Servers, nie umgerechnet). */
  min: string;
  max: string;
}

/** Aneinandergrenzende Fächer mit Absenkung > 0 zu einem Lauf; ein Abstand bleibt ein Abstand. */
export function solarRuns(points: readonly Point[]): SolarRun[] {
  const out: SolarRun[] = [];
  for (const p of points) {
    if (p.solar === null || p.solar <= 0) continue;
    const max = p.raw.solar_setback_k as string;
    const min = p.raw.solar_setback_min_k ?? max;
    const last = out[out.length - 1];
    if (last && last.end === p.start) {
      last.end = p.end;
      if (compareDecimal(min, last.min) === -1) last.min = min;
      if (compareDecimal(max, last.max) === 1) last.max = max;
    } else {
      out.push({ start: p.start, end: p.end, min, max });
    }
  }
  return out;
}

export interface DrawnBand {
  x: number;
  width: number;
  run: SolarRun;
}

export interface SolarDrawing {
  bands: DrawnBand[];
  /** Mitten der Dreiecke (je Bildpunkt höchstens eines). */
  marks: number[];
}

/**
 * Zeichnet die Läufe: `xOf` bildet eine Zeit auf die Bildkoordinate ab, `left`/`right` begrenzen
 * die Zeichenfläche. Läufe außerhalb entfallen; Breiten werden nie vergrößert.
 */
export function solarDrawing(
  runs: readonly SolarRun[],
  xOf: (t: number) => number,
  left: number,
  right: number,
): SolarDrawing {
  const bands: DrawnBand[] = [];
  const marks = new Set<number>();
  for (const run of runs) {
    const x1 = Math.max(left, xOf(run.start));
    const x2 = Math.min(right, xOf(run.end));
    const width = x2 - x1;
    if (!(width > 0)) continue;
    if (width >= MIN_DRAWN) bands.push({ x: x1, width, run });
    if (width < MARK_BELOW) marks.add(Math.round(x1 + width / 2));
  }
  return { bands, marks: [...marks].sort((a, b) => a - b) };
}

/** Passt die Beschriftung in das Band? Sonst bleibt sie weg, das Band wird nicht verbreitert. */
export function labelFits(label: string, width: number): boolean {
  return width >= label.length * LABEL_CHAR_PX + 12;
}

/** Dreieck als Pfad: Spitze nach unten, über dem Diagramm bei `top`. */
export function markPath(x: number, top: number): string {
  return `M${x - 3},${top - 9}L${x + 3},${top - 9}L${x},${top - 2}Z`;
}
