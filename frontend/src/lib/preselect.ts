// Voreinstellungen aus der Adresse (Direktlinks und Sprunglinks): `?zone=`, `?period=`,
// `?tab=`, `?suche=` und das Fragment der Hilfe. Rein, damit es ohne Svelte prüfbar ist.
//
// Grundsatz: Die Adresse ist eine Eingabe, kein Beleg. Ein Wert gilt nur, wenn er eine der
// Möglichkeiten trifft, die die Seite ohnehin kennt (ein sichtbarer Raum aus dem Bootstrap,
// ein bekannter Zeitraum, ein Reiter). Alles andere wird still verworfen -- ohne Hinweis, ob
// es die Id gibt oder wem sie gehört. Ob etwas erlaubt ist, entscheidet der Server bei jedem
// Zugriff erneut.

import { PERIODS, type PeriodId } from './history/format';

/** Der Raum aus `?zone=`, nur wenn er unter den sichtbaren Räumen vorkommt. */
export function zoneFromSearch(search: string, zones: readonly { id: number }[]): number | null {
  const raw = new URLSearchParams(search).get('zone');
  if (raw === null || !/^[0-9]{1,9}$/.test(raw)) return null;
  const id = Number(raw);
  return zones.some((z) => z.id === id) ? id : null;
}

/** Wert von `?zone=` für „alle Räume“ auf der Verlaufsseite. */
export const ALL_ZONES = 'alle';

/** Der Standardraum der Verlaufsseite: der erste sichtbare Raum in der Reihenfolge der Seite. */
export function defaultZoneId(zones: readonly { id: number }[]): number | null {
  return zones.length > 0 ? zones[0].id : null;
}

/** Die ausdrückliche Wahl aus `?zone=`: ein sichtbarer Raum, `'all'` (`?zone=alle`) oder `null`
 *  (keine oder eine unbrauchbare Angabe). „alle“ gilt nur, wenn es Räume gibt. */
export function zoneChoiceFromSearch(
  search: string,
  zones: readonly { id: number }[],
): number | 'all' | null {
  if (zones.length === 0) return null;
  if (new URLSearchParams(search).get('zone') === ALL_ZONES) return 'all';
  return zoneFromSearch(search, zones);
}

/** Welcher Raum die Verlaufsseite zeigt: die Wahl aus der Adresse, sonst der Standardraum.
 *  `null` heißt „alle Räume“ (nur auf ausdrücklichen Wunsch) -- oder: es gibt keinen Raum. */
export function resolveHistoryZone(search: string, zones: readonly { id: number }[]): number | null {
  const choice = zoneChoiceFromSearch(search, zones);
  if (choice === 'all') return null;
  return choice ?? defaultZoneId(zones);
}

/** Die Adresse (nur der Suchteil) nach einer Raumwahl: `zone` wird gesetzt, alles andere bleibt.
 *  `null` steht für „alle Räume“. */
export function withZoneChoice(search: string, zoneId: number | null): string {
  const params = new URLSearchParams(search);
  params.set('zone', zoneId === null ? ALL_ZONES : String(zoneId));
  return `?${params.toString()}`;
}

/** Der Zeitraum aus `?period=`, nur ein bekannter. */
export function periodFromSearch(search: string): PeriodId | null {
  const raw = new URLSearchParams(search).get('period');
  return PERIODS.find((p) => p.id === raw)?.id ?? null;
}

/** Ein Wert aus einer festen Liste (`?tab=`), sonst `null`. */
export function choiceFromSearch<T extends string>(
  search: string,
  name: string,
  allowed: readonly T[],
): T | null {
  const raw = new URLSearchParams(search).get(name);
  return allowed.find((a) => a === raw) ?? null;
}

/** Suchbegriff der Hilfe aus `?suche=`: kurz, ohne Steuerzeichen. */
export function termFromSearch(search: string): string {
  const raw = new URLSearchParams(search).get('suche') ?? '';
  // eslint-disable-next-line no-control-regex
  return raw.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 80);
}

/** Ein Glossarbegriff aus dem Fragment (`#sollwert`), nur wenn es ihn gibt. */
export function entryFromHash(hash: string, ids: readonly string[]): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  return ids.find((id) => id === decoded) ?? null;
}
