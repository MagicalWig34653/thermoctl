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

export type ApiErrorKind = 'unauthorized' | 'forbidden' | 'http' | 'network';

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    message: string,
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
  /** Wird bei 401 aufgerufen, z. B. um zur Anmeldung zu wechseln. */
  onUnauthorized?: () => void;
}

/** Wert eines Cookies aus einem `document.cookie`-Rohstring, sonst null. */
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
  const raw = meta ?? doc.getElementById('app')?.dataset.urlPrefix ?? '';
  return raw.replace(/\/+$/, '');
}

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    // Kein JSON im Fehlerfall: Statuszeile genügt.
  }
  return response.statusText || `HTTP ${response.status}`;
}

export function createApi(deps: ApiDeps) {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (!SAFE.has(method)) {
      const token = readCookie(deps.cookies(), CSRF_COOKIE);
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
      deps.onUnauthorized?.();
      throw new ApiError('unauthorized', 401, await detail(response));
    }
    if (response.status === 403) {
      throw new ApiError('forbidden', 403, await detail(response));
    }
    if (!response.ok) {
      throw new ApiError('http', response.status, await detail(response));
    }
    return (response.status === 204 ? undefined : await response.json()) as T;
  }

  return {
    get: <T>(path: string) => request<T>('GET', path),
    post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
    put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
    delete: <T>(path: string) => request<T>('DELETE', path),
  };
}

export type Api = ReturnType<typeof createApi>;
