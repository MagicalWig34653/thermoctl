import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { CATALOG, clampPeriod, matchesQuery, periodsFor } from '../src/lib/dashboard/catalog.ts';
import { CONFLICT_MESSAGE, NETWORK_MESSAGE, dashboardFailure, fieldLabel } from '../src/lib/dashboard/errors.ts';
import { pointerBefore, sectionMove, tileMove } from '../src/lib/dashboard/keyboard.ts';
import { agendaItems, metricValue, tileTitle } from '../src/lib/dashboard/tiles.ts';
import { TILE_KINDS } from '../src/lib/dashboard/types.ts';
import type { Room, Rooms } from '../src/lib/rooms/types.ts';

const http = (status: number, message: string, code: string | null = null, field: string | null = null) =>
  new ApiError('http', status, message, false, code, field);

test('409 heißt „woanders geändert“, mit Feld version', () => {
  const f = dashboardFailure(http(409, 'Das Layout wurde inzwischen geändert. Bitte neu laden.', 'conflict', 'version'));
  assert.equal(f.kind, 'conflict');
  assert.equal(f.message, CONFLICT_MESSAGE);
  assert.equal(f.field, 'version');
  assert.equal(dashboardFailure(http(409, 'x', 'conflict')).field, 'version');
});

test('422 nennt das Feld; unbekannte Felder erscheinen roh', () => {
  const f = dashboardFailure(http(422, 'Dieser Raum ist nicht verfügbar.', 'validation', 'zone_id'));
  assert.equal(f.kind, 'validation');
  assert.equal(f.message, 'Raum: Dieser Raum ist nicht verfügbar.');
  assert.equal(f.field, 'zone_id');
  assert.equal(dashboardFailure(http(422, 'Bitte ein Ziel wählen.', 'validation', 'destination')).message, 'Ziel: Bitte ein Ziel wählen.');
  assert.equal(dashboardFailure(http(422, 'Diese Kachel ist nicht verfügbar.', 'validation', 'kind')).message, 'Kachelart: Diese Kachel ist nicht verfügbar.');
  assert.equal(dashboardFailure(http(422, 'Die Eingabe ist ungültig.', 'validation')).message, 'Die Eingabe ist ungültig.');
  assert.equal(dashboardFailure(http(422, 'Mist', 'validation', 'sonstiges')).message, 'sonstiges: Mist');
  assert.equal(fieldLabel(null), null);
});

test('Netzwerk, Sitzung, Berechtigung und Sonstiges', () => {
  const net = dashboardFailure(new ApiError('network', 0, 'Failed to fetch'));
  assert.equal(net.kind, 'network');
  assert.equal(net.message, NETWORK_MESSAGE);
  assert.match(net.message, /Entwurf bleibt/);
  assert.equal(dashboardFailure(new ApiError('unauthorized', 401, 'x')).kind, 'session');
  assert.equal(dashboardFailure(new ApiError('forbidden', 403, 'x', true)).kind, 'session');
  const forb = dashboardFailure(new ApiError('forbidden', 403, 'Dafür fehlt die Berechtigung.'));
  assert.deepEqual([forb.kind, forb.message], ['forbidden', 'Dafür fehlt die Berechtigung.']);
  assert.equal(dashboardFailure(http(500, 'Serverfehler')).kind, 'other');
  assert.equal(dashboardFailure(http(500, '')).message, 'Fehler 500');
  assert.equal(dashboardFailure(new Error('x')).kind, 'other');
});

test('Katalog: alle zehn Kacheltypen genau einmal', () => {
  assert.deepEqual([...CATALOG.map((e) => e.kind)].sort(), [...TILE_KINDS].sort());
  assert.equal(CATALOG.length, 10);
  for (const e of CATALOG) assert.equal(e.needsZone, ['room', 'chart', 'schedule', 'heating'].includes(e.kind), e.kind);
  assert.deepEqual(CATALOG.filter((e) => e.adminOnly).map((e) => e.kind).sort(), ['devices', 'system']);
});

test('Zeiträume: Verlauf 1/7/30, Heizzeit 7/30/90, sonst keiner', () => {
  assert.deepEqual(periodsFor('chart'), [1, 7, 30]);
  assert.deepEqual(periodsFor('heating'), [7, 30, 90]);
  assert.equal(periodsFor('room'), null);
  assert.equal(clampPeriod('room', 30), 1);
  assert.equal(clampPeriod('chart', 90), 30);
  assert.equal(clampPeriod('heating', 1), 7);
  assert.equal(clampPeriod('heating', 30), 30);
});

test('Galeriesuche findet über Name, Beschreibung und Stichworte', () => {
  const find = (q: string) => CATALOG.filter((e) => matchesQuery(e, q)).map((e) => e.kind);
  assert.equal(find('').length, 10);
  assert.deepEqual(find('verlauf'), ['chart']);
  assert.ok(find('trockenlauf').includes('system'));
  assert.deepEqual(find('xyz'), []);
});

