// Ersteinrichtung (`/ui-api/setup`): Status und Abschluss. Die Prüfung und die Sperre nach
// der Einrichtung liegen allein beim Server; die Vorabprüfung hier spart nur den Weg dorthin.

import { ApiError, type Api } from '../api';

export const FIELDS = ['setup_token', 'username', 'display_name', 'password', 'timezone'] as const;
export type SetupField = (typeof FIELDS)[number];
export type SetupForm = Record<SetupField, string>;
export type FieldErrors = Partial<Record<SetupField, string>>;

export const LIMITS: Record<'username' | 'display_name' | 'timezone', number> = {
  username: 64,
  display_name: 128,
  timezone: 64,
};

export type SetupStatus = 'open' | 'closed' | 'error';

/** Ist die Einrichtung offen? 404 heißt abgeschlossen; alles andere Unklare ist ein Fehler. */
export async function loadSetupStatus(api: Api): Promise<SetupStatus> {
  try {
    const reply = await api.get<{ open?: unknown }>('/ui-api/setup');
    return reply?.open === true ? 'open' : 'error';
  } catch (error) {
    return error instanceof ApiError && error.status === 404 ? 'closed' : 'error';
  }
}

/** Zeitzone des Browsers als Vorschlag; der Server prüft den Namen. */
export function suggestedTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin';
  } catch {
    return 'Europe/Berlin';
  }
}

/** Vorabprüfung der Eingaben (leer, zu lang). Das Passwort wird nicht verändert; seine
 *  Mindestlänge kennt nur der Server. */
export function validateLocal(form: SetupForm): FieldErrors {
  const errors: FieldErrors = {};
  if (form.setup_token.trim() === '') errors.setup_token = 'Bitte das Einrichtungs-Token eintragen.';
  if (form.password === '') errors.password = 'Bitte ein Passwort wählen.';
  for (const field of ['username', 'display_name', 'timezone'] as const) {
    const value = form[field].trim();
    if (value === '') errors[field] = 'Bitte ausfüllen.';
    else if (value.length > LIMITS[field]) errors[field] = `Höchstens ${LIMITS[field]} Zeichen.`;
  }
  return errors;
}

export type SetupOutcome =
  | { kind: 'done' }
  | { kind: 'closed' }
  | { kind: 'field'; field: SetupField; message: string }
  | { kind: 'error'; message: string };

export const TOKEN_REFUSED = 'Das Einrichtungs-Token ist ungültig, bereits verbraucht oder abgelaufen.';

const isField = (value: string | null): value is SetupField => (FIELDS as readonly string[]).includes(value ?? '');

/** Schließt die Einrichtung ab. Schreibende Aufrufe werden nie wiederholt (siehe api.ts). */
export async function submitSetup(api: Api, form: SetupForm): Promise<SetupOutcome> {
  try {
    await api.post('/ui-api/setup', {
      setup_token: form.setup_token.trim(),
      username: form.username.trim(),
      display_name: form.display_name.trim(),
      password: form.password,
      timezone: form.timezone.trim(),
    });
    return { kind: 'done' };
  } catch (error) {
    if (!(error instanceof ApiError)) return { kind: 'error', message: 'Die Einrichtung ist fehlgeschlagen.' };
    if (error.status === 404) return { kind: 'closed' };
    if (error.kind === 'network') return { kind: 'error', message: 'Der Server ist nicht erreichbar.' };
    if (error.stale) {
      return { kind: 'error', message: 'Die Seite ist veraltet. Bitte neu laden und erneut versuchen.' };
    }
    if ((error.status === 403 || error.status === 422) && isField(error.field)) {
      // Ein Text für ungültig, verbraucht und abgelaufen: der Server unterscheidet sie nicht, und
      // wer das Token nicht hat, soll auch aus der Meldung nichts lernen.
      const message = error.field === 'setup_token' ? TOKEN_REFUSED : error.message;
      return { kind: 'field', field: error.field, message };
    }
    return { kind: 'error', message: 'Die Einrichtung ist fehlgeschlagen.' };
  }
}
