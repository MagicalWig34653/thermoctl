// Anmeldezustand der Oberfläche: Bootstrap laden, anmelden, abmelden, Ablauf behandeln.
//
// Schreibende Aufrufe werden nie automatisch wiederholt (siehe api.ts). Läuft die Sitzung
// ab, wechselt die Oberfläche zur Anmeldung und behält die Adresse bei; nach dem Anmelden
// steht dieselbe Ansicht wieder da.

import { ApiError, type Api } from './api';
import { replyTarget, type LoginReply } from './auth/target';
import type { ServerNavItem } from './navigation';

export interface Bootstrap {
  user_id: number;
  ui_profile: string;
  navigation: ServerNavItem[];
  zones: { id: number; name: string }[];
}

export type SessionStatus = 'loading' | 'anonymous' | 'ready' | 'error';

export const MESSAGES = {
  expired: 'Die Sitzung ist abgelaufen. Bitte erneut anmelden.',
  stale: 'Die Sitzung ist nicht mehr gültig. Bitte erneut anmelden.',
  loginFailed: 'Anmeldung nicht möglich.',
  network: 'Der Server ist nicht erreichbar.',
} as const;

/** Meldung für einen fehlgeschlagenen Anmeldeversuch. Der Text des Servers ist bewusst
 *  allgemein gehalten (kein Hinweis, ob der Benutzer existiert); alles Unerwartete bekommt
 *  einen eigenen, ebenso neutralen Text. */
export function loginMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === 'unauthorized' || error.status === 429) return error.message;
    if (error.kind === 'network') return MESSAGES.network;
    if (error.stale) return MESSAGES.stale;
  }
  return MESSAGES.loginFailed;
}

export function createSession(api: Api) {
  let status = $state<SessionStatus>('loading');
  let bootstrap = $state<Bootstrap | null>(null);
  let notice = $state<string | null>(null);
  let failure = $state<string | null>(null);

  /** Wird von der API-Schicht bei 401 und ungültigem CSRF-Wert aufgerufen. */
  function expire(reason: 'unauthorized' | 'stale') {
    if (status === 'ready') {
      notice = reason === 'stale' ? MESSAGES.stale : MESSAGES.expired;
    }
    status = 'anonymous';
    bootstrap = null;
  }

  async function load() {
    failure = null;
    try {
      bootstrap = await api.get<Bootstrap>('/ui-api/bootstrap');
      status = 'ready';
    } catch (error) {
      if (error instanceof ApiError && error.kind === 'unauthorized') {
        // expire() hat den Zustand schon gesetzt.
        return;
      }
      failure = error instanceof ApiError && error.kind === 'network' ? MESSAGES.network : error instanceof ApiError ? `${error.status} ${error.message}` : String(error);
      status = 'error';
    }
  }

  /** Übernimmt eine soeben eröffnete Sitzung (Cookie ist gesetzt): Bootstrap laden.
   *  Liefert null bei Erfolg, sonst die Meldung. */
  async function finishLogin(): Promise<string | null> {
    notice = null;
    await load();
    return status === 'ready' ? null : (failure ?? MESSAGES.loginFailed);
  }

  /** Meldet mit Passwort an. `returnTo` ist nur ein Wunsch; zurück kommt das vom Server
   *  bestätigte Ziel (oder null). Bei Erfolg ist `error` null. */
  async function login(
    username: string,
    password: string,
    returnTo: string | null = null,
  ): Promise<{ error: string | null; target: string | null }> {
    let target: string | null = null;
    try {
      const reply = await api.post<LoginReply>('/ui-api/login', {
        username,
        password,
        ...(returnTo !== null ? { return_to: returnTo } : {}),
      });
      target = replyTarget(reply);
    } catch (error) {
      return { error: loginMessage(error), target: null };
    }
    // Mit bestätigtem Ziel verlässt die Seite die Oberfläche gleich (Voll-Seitenwechsel);
    // den Bootstrap braucht dann niemand mehr.
    if (target !== null) return { error: null, target };
    return { error: await finishLogin(), target: null };
  }

  /** Meldet ab; liefert eine Fehlermeldung, wenn der Server nicht geantwortet hat. */
  async function logout(): Promise<string | null> {
    try {
      await api.post('/ui-api/logout');
    } catch (error) {
      // 401/ungültiger CSRF-Wert: die Sitzung ist ohnehin weg (expire hat reagiert).
      if (status !== 'ready') return null;
      return error instanceof ApiError && error.kind === 'network' ? MESSAGES.network : 'Abmelden ist fehlgeschlagen.';
    }
    notice = null;
    bootstrap = null;
    status = 'anonymous';
    return null;
  }

  return {
    get status() {
      return status;
    },
    get bootstrap() {
      return bootstrap;
    },
    get notice() {
      return notice;
    },
    get failure() {
      return failure;
    },
    load,
    login,
    finishLogin,
    logout,
    expire,
  };
}

export type Session = ReturnType<typeof createSession>;
