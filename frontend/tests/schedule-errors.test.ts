import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { CONFLICT_MESSAGE, NETWORK_MESSAGE, STALE_MESSAGE, scheduleFailure } from '../src/lib/schedule/errors.ts';

const http = (status: number, message: string, code: string | null = null, field: string | null = null) =>
  new ApiError('http', status, message, false, code, field);

test('Versionskonflikt sperrt statt neu zu laden und nennt die Sitzung', () => {
  const f = scheduleFailure(http(409, 'Der Zeitplan wurde inzwischen geändert.', 'version_conflict', 'version'));
  assert.equal(f.kind, 'conflict');
  assert.equal(f.message, CONFLICT_MESSAGE);
  assert.equal(f.reload, false);
  assert.match(f.message, /anderen Sitzung/);
});

test('Überschneidung, veraltete Rücknahme und Eingabefehler laden den Serverstand neu', () => {
  const overlap = scheduleFailure(http(409, 'Dort liegt schon ein Schaltpunkt.', 'overlap', 'time_of_day'));
  assert.deepEqual([overlap.kind, overlap.reload, overlap.field], ['overlap', true, 'time_of_day']);
  assert.equal(overlap.message, 'Dort liegt schon ein Schaltpunkt.');
  const stale = scheduleFailure(http(409, 'x', 'undo_stale'));
  assert.deepEqual([stale.kind, stale.message, stale.reload], ['stale', STALE_MESSAGE, true]);
  const invalid = scheduleFailure(http(422, 'Ungültige Zeit.', 'validation', 'end_minute'));
  assert.deepEqual([invalid.kind, invalid.message, invalid.field], ['validation', 'Ungültige Zeit.', 'end_minute']);
});

test('403, 404, Netzwerk und Sitzung', () => {
  assert.equal(scheduleFailure(new ApiError('forbidden', 403, 'Dafür fehlt die Berechtigung.', false, 'forbidden')).kind, 'forbidden');
  assert.equal(scheduleFailure(http(404, 'Raum nicht gefunden.', 'not_found')).kind, 'missing');
  const net = scheduleFailure(new ApiError('network', 0, 'Failed to fetch'));
  assert.deepEqual([net.kind, net.message, net.reload], ['network', NETWORK_MESSAGE, true]);
  const session = scheduleFailure(new ApiError('unauthorized', 401, 'Bitte anmelden.'));
  assert.deepEqual([session.kind, session.reload], ['session', false]);
  assert.equal(scheduleFailure(new ApiError('forbidden', 403, 'Sitzung ungültig', true)).kind, 'session');
});

test('Alles andere bleibt eine allgemeine Meldung', () => {
  assert.equal(scheduleFailure(new Error('boom')).kind, 'other');
  const other = scheduleFailure(http(500, ''));
  assert.equal(other.message, 'Fehler 500');
  assert.equal(scheduleFailure(http(409, 'Sonstiger Konflikt', 'something')).kind, 'other');
});
