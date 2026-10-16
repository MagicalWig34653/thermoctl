// Hilfen für die Wochenplan-Tests: ein Ring aus Schaltpunkten wie ihn der echte Server liefert.
import { splitRange, WEEK, weekMinute, type Block } from '../src/lib/schedule/geometry.ts';

/** Ring aus Beginnen `[Wochentag, Minute, Modus]`; jedes Ende ist der nächste Beginn (rundum). */
export function ring(starts: [number, number, number][]): Block[] {
  const sorted = starts
    .map(([weekday, start, modeId]) => ({ weekday, start, modeId, at: weekMinute(weekday, start) }))
    .sort((a, b) => a.at - b.at);
  return sorted.map((s, i) => {
    const next = sorted[(i + 1) % sorted.length];
    const length = sorted.length === 1 ? WEEK : (next.at - s.at + WEEK) % WEEK;
    return {
      weekday: s.weekday,
      start: s.start,
      end: s.start + length,
      pointId: i + 1,
      modeId: s.modeId,
      modeName: `M${s.modeId}`,
      warmth: 0.5,
      temperatureC: null,
      pieces: splitRange(s.weekday, s.start, s.start + length),
    };
  });
}
