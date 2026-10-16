import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { MESSAGES, loginMessage } from '../src/lib/session.svelte.ts';

test('loginMessage: Servertext bei 401/429, sonst neutrale Meldung ohne Rückschluss auf Benutzer', () => {
  assert.equal(loginMessage(new ApiError('unauthorized', 401, 'Anmeldung fehlgeschlagen.')), 'Anmeldung fehlgeschlagen.');
  assert.equal(loginMessage(new ApiError('http', 429, 'Zu viele Versuche.')), 'Zu viele Versuche.');
  assert.equal(loginMessage(new ApiError('network', 0, 'x')), MESSAGES.network);
  assert.equal(loginMessage(new ApiError('forbidden', 403, 'x', true)), MESSAGES.stale);
  assert.equal(loginMessage(new ApiError('http', 500, 'Interner Fehler: Benutzer admin')), MESSAGES.loginFailed);
  assert.equal(loginMessage(new Error('boom')), MESSAGES.loginFailed);
});
