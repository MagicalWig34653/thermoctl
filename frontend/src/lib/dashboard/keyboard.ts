// Tastatur für das Umsortieren (rein, getestet). Der Griff einer Kachel oder eines Abschnitts
// ist eine Schaltfläche; mit den Pfeiltasten wandert das Element eine Stelle.

export interface MoveKey {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

/** Richtung für einen Kachelgriff: links/oben = früher, rechts/unten = später. */
export function tileMove(e: MoveKey): -1 | 1 | null {
  if (e.altKey || e.ctrlKey || e.metaKey) return null;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') return -1;
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') return 1;
  return null;
}

/** Abschnitte stehen untereinander: nur hoch/runter. */
export function sectionMove(e: MoveKey): -1 | 1 | null {
  if (e.altKey || e.ctrlKey || e.metaKey) return null;
  if (e.key === 'ArrowUp') return -1;
  if (e.key === 'ArrowDown') return 1;
  return null;
}

/** Liegt der Zeiger vor der Mitte der Zielkachel? Bei fast zeilenbreiten Kacheln zählt oben/unten,
 *  sonst links/rechts. */
export function pointerBefore(
  rect: { left: number; top: number; width: number; height: number },
  container: { width: number },
  x: number,
  y: number,
): boolean {
  const fullRow = rect.width >= container.width * 0.75;
  return fullRow ? y < rect.top + rect.height / 2 : x < rect.left + rect.width / 2;
}
