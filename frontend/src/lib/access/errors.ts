// Fehlerabbildung der Zugangsansichten (rein, getestet). Der Server entscheidet; hier wird
// nur in verständliche Sätze übersetzt und festgelegt, ob danach neu gelesen wird.
// Schreibende Aufrufe werden nie wiederholt.
import { ApiError } from '../api';

export type AccessFailureKind =
  | 'session' | 'network' | 'forbidden' | 'conflict' | 'not_found' | 'validation' | 'server';

export interface AccessFailure {
  kind: AccessFailureKind;
  message: string;
  field: string | null;
  code: string | null;
  /** Danach den Serverstand lesen (Lesen ist gefahrlos). */
  reload: boolean;
}

export const SESSION_MESSAGE = 'Die Sitzung ist abgelaufen. Bitte erneut anmelden – die Eingabe wurde nicht gesendet.';
export const NETWORK_MESSAGE =
  'Der Server ist nicht erreichbar. Ob die Änderung angekommen ist, ist unklar – bitte den Zustand prüfen, bevor du es erneut versuchst.';

/** Feste Sätze für Fehlercodes, bei denen der Servertext zu knapp oder zu technisch ist. */
const BY_CODE: Record<string, string> = {
  last_administrator:
    'Das ist nicht möglich: Danach gäbe es niemanden mehr mit Administratorrechten, der aktiv ist. Weise zuerst einer anderen Person diese Rechte zu.',
  permission_not_held:
    'Ein gewähltes Recht hast du selbst nicht. Ein Token kann nie mehr als dein eigenes Konto.',
  wrong_password: 'Das bisherige Passwort stimmt nicht.',
  current_session: 'Das ist die Sitzung, mit der du gerade arbeitest. Zum Beenden nutze „Abmelden“.',
  builtin_group: 'Eingebaute Gruppen lassen sich nicht löschen.',
  wrong_profile: 'Diese Aktion gehört nicht zu deiner Oberfläche.',
  token_not_active: 'Widerrufene oder abgelaufene Tokens lassen sich nicht mehr ändern.',
};

/** Konflikte, die kein veralteter Stand sind: die Eingabe ist das Problem, Neulesen ändert nichts. */
const FINAL_CONFLICTS = new Set(['last_administrator', 'builtin_group', 'current_session', 'own_account', 'username_taken', 'name_taken']);

export function accessFailure(error: unknown): AccessFailure {
  if (!(error instanceof ApiError)) {
    return { kind: 'server', message: 'Unerwarteter Fehler.', field: null, code: null, reload: false };
  }
  const base = { field: error.field, code: error.code };
  if (error.kind === 'unauthorized' || error.stale) {
    return { ...base, kind: 'session', message: SESSION_MESSAGE, reload: false };
  }
  if (error.kind === 'network') return { ...base, kind: 'network', message: NETWORK_MESSAGE, reload: true };
  const known = error.code !== null ? BY_CODE[error.code] : undefined;
  if (error.kind === 'forbidden') {
    // wrong_password und permission_not_held sind Antworten auf die Eingabe, kein Rechteverlust.
    const input = error.code === 'wrong_password' || error.code === 'permission_not_held';
    return {
      ...base,
      kind: 'forbidden',
      message: known ?? 'Dafür fehlt dir die Berechtigung.',
      reload: !input,
    };
  }
  if (error.kind === 'client') return { ...base, kind: 'server', message: error.message, reload: false };
  const text = known ?? error.message;
  if (error.status === 404) return { ...base, kind: 'not_found', message: text || 'Nicht gefunden.', reload: true };
  if (error.status === 409) return { ...base, kind: 'conflict', message: text, reload: !FINAL_CONFLICTS.has(error.code ?? '') };
  if (error.status === 422) return { ...base, kind: 'validation', message: text, reload: false };
  return { ...base, kind: 'server', message: text || `Der Server meldet einen Fehler (${error.status}).`, reload: false };
}
