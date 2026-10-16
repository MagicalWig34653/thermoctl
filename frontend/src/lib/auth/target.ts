// Rücksprungziel nach der Anmeldung. Eigene Datei, damit `session.svelte.ts` und die
// Passkey-Anmeldung (`login.ts`) einander nicht gegenseitig einbinden.
//
// Das Ziel wird NIE ungeprüft übernommen. Der Client schickt den Wunsch aus der Adresse
// (`?return_to=`) mit; ob er gilt, entscheidet allein der Server (`safe_return_to`), und nur
// dessen Antwort (`return_to`) wird verwendet -- hier zusätzlich noch einmal auf Form geprüft,
// weil ein Fehler an dieser Stelle ein Open Redirect wäre.

const MAX_TARGET = 2048;

/** Wunsch aus der Adresszeile (`?return_to=/oauth/consent?...`), nur Rohwert: der Server prüft. */
export function requestedReturnTo(search: string): string | null {
  const value = new URLSearchParams(search).get('return_to');
  return value !== null && value !== '' && value.length <= MAX_TARGET ? value : null;
}

/** Vom Server bestätigtes Ziel, auf Form geprüft: ein Pfad der eigenen Herkunft (`/x`),
 *  kein `//`, kein Backslash, keine Steuerzeichen. Alles andere ergibt null. */
export function acceptedTarget(value: unknown): string | null {
  if (typeof value !== 'string' || !value.startsWith('/') || value.length > MAX_TARGET) return null;
  if (value.startsWith('//') || value.includes('\\')) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return null;
  return value;
}

/** Vollständige Seitenadresse für ein bestätigtes Ziel. Der Präfix ist der Laufzeitpräfix der Seite. */
export function targetHref(prefix: string, target: string): string {
  return `${prefix}${target}`;
}

export interface LoginReply {
  ok?: boolean;
  return_to?: unknown;
}

/** Aus der Antwort der Passwort-Anmeldung: das bestätigte Ziel, sonst null. */
export function replyTarget(reply: LoginReply | undefined): string | null {
  return acceptedTarget(reply?.return_to);
}
