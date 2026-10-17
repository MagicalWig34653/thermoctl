import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { deviceFailure, failureText } from '../src/lib/devices/errors.ts';
import { drawnLines, modeLabel, nodeDeviceId, portIndex, rejectionReason, undrawnConnections, valvesOfDevice } from '../src/lib/devices/map-logic.ts';
import { EMPTY_PREFS, STORAGE_KEY, loadPrefs, parsePrefs, savePrefs } from '../src/lib/devices/storage.ts';
import { map, sensor } from './devices-fixture.ts';

test('Ablehnung kommt aus den Tabellen des Servers, nichts wird errechnet', () => {
  const p = portIndex(map);
  assert.equal(rejectionReason(map, p.get('d1.hum')!, p.get('z7.source')!), 'Falsche Wertart.');
  assert.equal(rejectionReason(map, p.get('d1.temp')!, p.get('z7.source')!), null);
  assert.equal(rejectionReason(map, p.get('d1.temp')!, p.get('d1.battery')!), map.technical_reason);
  assert.equal(rejectionReason(map, p.get('d1.temp')!, p.get('d1.hum')!), map.same_side.out);
  assert.match(rejectionReason(map, p.get('d1.temp')!, p.get('d1.temp')!)!, /mit sich selbst/);
});

test('gezeichnet werden nur Linien mit beiden sichtbaren Anschlüssen', () => {
  const p = portIndex(map);
  const all = new Set(p.keys());
  assert.equal(drawnLines(map, all, p).length, 1);
  assert.equal(drawnLines(map, new Set(['d1.temp']), p).length, 0);
  assert.deepEqual(undrawnConnections(map).map((c) => c.id), ['c2']);
});

test('Betriebsart und Ventile stammen aus den Serverdaten', () => {
  assert.equal(modeLabel(map, 'controlled'), 'Geregelt');
  assert.equal(modeLabel(map, 'unbekannt'), 'unbekannt');
  assert.equal(modeLabel(map, null), '');
  assert.equal(valvesOfDevice(map, 2).length, 1);
  assert.equal(valvesOfDevice(map, null).length, 0);
  assert.equal(nodeDeviceId(sensor), 1);
});

test('Fehlerabbildung: Konflikt, Sitzung, Netz, Berechtigung, Eingabe', () => {
  const conflict = deviceFailure(new ApiError('http', 409, 'Der Aktor gehört schon zu Zone X.', false, 'actuator_in_other_zone'));
  assert.equal(conflict.kind, 'conflict');
  assert.equal(conflict.isConflict, true);
  assert.equal(conflict.reload, true);
  assert.equal(conflict.code, 'actuator_in_other_zone');
  assert.equal(failureText(conflict), 'Der Aktor gehört schon zu Zone X.');
  const session = deviceFailure(new ApiError('unauthorized', 401, 'x'));
  assert.equal(session.kind, 'session');
  assert.equal(session.reload, false);
  const net = deviceFailure(new ApiError('network', 0, 'offline'));
  assert.equal(net.kind, 'network');
  assert.equal(net.reload, true);
  assert.match(failureText(net), /neu gelesen/);
  assert.equal(deviceFailure(new ApiError('forbidden', 403, '')).message, 'Dafür fehlt die Berechtigung.');
  const bad = deviceFailure(new ApiError('http', 422, 'Bitte eine Zahl angeben.', false, 'x', 'step_k'));
  assert.equal(bad.kind, 'validation');
  assert.equal(bad.field, 'step_k');
  assert.equal(bad.reload, false);
  assert.equal(deviceFailure(new Error('boom')).kind, 'server');
});

test('Speicher: kaputte, fehlende und gesperrte Werte ergeben leere Voreinstellung', () => {
  assert.deepEqual(parsePrefs(null), EMPTY_PREFS);
  assert.deepEqual(parsePrefs('{kaputt'), EMPTY_PREFS);
  const ok = parsePrefs(JSON.stringify({
    pos: { a: { x: 1, y: 2 }, b: { x: 'x', y: 1 }, c: null },
    view: { x: 1, y: 2, k: 0.5 }, collapsed: ['a', 3], technical: true,
  }));
  // Ältere Stände ohne Merkmal gelten mit Inhalt als noch nicht an den Server gesendet.
  assert.deepEqual(ok, { pos: { a: { x: 1, y: 2 } }, view: { x: 1, y: 2, k: 0.5 }, collapsed: ['a'], technical: true, dirty: true });
  assert.equal(parsePrefs(JSON.stringify({ technical: true })).dirty, false);
  assert.equal(parsePrefs(JSON.stringify({ pos: { a: { x: 1, y: 2 } }, dirty: false })).dirty, false);
  assert.equal(parsePrefs(JSON.stringify({ view: { x: 1, y: 2, k: 0 } })).view, null);
  const throwing = () => ({ getItem: () => { throw new Error('gesperrt'); }, setItem: () => { throw new Error('voll'); } });
  assert.deepEqual(loadPrefs(throwing), EMPTY_PREFS);
  assert.doesNotThrow(() => savePrefs(EMPTY_PREFS, throwing));
  assert.deepEqual(loadPrefs(() => null), EMPTY_PREFS);
  const mem: Record<string, string> = {};
  const store = () => ({ getItem: (k: string) => mem[k] ?? null, setItem: (k: string, v: string) => void (mem[k] = v) });
  savePrefs({ ...EMPTY_PREFS, technical: true }, store);
  assert.ok(mem[STORAGE_KEY]);
  assert.equal(loadPrefs(store).technical, true);
});
