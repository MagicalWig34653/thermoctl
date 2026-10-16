// Eigener, kleiner Client für `{prefix}/kiosk-api/...`.
//
// Bewusst NICHT `createApi` aus `api.ts`: Dessen Fehlerbehandlung führt bei 401 zur
// Anmeldung der Hauptanwendung. Das Kiosk hat keine Anmeldung -- es gibt nur das
// Kiosk-Cookie, und ein widerrufener Zugang endet in einem Hinweis, nie in einer
// Anmeldeseite. Darum hier:
//  - kein Umleiten (`redirect: 'error'`): eine Weiterleitung wird zum Fehler statt
//    stillschweigend irgendwohin zu führen,
//  - der CSRF-Wert kommt aus dem Kiosk-Cookie (`thermoctl_kiosk_csrf`), nicht aus dem
//    Sitzungs-Cookie,
//  - kein Aufruf wird wiederholt, schreibende erst recht nicht: Eine zweite Ausführung
//    nach einem unklaren Ergebnis könnte die Heizung doppelt verstellen.

import { CSRF_HEADER, readCookie } from '../api';
import type { KioskRoom, KioskState } from './types';

export const KIOSK_CSRF_COOKIE = 'thermoctl_kiosk_csrf';

export type KioskErrorKind =
  | 'invalid' // Zugang widerrufen, abgelaufen oder nicht (mehr) vorhanden
  | 'forbidden' // Aktion für diesen Raum nicht erlaubt
  | 'not_found' // Raum für diesen Zugang nicht (mehr) vorhanden
  | 'csrf' // Sicherheitswert fehlt oder stimmt nicht
  | 'rejected' // von der Regelung abgelehnt (z. B. Sollwert außerhalb des Bereichs)
  | 'setup' // Anlage noch nicht fertig eingerichtet
  | 'network' // keine Antwort bekommen
  | 'http' // sonstiger Fehler
  | 'client'; // lokal abgebrochen, nichts gesendet

export class KioskError extends Error {
  constructor(
    readonly kind: KioskErrorKind,
    readonly status: number,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = 'KioskError';
  }
}

export interface KioskClientDeps {
  fetch: typeof fetch;
  prefix: string;
  /** Rohwert von `document.cookie` (injizierbar für Tests). */
  cookies: () => string;
}

export function kioskErrorKind(status: number, code: string | null): KioskErrorKind {
  if (status === 401 && code === 'kiosk_token_invalid') return 'invalid';
  if (status === 403) return code === 'csrf' ? 'csrf' : 'forbidden';
  if (status === 404 && code === 'not_found') return 'not_found';
  if (status === 422 && code === 'rejected') return 'rejected';
  if (status === 503 && code === 'setup_incomplete') return 'setup';
  return 'http';
}

async function failure(response: Response): Promise<KioskError> {
  let detail = response.statusText || `HTTP ${response.status}`;
  let code: string | null = null;
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') detail = body.detail;
    if (typeof body?.code === 'string') code = body.code;
  } catch {
    // Kein JSON: die Statuszeile genügt.
  }
  return new KioskError(kioskErrorKind(response.status, code), response.status, detail, code);
}

export function createKioskClient(deps: KioskClientDeps) {
  async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (method !== 'GET') {
      let token: string | null;
      try {
        token = readCookie(deps.cookies(), KIOSK_CSRF_COOKIE);
      } catch {
        token = null;
      }
      // Ohne Wert gar nicht erst senden: der Server würde ohnehin ablehnen.
      if (token === null) {
        throw new KioskError(
          'client',
          0,
          'Der Sicherheitswert dieses Zugangs fehlt. Bitte den Kiosk-Link erneut öffnen.',
          'csrf_missing',
        );
      }
      headers[CSRF_HEADER] = token;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    let response: Response;
    try {
      response = await deps.fetch(`${deps.prefix}/kiosk-api${path}`, {
        method,
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new KioskError('network', 0, error instanceof Error ? error.message : 'Netzwerkfehler');
    }
    if (!response.ok) throw await failure(response);
    try {
      return (await response.json()) as T;
    } catch {
      throw new KioskError('http', response.status, 'Die Antwort des Servers ist kein gültiges JSON.');
    }
  }

  return {
    state: () => request<KioskState>('GET', '/state'),
    step: (roomId: number, direction: 'up' | 'down') =>
      request<KioskRoom>('POST', `/rooms/${roomId}/setpoint-step`, { direction }),
    boost: (roomId: number) => request<KioskRoom>('POST', `/rooms/${roomId}/boost`),
    cancelOverride: (roomId: number) => request<KioskRoom>('POST', `/rooms/${roomId}/override/cancel`),
  };
}

export type KioskClient = ReturnType<typeof createKioskClient>;