test('Tastatur: Pfeile bewegen, mit Modifikator nicht', () => {
  const key = (k: string, m: Partial<{ altKey: boolean; ctrlKey: boolean; metaKey: boolean }> = {}) => ({ key: k, altKey: false, ctrlKey: false, metaKey: false, ...m });
  assert.equal(tileMove(key('ArrowLeft')), -1);
  assert.equal(tileMove(key('ArrowUp')), -1);
  assert.equal(tileMove(key('ArrowRight')), 1);
  assert.equal(tileMove(key('ArrowDown')), 1);
  assert.equal(tileMove(key('ArrowDown', { ctrlKey: true })), null);
  assert.equal(tileMove(key('a')), null);
  assert.equal(sectionMove(key('ArrowUp')), -1);
  assert.equal(sectionMove(key('ArrowDown')), 1);
  assert.equal(sectionMove(key('ArrowLeft')), null);
  assert.equal(sectionMove(key('ArrowUp', { altKey: true })), null);
});

test('Ziehziel: bei Zeilenkacheln zählt oben/unten, sonst links/rechts', () => {
  const rect = { left: 100, top: 100, width: 100, height: 80 };
  assert.equal(pointerBefore(rect, { width: 400 }, 120, 170), true, 'links der Mitte');
  assert.equal(pointerBefore(rect, { width: 400 }, 180, 110), false, 'rechts der Mitte');
  const wide = { left: 0, top: 100, width: 380, height: 80 };
  assert.equal(pointerBefore(wide, { width: 400 }, 350, 110), true, 'obere Hälfte');
  assert.equal(pointerBefore(wide, { width: 400 }, 10, 170), false, 'untere Hälfte');
});

function room(over: Partial<Room> & { id: number; name: string }): Room {
  return {
    operating_mode: { code: 'auto', label: 'Automatik' },
    temperature_c: '21.0',
    measured_at: null,
    measured_age_seconds: 10,
    sensor: { status_code: 'ok', status_label: 'ok', stuck: false, window_open: false, window_open_by_temperature: false, window_alarm: false },
    setpoint: { temperature_c: '21.0', reason: 'plan', mode_id: null, mode_name: null },
    override: null,
    decision: { would_heat: false, outcome_code: 'x', reason: '', decided_at: '2026-01-01T00:00:00Z', planned_setpoint_c: '21.0' },
    emergency: null,
    plan_applies: true,
    day_track: [],
    actions: { override: false, cancel_override: false, edit_setpoint_step: false, edit_mode_setpoints: false, jump_next: false, set_operating_mode: false },
    ...over,
  };
}
const rooms = (list: Room[], state: Rooms['actuation']['state'] = 'live'): Rooms => ({
  timezone: 'Europe/Berlin',
  poll_interval_seconds: 30,
  now_fraction: 10,
  actuation: { control_armed: state !== 'dry_run', sending_allowed: state === 'live', cluster_standby: false, state },
  rooms: list,
});

test('Kennzahlen kommen aus den Serverzuständen der Räume', () => {
  const data = rooms([
    room({ id: 1, name: 'A', decision: { would_heat: true, outcome_code: 'x', reason: '', decided_at: '', planned_setpoint_c: null } }),
    room({ id: 2, name: 'B', temperature_c: null }),
    room({ id: 3, name: 'C' }),
  ]);
  assert.equal(metricValue('rooms', data).value, '3');
  assert.equal(metricValue('heating', data).value, '1');
  assert.equal(metricValue('issues', data).value, '1');
  assert.match(metricValue('heating', data).note, /^Räume fordern Wärme an$/);
  assert.match(metricValue('heating', rooms(data.rooms, 'dry_run')).note, /Trockenlauf/);
  assert.equal(metricValue('issues', rooms([room({ id: 1, name: 'A' })])).note, 'Keine Auffälligkeiten');
});

test('Außentemperatur: nicht erfunden, sondern als nicht verfügbar gekennzeichnet', () => {
  const m = metricValue('outdoor', rooms([]));
  assert.equal(m.available, false);
  assert.equal(m.value, '–');
});

test('Heute relevant: Störungen vor Übersteuerungen, begrenzt', () => {
  const data = rooms([
    room({ id: 1, name: 'A', override: { id: 1, temperature_c: '23.0', starts_at: '2026-01-01T10:00:00Z', ends_at: null } }),
    room({ id: 2, name: 'B', temperature_c: null }),
  ]);
  const items = agendaItems(data);
  assert.deepEqual(items.map((i) => [i.roomId, i.tone]), [[2, 'warn'], [1, 'info']]);
  assert.match(items[1].text, /ohne Ende/);
  assert.deepEqual(agendaItems(rooms([room({ id: 1, name: 'A' })])), []);
  const many = rooms(Array.from({ length: 9 }, (_, i) => room({ id: i, name: `R${i}`, temperature_c: null })));
  assert.equal(agendaItems(many).length, 6);
});

test('tileTitle: eigener Titel oder Katalogname', () => {
  assert.equal(tileTitle({ kind: 'chart', title: '  ' }), 'Temperaturverlauf');
  assert.equal(tileTitle({ kind: 'note', title: ' Einkauf ' }), 'Einkauf');
});

test('Notizen werden nie als HTML eingesetzt', () => {
  const dir = join(import.meta.dirname, '..', 'src', 'lib', 'components', 'dashboard');
  for (const file of readdirSync(dir).filter((f) => f.endsWith('.svelte'))) {
    assert.ok(!readFileSync(join(dir, file), 'utf8').includes('{@html'), `${file} darf kein {@html} enthalten`);
  }
});
