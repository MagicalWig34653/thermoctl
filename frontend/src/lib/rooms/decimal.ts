// Temperaturen bleiben Zeichenketten. Es wird nie in `number` umgewandelt und gerundet:
// Anzeige, Vergleich und Eingabeprüfung arbeiten auf den Ziffern.

const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

interface Parts {
  negative: boolean;
  whole: string;
  fraction: string;
}

function parts(value: string): Parts | null {
  const match = DECIMAL.exec(value.trim());
  if (!match) return null;
  return { negative: match[1] === '-', whole: match[2].replace(/^0+(?=\d)/, ''), fraction: match[3] ?? '' };
}

/** `"21.50"` wird zu `"21,5"`, `"21"` zu `"21,0"`; nichts wird gerundet. Ungültig oder
 *  leer ergibt einen Gedankenstrich. */
export function formatTemperature(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const p = parts(value);
  if (!p) return '—';
  const fraction = p.fraction.replace(/0+$/, '').padEnd(1, '0');
  return `${p.negative ? '−' : ''}${p.whole},${fraction}`;
}

export function formatCelsius(value: string | null | undefined): string {
  const text = formatTemperature(value);
  return text === '—' ? text : `${text} °C`;
}

function scaled(p: Parts, digits: number): bigint {
  const n = BigInt(p.whole + p.fraction.padEnd(digits, '0'));
  return p.negative ? -n : n;
}

/** Vergleicht zwei Decimal-Strings exakt: negativ, 0 oder positiv. `null` bei ungültiger Eingabe. */
export function compareDecimal(a: string, b: string): number | null {
  const pa = parts(a);
  const pb = parts(b);
  if (!pa || !pb) return null;
  const digits = Math.max(pa.fraction.length, pb.fraction.length);
  const diff = scaled(pa, digits) - scaled(pb, digits);
  return diff < 0n ? -1 : diff > 0n ? 1 : 0;
}

export function sameTemperature(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a === b;
  return compareDecimal(a, b) === 0;
}

export type TemperatureInput = { ok: true; value: string } | { ok: false; message: string };

/** Prüft die Eingabe nur auf Form (Komma oder Punkt, höchstens zwei Nachkommastellen).
 *  Den zulässigen Bereich entscheidet der Server; er antwortet mit 422 und `field`. */
export function parseTemperatureInput(text: string): TemperatureInput {
  const cleaned = text.trim().replace(',', '.');
  if (cleaned === '') return { ok: false, message: 'Bitte eine Temperatur eingeben.' };
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(cleaned)) {
    return { ok: false, message: 'Bitte eine Temperatur wie 21,5 eingeben.' };
  }
  return { ok: true, value: cleaned };
}
