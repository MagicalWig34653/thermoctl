// Fetch-Wrapper für die UI-Schnittstelle (`{prefix}/ui-api/...`).
//
// Sitzung und CSRF laufen über Cookies, nicht über Tokens im Skript:
//  - die Sitzung (`thermoctl_session`) ist httpOnly und wird vom Browser mitgeschickt,
//  - der CSRF-Wert steht im lesbaren Cookie `thermoctl_csrf` und geht bei jedem
//    schreibenden Aufruf als Header `X-CSRF-Token` zurück (Double-Submit).
// Schreibende Aufrufe werden NIE automatisch wiederholt: Eine zweite Ausführung nach
// einem unklaren Ergebnis könnte etwas an der Heizung doppelt auslösen.

export const CSRF_COOKIE = 'thermoctl_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

export type ApiErrorKind = 'unauthorized' | 'forbidden' | 'http' | 'network' | 'client';

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
    /** 403 mit `stale: true`: Sitzung oder CSRF-Wert ist ungültig, die Anmeldung muss neu erfolgen. */
    readonly stale = false,
    /** Maschinenlesbarer Fehlercode des Servers (`{detail, code}`), falls geliefert. */
    readonly code: string | null = null,
    /** Betroffenes Eingabefeld (`field`), falls der Server es nennt. */
    readonly field: string | null = null,
    /** Restliche Felder des Fehlerkörpers (z. B. `conflicts`, `force_token` bei 409), sonst null. */
    readonly body: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ApiDeps {
  fetch: typeof fetch;
  prefix: string;
  /** Liefert den Rohwert von `document.cookie` (injizierbar für Tests). */
  cookies: () => string;
  /** Wird bei 401 und bei 403 mit `stale: true` aufgerufen, um zur Anmeldung zu wechseln. */
  onUnauthorized?: (reason: 'unauthorized' | 'stale') => void;
}

/** Wert eines Cookies aus einem `document.cookie`-Rohstring, sonst null.
 *  Wirft `URIError` bei ungültiger Prozentkodierung; `createApi` fängt das ab. */
export function readCookie(raw: string, name: string): string | null {
  for (const part of raw.split(';')) {
    const eq = part.indexOf('=');
    if (eq > -1 && part.slice(0, eq).trim() === name) {
      return decodeURIComponent(part.slice(eq + 1).trim());
    }
  }
  return null;
}

/** Pfadpräfix, den der Server zur Laufzeit in die Seite schreibt (leer ohne Präfix). */
export function runtimePrefix(doc: Document = document): string {
  const meta = doc.querySelector('meta[name="thermoctl-prefix"]')?.getAttribute('content');
  const raw = (meta ?? doc.getElementById('app')?.dataset.urlPrefix ?? '').replace(/\/+$/, '');
  // Nur Pfadsegmente zulassen: ein Präfix mit Schema, `//` oder Sonderzeichen würde Aufrufe
  // und Links an einen fremden Ort lenken.
  return /^(\/[A-Za-z0-9._~-]+)*$/.test(raw) ? raw : '';
}

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

interface ErrorBody {
  detail: string;
  stale: boolean;
  code: string | null;
  field: string | null;
  body: Record<string, unknown> | null;
}

async function errorBody(response: Response): Promise<ErrorBody> {
  try {
    const body = await response.json();
    return {
      detail: typeof body?.detail === 'string' ? body.detail : fallbackDetail(response),
      stale: body?.stale === true,
      code: typeof body?.code === 'string' ? body.code : null,
      field: typeof body?.field === 'string' ? body.field : null,
      body: body !== null && typeof body === 'object' && !Array.isArray(body) ? body : null,
    };
  } catch {
    // Kein JSON im Fehlerfall: Statuszeile genügt.
    return { detail: fallbackDetail(response), stale: false, code: null, field: null, body: null };
  }
}

function fallbackDetail(response: Response): string {
  return response.statusText || `HTTP ${response.status}`;
}

export function createApi(deps: ApiDeps) {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (!SAFE.has(method)) {
      let token: string | null;
      try {
        token = readCookie(deps.cookies(), CSRF_COOKIE);
      } catch {
        // Beschädigtes Cookie: lieber gar nicht senden als ohne oder mit falschem Wert.
        throw new ApiError('client', 0, 'Das Sicherheits-Cookie ist beschädigt. Bitte neu anmelden.');
      }
      if (token !== null) headers[CSRF_HEADER] = token;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    let response: Response;
    try {
      response = await deps.fetch(`${deps.prefix}${path}`, {
        method,
        headers,
        credentials: 'same-origin',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new ApiError('network', 0, error instanceof Error ? error.message : 'Netzwerkfehler');
    }

    if (response.status === 401) {
      deps.onUnauthorized?.('unauthorized');
      throw new ApiError('unauthorized', 401, (await errorBody(response)).detail);
    }
    if (response.status === 403) {
      const { detail, stale, code, field, body: extra } = await errorBody(response);
      if (stale) deps.onUnauthorized?.('stale');
      throw new ApiError('forbidden', 403, detail, stale, code, field, extra);
    }
    if (!response.ok) {
      const { detail, code, field, body: extra } = await errorBody(response);
      throw new ApiError('http', response.status, detail, false, code, field, extra);
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError('http', response.status, 'Die Antwort des Servers ist kein gültiges JSON.');
    }
  }

  return {
    get: <T>(path: string) => request<T>('GET', path),
    post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
    put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
    delete: <T>(path: string) => request<T>('DELETE', path),
  };
}

export type Api = ReturnType<typeof createApi>;
