// Lage des Tooltips: bleibt innerhalb der Ansicht, auch am linken und rechten Rand.

export interface Placement {
  left: number;
  side: 'right' | 'left';
}

/**
 * `anchorX`: Position der Markierung im Container, `width`: Breite des Tooltips,
 * `bounds`: erlaubter Bereich im selben Koordinatensystem. Bevorzugt rechts vom Anker; passt es
 * dort nicht, links davon; passt es nirgends ganz, wird an den Rand geklemmt.
 */
export function tooltipPlacement(
  anchorX: number,
  width: number,
  bounds: { left: number; right: number },
  gap = 14,
): Placement {
  let side: 'right' | 'left' = 'right';
  let left = anchorX + gap;
  if (left + width > bounds.right) {
    side = 'left';
    left = anchorX - gap - width;
  }
  const maxLeft = bounds.right - width;
  left = Math.min(left, maxLeft);
  left = Math.max(left, bounds.left);
  return { left, side };
}
