// Darstellung der Rechte einer Gruppe als Zeilen je Recht mit „Anlage“ und einzelnen Räumen.
// Reine Darstellung: Welche Rechte es gibt, ob sie raumbezogen sind und was erlaubt ist,
// kommt vom Server (`areas`, `zones`), und der Server prüft jede Speicherung erneut.
import type { Grant, PermissionArea, ZoneRef } from './types';

export interface MatrixCell {
  zoneId: number;
  zoneName: string;
  granted: boolean;
}

export interface MatrixRow {
  code: string;
  description: string;
  zoneScoped: boolean;
  /** Anlagenweit erteilt (zone_id = null). Das deckt laut Server auch jeden Raum ab. */
  plantWide: boolean;
  /** Nur bei raumbezogenen Rechten gefüllt. */
  rooms: MatrixCell[];
}

export interface MatrixArea {
  title: string;
  hint: string;
  rows: MatrixRow[];
}

export const keyOf = (g: Grant): string => `${g.code}|${g.zone_id ?? ''}`;

export function buildMatrix(areas: readonly PermissionArea[], zones: readonly ZoneRef[], grants: readonly Grant[]): MatrixArea[] {
  const held = new Set(grants.map(keyOf));
  return areas.map((area) => ({
    title: area.title,
    hint: area.hint,
    rows: area.permissions.map((p) => ({
      code: p.code,
      description: p.description,
      zoneScoped: p.zone_scoped,
      plantWide: held.has(`${p.code}|`),
      rooms: p.zone_scoped ? zones.map((z) => ({ zoneId: z.id, zoneName: z.name, granted: held.has(`${p.code}|${z.id}`) })) : [],
    })),
  }));
}

/** Schaltet genau eine Erteilung um und liefert die neue vollständige Liste (stabil sortiert). */
export function toggleGrant(grants: readonly Grant[], code: string, zoneId: number | null): Grant[] {
  const target = `${code}|${zoneId ?? ''}`;
  const has = grants.some((g) => keyOf(g) === target);
  const next = has ? grants.filter((g) => keyOf(g) !== target) : [...grants, { code, zone_id: zoneId }];
  return sortGrants(next);
}

export function sortGrants(grants: readonly Grant[]): Grant[] {
  return [...grants].sort((a, b) => a.code.localeCompare(b.code) || (a.zone_id ?? 0) - (b.zone_id ?? 0));
}

export function sameGrants(a: readonly Grant[], b: readonly Grant[]): boolean {
  if (a.length !== b.length) return false;
  const keys = new Set(a.map(keyOf));
  return b.every((g) => keys.has(keyOf(g)));
}

/** Zahl der Erteilungen, die sich gegenüber dem gespeicherten Stand unterscheiden. */
export function changeCount(saved: readonly Grant[], draft: readonly Grant[]): number {
  const s = new Set(saved.map(keyOf));
  const d = new Set(draft.map(keyOf));
  let n = 0;
  for (const k of d) if (!s.has(k)) n += 1;
  for (const k of s) if (!d.has(k)) n += 1;
  return n;
}

/** Kurzfassung für eine Zeile: „3 anlagenweit, 2 für einzelne Räume“. */
export function grantSummary(grants: readonly Grant[]): string {
  const wide = grants.filter((g) => g.zone_id === null).length;
  const room = grants.length - wide;
  if (!grants.length) return 'Keine Rechte';
  const parts = [];
  if (wide) parts.push(`${wide} anlagenweit`);
  if (room) parts.push(`${room} für einzelne Räume`);
  return parts.join(', ');
}

/** Name des Oberflächenprofils für Menschen; unbekannte Werte erscheinen unverändert. */
export function profileLabel(profile: string): string {
  if (profile === 'admin') return 'Anlagensicht';
  if (profile === 'tenant') return 'Mietersicht';
  return profile;
}
