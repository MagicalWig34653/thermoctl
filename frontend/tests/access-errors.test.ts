import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { NETWORK_MESSAGE, SESSION_MESSAGE, accessFailure } from '../src/lib/access/errors.ts';

const http = (status: number, message: string, code: string | null = null, field: string | null = null) =>
  new ApiError('http', status, message, false, code, field);
const forbidden = (message: string, code: string | null = null, field: string | null = null, stale = false) =>
  new ApiError('forbidden', 403, message, stale, code, field);

test('409 last_administrator wird verständlich erklärt und nicht neu gelesen', () => {
  const f = accessFailure(http(409, 'x', 'last_administrator'));
  assert.equal(f.kind, 'conflict');
  assert.match(f.message, /niemanden mehr mit Administratorrechten/);
  assert.equal(f.reload, false);
});

test('403 permission_not_held ist eine Antwort auf die Eingabe, kein Rechteverlust', () => {
  const f = accessFailure(forbidden('Ein gewähltes Recht fehlt Ihnen.', 'permission_not_held'));
  assert.equal(f.kind, 'forbidden');
  assert.match(f.message, /selbst nicht/);
  assert.equal(f.reload, false);
});

test('allgemeines 403 liest neu und sagt, dass die Berechtigung fehlt', () => {
  const f = accessFailure(forbidden('Dafür fehlt die Berechtigung.', 'forbidden'));
  assert.equal(f.message, 'Dafür fehlt dir die Berechtigung.');
  assert.equal(f.reload, true);
});

test('wrong_password nennt das Feld und liest nicht neu', () => {
  const f = accessFailure(forbidden('Das bisherige Passwort stimmt nicht.', 'wrong_password', 'current_password'));
  assert.deepEqual([f.kind, f.field, f.reload], ['forbidden', 'current_password', false]);
});

test('current_session, own_account und builtin_group behalten ihre Eingabe', () => {
  assert.match(accessFailure(http(409, 'x', 'current_session')).message, /Abmelden/);
  const own = accessFailure(http(409, 'Das eigene Passwort ändern Sie unter Konto.', 'own_account'));
  assert.equal(own.message, 'Das eigene Passwort ändern Sie unter Konto.');
  assert.equal(own.reload, false);
  assert.match(accessFailure(http(409, 'x', 'builtin_group')).message, /Eingebaute Gruppen/);
});

test('Namenskonflikte zeigen den Servertext am Feld', () => {
  const f = accessFailure(http(409, 'Der Benutzername ist vergeben.', 'username_taken', 'username'));
  assert.deepEqual([f.message, f.field, f.reload], ['Der Benutzername ist vergeben.', 'username', false]);
});

test('Sitzungsablauf (401 und veralteter CSRF-Wert) verlangt neue Anmeldung ohne Wiederholen', () => {
  const a = accessFailure(new ApiError('unauthorized', 401, 'Nicht angemeldet'));
  assert.deepEqual([a.kind, a.message, a.reload], ['session', SESSION_MESSAGE, false]);
  assert.equal(accessFailure(forbidden('x', null, null, true)).kind, 'session');
});

test('Netzwerkfehler: Ausgang unklar, Zustand neu lesen', () => {
  const f = accessFailure(new ApiError('network', 0, 'Failed to fetch'));
  assert.deepEqual([f.kind, f.message, f.reload], ['network', NETWORK_MESSAGE, true]);
  assert.match(f.message, /unklar/);
});

test('404 und 422 und Unerwartetes', () => {
  assert.deepEqual([accessFailure(http(404, 'Token nicht gefunden.', 'not_found')).kind, accessFailure(http(404, 'x', 'not_found')).reload], ['not_found', true]);
  const v = accessFailure(http(422, 'Zu kurz.', 'password_too_short', 'password'));
  assert.deepEqual([v.kind, v.field, v.reload], ['validation', 'password', false]);
  assert.equal(accessFailure(new Error('boom')).message, 'Unerwarteter Fehler.');
});

test('Der Fehlertext enthält nie eingegebene Geheimnisse', () => {
  // Die Abbildung kennt nur Code, Feld und Servertext, keine Eingabe.
  const f = accessFailure(forbidden('Das bisherige Passwort stimmt nicht.', 'wrong_password', 'current_password'));
  assert.equal(JSON.stringify(f).includes('hunter2'), false);
});
