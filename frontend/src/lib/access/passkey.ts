// WebAuthn-Hilfen für die Registrierung eines Passkeys. Ablauf wie thermoctl/web/static/passkey.js:
// Alles Binäre geht als base64url (ohne Auffüllung) hin und her.

export function base64urlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export function bytesToBase64url(buffer: ArrayBuffer | Uint8Array): string {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let raw = '';
  for (const b of bytes) raw += String.fromCharCode(b);
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

interface Descriptor {
  id: string;
  [k: string]: unknown;
}
export interface RegistrationOptions {
  challenge: string;
  user: { id: string; [k: string]: unknown };
  excludeCredentials?: Descriptor[];
  [k: string]: unknown;
}

/** Macht aus den Serverargumenten, was `navigator.credentials.create()` erwartet. */
export function prepareRegistration(args: RegistrationOptions): PublicKeyCredentialCreationOptions {
  const ready = {
    ...args,
    challenge: base64urlToBytes(args.challenge),
    user: { ...args.user, id: base64urlToBytes(args.user.id) },
  } as unknown as PublicKeyCredentialCreationOptions & { excludeCredentials?: unknown };
  if (Array.isArray(args.excludeCredentials)) {
    ready.excludeCredentials = args.excludeCredentials.map((e) => ({ ...e, id: base64urlToBytes(e.id) })) as unknown as PublicKeyCredentialDescriptor[];
  }
  return ready;
}

export function registrationBody(label: string, credential: PublicKeyCredential) {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    label,
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      attestationObject: bytesToBase64url(response.attestationObject),
    },
  };
}

export function passkeySupported(win: { PublicKeyCredential?: unknown; navigator?: { credentials?: unknown } } = window): boolean {
  return typeof win.PublicKeyCredential === 'function' && typeof win.navigator?.credentials === 'object';
}

/** Meldung für einen abgebrochenen oder fehlgeschlagenen Versuch (ohne Technik). */
export function passkeyMessage(error: unknown): string {
  if (error instanceof Error && error.name === 'NotAllowedError') return 'Der Vorgang wurde abgebrochen.';
  if (error instanceof Error && error.name === 'InvalidStateError') return 'Dieses Gerät ist bereits als Passkey hinterlegt.';
  if (error instanceof Error && error.message) return error.message;
  return 'Der Passkey konnte nicht hinterlegt werden.';
}
