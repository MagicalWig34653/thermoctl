// Rolle dieser Instanz im Verbund, als Darstellung der Serverwerte (nichts wird im Browser entschieden).
// `determining` bedeutet: Die Instanz ist frisch gestartet und wartet, bis der Anspruch einer eventuell
// abgestürzten Vorgängerin abläuft. Sie schaltet dabei NICHTS, ist aber noch keine nachgewiesener Standby.

export type ClusterRole = 'leading' | 'standby' | 'determining';

export interface RoleInfo {
  cluster_standby: boolean;
  cluster_state?: ClusterRole;
  determining_until?: string | null;
}

/** Ältere Antworten ohne `cluster_state`: `cluster_standby` entscheidet wie bisher. */
export function roleOf(a: RoleInfo): ClusterRole {
  if (a.cluster_state) return a.cluster_state;
  return a.cluster_standby ? 'standby' : 'leading';
}

/** Uhrzeit (HH:MM, Ortszeit des Browsers) aus der ISO-Zeit, sonst null. */
export function untilText(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

/** Kurzer Text für Wert und Hinweis. */
export function roleLabel(a: RoleInfo): { value: string; tone: 'ok' | 'warn' | 'neutral'; short: string } {
  const role = roleOf(a);
  if (role === 'leading') return { value: 'Primär', tone: 'ok', short: 'Primär' };
  if (role === 'determining') {
    const until = untilText(a.determining_until);
    const value = until ? `Wird ermittelt (bis ca. ${until})` : 'Wird ermittelt';
    return { value, tone: 'neutral', short: 'Primärrolle wird ermittelt' };
  }
  return { value: 'Standby – schaltet nicht', tone: 'warn', short: 'Standby' };
}
