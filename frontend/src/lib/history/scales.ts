// Skalen: schöne Temperaturachse und Zeitmarken in der Zeitzone der Anlage.

export interface TemperatureScale {
  low: number;
  high: number;
  step: number;
  ticks: number[];
}

const STEPS = [0.5, 1, 2, 5, 10, 20];

/** Achse mit ganzen Schritten um die Werte; ohne Werte eine neutrale Spanne. */
export function temperatureScale(values: readonly number[], targetTicks = 5): TemperatureScale {
  const finite = values.filter(Number.isFinite);
  let min = finite.length ? Math.min(...finite) : 18;
  let max = finite.length ? Math.max(...finite) : 22;
  const pad = Math.max(0.3, (max - min) * 0.08);
  min -= pad;
  max += pad;
  const rough = (max - min) / Math.max(1, targetTicks - 1);
  const step = STEPS.find((s) => s >= rough) ?? STEPS[STEPS.length - 1];
  const low = Math.floor(min / step) * step;
  let high = Math.ceil(max / step) * step;
  if (high - low < step) high = low + step;
  const ticks: number[] = [];
  for (let v = low; v <= high + step / 1000; v += step) ticks.push(Math.round(v * 1000) / 1000);
  return { low, high, step, ticks };
}

/** Beschriftung einer Temperaturmarke mit Einheit. */
export function temperatureTickLabel(v: number): string {
  return `${String(v).replace('.', ',')} °C`;
}

// -- Zeit -------------------------------------------------------------------------------------

const formatters = new Map<string, Intl.DateTimeFormat>();
function formatter(tz: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(options);
  let f = formatters.get(key);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('de-DE', { ...options, timeZone: tz });
    } catch {
      f = new Intl.DateTimeFormat('de-DE', { ...options, timeZone: 'UTC' });
    }
    formatters.set(key, f);
  }
  return f;
}

/** Abweichung der Ortszeit von UTC in ms zum Zeitpunkt `t`. */
export function zoneOffsetMs(t: number, tz: string): number {
  const parts = formatter(tz, {
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  }).formatToParts(new Date(t));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'));
  return asUtc - Math.floor(t / 1000) * 1000;
}

const MIN = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;
const TICK_STEPS = [
  5 * MIN, 10 * MIN, 15 * MIN, 30 * MIN, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR,
  DAY, 2 * DAY, 7 * DAY,
];

export interface TimeTick {
  t: number;
  label: string;
}

export function formatClock(t: number, tz: string): string {
  return formatter(tz, { hour: '2-digit', minute: '2-digit' }).format(new Date(t));
}

export function formatDay(t: number, tz: string): string {
  return formatter(tz, { day: '2-digit', month: '2-digit' }).format(new Date(t));
}

/** „Mo., 14.10., 15:30 Uhr“ */
export function formatMoment(t: number, tz: string): string {
  const day = formatter(tz, { weekday: 'short', day: '2-digit', month: '2-digit' }).format(new Date(t));
  return `${day}, ${formatClock(t, tz)} Uhr`;
}

/** Zeitmarken auf runden Ortszeiten, höchstens `maxCount`; die Beschriftung passt zum Abstand. */
export function timeTicks(start: number, end: number, tz: string, maxCount = 6): TimeTick[] {
  if (!(end > start)) return [];
  const span = end - start;
  const step = TICK_STEPS.find((s) => span / s <= maxCount) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const offset = zoneOffsetMs(start, tz);
  const first = Math.ceil((start + offset) / step) * step;
  const out: TimeTick[] = [];
  for (let local = first; ; local += step) {
    // Versatz am Ziel nehmen, damit Zeitumstellungen im Ausschnitt stimmen.
    const t = local - zoneOffsetMs(local - offset, tz);
    if (t > end) break;
    if (t >= start) {
      const label = step >= DAY ? formatDay(t, tz) : span > DAY ? `${formatDay(t, tz)} ${formatClock(t, tz)}` : formatClock(t, tz);
      out.push({ t, label });
    }
    if (out.length > 64) break;
  }
  return out;
}
