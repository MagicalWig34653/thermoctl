// Anmeldung vor der Sitzung: Rücksprungziel und Passkey-Anmeldung (WebAuthn im Browser).
//
// Das Rücksprungziel (target.ts) wird nie ungeprüft übernommen; der Server entscheidet.

import { ApiError, type Api } from '../api';
import { base64urlToBytes, bytesToBase64url } from '../access/passkey';
import { MESSAGES } from '../session.svelte';
import { replyTarget, type LoginReply } from './target';

/** Ob die Anmeldeseite den Passkey-Knopf anbieten darf (Schalter der Betriebskonfiguration). */
export async function fetchPasskeysOffered(api: Api): Promise<boolean> {
  try {
    const reply = await api.get<{ passkeys_available?: unknown }>('/ui-api/login-options');
    return reply?.passkeys_available === true;
  } catch {
    // Ohne Antwort kein Knopf; die Anmeldung mit Passwort bleibt.
    return false;
  }
}

interface Descriptor {
  id: string;
  [k: string]: unknown;
}
export interface AuthenticationOptions {
  challenge: string;
  allowCredentials?: Descriptor[];
  [k: string]: unknown;
}

/** Macht aus den Serverargumenten, was `navigator.credentials.get()` erwartet. */
export function prepareAuthentication(args: AuthenticationOptions): PublicKeyCredentialRequestOptions {
  const ready = { ...args, challenge: base64urlToBytes(args.challenge) } as unknown as PublicKeyCredentialRequestOptions;
  if (Array.isArray(args.allowCredentials)) {
    ready.allowCredentials = args.allowCredentials.map((e) => ({
      ...e,
      id: base64urlToBytes(e.id),
    })) as unknown as PublicKeyCredentialDescriptor[];
  }
  return ready;
}

/** Körper für `/passkey/authentication/verify`; `return_to` nur, wenn ein Wunsch vorliegt. */
export function assertionBody(credential: PublicKeyCredential, returnTo: string | null) {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      authenticatorData: bytesToBase64url(response.authenticatorData),
      signature: bytesToBase64url(response.signature),
      userHandle: response.userHandle ? bytesToBase64url(response.userHandle) : null,
    },
    ...(returnTo !== null ? { return_to: returnTo } : {}),
  };
}

export interface CredentialsLike {
  get(options: CredentialRequestOptions): Promise<Credential | null>;
}

/** Führt die Passkey-Anmeldung aus. Die Sitzung setzt der Server (Cookie) bei erfolgreicher
 *  Prüfung; geliefert wird das vom Server bestätigte Rücksprungziel oder null. */
export async function passkeyLogin(
  deps: { api: Api; credentials: CredentialsLike | undefined },
  returnTo: string | null,
): Promise<{ target: string | null }> {
  if (!deps.credentials) throw new Error('Dieser Browser unterstützt keine Passkeys.');
  const options = await deps.api.post<AuthenticationOptions>('/passkey/authentication/options');
  const credential = await deps.credentials.get({ publicKey: prepareAuthentication(options) });
  if (!credential) throw new Error('Es wurde kein Passkey ausgewählt.');
  const reply = await deps.api.post<LoginReply>(
    '/passkey/authentication/verify',
    assertionBody(credential as PublicKeyCredential, returnTo),
  );
  return { target: replyTarget(reply) };
}

export const PASSKEY_REFUSED = 'Die Anmeldung war nicht erfolgreich.';

/** Meldung für einen fehlgeschlagenen Passkey-Versuch. Der Server unterscheidet seine
 *  Ablehnungen bewusst nicht; die Oberfläche tut es auch nicht. */
export function passkeyLoginMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === 'network') return MESSAGES.network;
    if (error.stale) return MESSAGES.stale;
    if (error.status === 404) return 'Passkeys sind nicht eingerichtet.';
    return PASSKEY_REFUSED;
  }
  if (error instanceof Error) {
    if (error.name === 'NotAllowedError') return 'Die Anmeldung wurde abgebrochen.';
    if (error.message === 'Dieser Browser unterstützt keine Passkeys.' || error.message === 'Es wurde kein Passkey ausgewählt.') {
      return error.message;
    }
  }
  return PASSKEY_REFUSED;
}
