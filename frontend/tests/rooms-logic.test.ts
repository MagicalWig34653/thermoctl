import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import {
  actuationHeadline,
  actuationRows,
  filterRooms,
  isTemperatureField,
  nowPercent,
  formatAge,
  formatInstant,
  formatMinute,
  plannedDiffers,
  roomActionsUi,
  roomStatus,
  segmentBox,
  shouldReload,
  toFailure,
} from '../src/lib/rooms/logic.ts';
import type { Room } from '../src/lib/rooms/types.ts';

function room(patch: Partial<Room> = {}): Room {
  return {
    id: 1,
    name: 'Wohnzimmer',
    operating_mode: { code: 'auto', label: 'Automatik' },
    temperature_c: '20.5',
    measured_at: '2026-10-16T08:00:00+00:00',
    measured_age_seconds: 120,
    sensor: {
      status_code: 'ok',
      status_label: 'In Ordnung',
      stuck: false,
      window_open: false,
      window_open_by_temperature: false,
      window_alarm: false,
    },
    setpoint: { temperature_c: '21.0', reason: 'Plan', mode_id: 3, mode_name: 'Tag' },
    override: null,
    decision: { would_heat: true, outcome_code: 'heat', reason: 'zu kalt', decided_at: '2026-10-16T08:00:00+00:00', planned_setpoint_c: '21.0' },
    emergency: null,
    plan_applies: true,
    day_track: [],
    actions: { override: true, cancel_override: true, edit_setpoint_step: true, edit_mode_setpoints: true, jump_next: true, set_operating_mode: true },
    ...patch,
  };
}

test('roomStatus: Reihenfolge Sensor, Fenster, Aus, Heizanforderung', () => {
  assert.equal(roomStatus(room()).kind, 'heating');
  assert.equal(roomStatus(room({ decision: { ...room().decision!, would_heat: false } })).kind, 'idle');
  assert.equal(roomStatus(room({ temperature_c: null })).kind, 'sensor');
  const broken = room({ sensor: { ...room().sensor, status_code: 'timeout', status_label: 'Zeitüberschreitung' } });
  assert.deepEqual(roomStatus(broken), { kind: 'sensor', label: 'Zeitüberschreitung', attention: true });
  assert.equal(roomStatus(room({ sensor: { ...room().sensor, window_open: true } })).kind, 'window');
  assert.equal(roomStatus(room({ operating_mode: { code: 'off', label: 'Aus' } })).kind, 'off');
  assert.equal(roomStatus(room({ decision: null })).kind, 'unknown');
});

test('plannedDiffers vergleicht Decimal-Strings exakt', () => {
  assert.equal(plannedDiffers(room()), false);
  assert.equal(plannedDiffers(room({ setpoint: { ...room().setpoint, temperature_c: '21.00' } })), false);
  assert.equal(plannedDiffers(room({ setpoint: { ...room().setpoint, temperature_c: '23.5' } })), true);
  assert.equal(plannedDiffers(room({ decision: null })), false);
});

test('filterRooms: Suche und Filter', () => {
  const rooms = [
    room({ id: 1, name: 'Wohnzimmer' }),
    room({ id: 2, name: 'Küche', decision: { ...room().decision!, would_heat: false }, override: { id: 9, temperature_c: '23', starts_at: 'x', ends_at: null } }),
    room({ id: 3, name: 'Bad', temperature_c: null }),
  ];
  assert.deepEqual(filterRooms(rooms, 'KÜ', 'all').map((r) => r.id), [2]);
  assert.deepEqual(filterRooms(rooms, '', 'heating').map((r) => r.id), [1]);
  assert.deepEqual(filterRooms(rooms, '', 'override').map((r) => r.id), [2]);
  assert.deepEqual(filterRooms(rooms, '', 'attention').map((r) => r.id), [3]);
  assert.deepEqual(filterRooms(rooms, 'zzz', 'all'), []);
});

test('Anlagenstatus: drei getrennte Zustände', () => {
  const armedNotSending = { control_armed: true, sending_allowed: false, cluster_standby: false, state: 'armed_restart_missing' as const };
  const rows = actuationRows(armedNotSending);
  assert.deepEqual(rows.map((r) => [r.id, r.tone]), [['armed', 'ok'], ['sending', 'warn'], ['role', 'ok']]);
  assert.match(actuationHeadline(armedNotSending).text, /Neustart fehlt/);
  const dry = { control_armed: false, sending_allowed: false, cluster_standby: true, state: 'dry_run' as const };
  assert.match(actuationHeadline(dry).text, /Trockenlauf/);
  assert.equal(actuationRows(dry)[2].tone, 'warn');
  assert.equal(actuationHeadline({ control_armed: true, sending_allowed: true, cluster_standby: false, state: 'live' }).tone, 'ok');
});

test('Aktionsflags: fehlendes Flag blendet aus', () => {
  const none = { override: false, cancel_override: false, edit_setpoint_step: false, edit_mode_setpoints: false, jump_next: false, set_operating_mode: false };
  const ui = roomActionsUi(room({ actions: none }), true);
  for (const action of Object.values(ui)) assert.equal(action.show, false);
});

test('Aktionsflags: Zustand begründet Deaktivierung, ohne etwas freizugeben', () => {
  const free = roomActionsUi(room(), true);
  assert.equal(free.step.disabled, false);
  assert.equal(free.createOverride.show && !free.createOverride.disabled, true);
  assert.equal(free.cancelOverride.show, false); // nichts zu beenden

  const withOverride = roomActionsUi(room({ override: { id: 1, temperature_c: '23', starts_at: 'x', ends_at: null } }), true);
  assert.equal(withOverride.step.disabled, true);
  assert.match(withOverride.step.reason!, /Übersteuerung/);
  assert.equal(withOverride.createOverride.disabled, true);
  assert.equal(withOverride.cancelOverride.show, true);

  const noCancelRight = roomActionsUi(
    room({ override: { id: 1, temperature_c: '23', starts_at: 'x', ends_at: null }, actions: { ...room().actions, cancel_override: false } }),
    true,
  );
  assert.equal(noCancelRight.cancelOverride.show, false);

  assert.equal(roomActionsUi(room(), false).jumpNext.disabled, true);
  assert.equal(roomActionsUi(room({ operating_mode: { code: 'off', label: 'Aus' } }), true).step.disabled, true);
  assert.equal(roomActionsUi(room({ setpoint: { ...room().setpoint, mode_id: null } }), true).step.disabled, true);
});

test('toFailure bildet jeden Fehlerweg auf eine Art ab', () => {
  const http = (status: number, msg: string, code: string | null = null, field: string | null = null) =>
    toFailure(new ApiError('http', status, msg, false, code, field));
  assert.equal(http(422, 'Ungültig', 'validation', 'temperature_c').kind, 'validation');
  assert.equal(http(422, 'Ungültig', 'validation', 'temperature_c').field, 'temperature_c');
  const conflict = http(409, 'Läuft schon', 'override_running');
  assert.deepEqual([conflict.kind, conflict.code, conflict.message], ['conflict', 'override_running', 'Läuft schon']);
  assert.equal(http(404, 'weg', 'not_found').kind, 'not_found');
  assert.equal(http(503, 'Einrichtung', 'setup_incomplete').kind, 'server');
  assert.equal(toFailure(new ApiError('forbidden', 403, 'Dafür fehlt die Berechtigung.', false, 'forbidden')).kind, 'forbidden');
  assert.equal(toFailure(new ApiError('forbidden', 403, 'x', true)).kind, 'session');
  assert.equal(toFailure(new ApiError('unauthorized', 401, 'x')).kind, 'session');
  const net = toFailure(new ApiError('network', 0, 'fetch failed'));
  assert.equal(net.kind, 'network');
  assert.match(net.message, /unklar/); // kein Hinweis „nicht gespeichert“, solange das ungewiss ist
  assert.equal(toFailure(new Error('boom')).kind, 'server');
});

test('shouldReload nur bei geändertem Serverzustand', () => {
  assert.equal(shouldReload({ kind: 'conflict', message: '', field: null, code: null }), true);
  assert.equal(shouldReload({ kind: 'validation', message: '', field: null, code: null }), false);
  assert.equal(shouldReload({ kind: 'network', message: '', field: null, code: null }), false);
});

test('Zeiten: Zeitzone der Anlage, nicht die des Rechners', () => {
  const now = new Date('2026-10-16T10:00:00Z');
  // 08:30 UTC sind in Berlin (Sommerzeit bis 25.10.) 10:30
  assert.equal(formatInstant('2026-10-16T08:30:00+00:00', 'Europe/Berlin', now), '10:30 Uhr');
  assert.equal(formatInstant('2026-10-16T08:30:00+00:00', 'America/New_York', now), '04:30 Uhr');
  assert.match(formatInstant('2026-10-17T08:30:00+00:00', 'Europe/Berlin', now), /10:30 Uhr$/);
  assert.match(formatInstant('2026-10-17T08:30:00+00:00', 'Europe/Berlin', now), /17\.10\./);
  // Tageswechsel nach Anlagenzeit: 22:30 UTC sind in Berlin schon der nächste Tag
  assert.match(formatInstant('2026-10-16T22:30:00+00:00', 'Europe/Berlin', now), /17\.10\./);
  assert.equal(formatInstant(null, 'Europe/Berlin'), '—');
  assert.equal(formatInstant('kein Datum', 'Europe/Berlin'), '—');
  assert.equal(formatInstant('2026-10-16T08:30:00+00:00', 'Gibt/Es_Nicht', now).endsWith('Uhr'), true);
});

test('formatAge, formatMinute, segmentBox', () => {
  assert.equal(formatAge(null), 'noch kein Messwert');
  assert.equal(formatAge(30), 'gerade eben');
  assert.equal(formatAge(300), 'vor 5 Min.');
  assert.equal(formatAge(7200), 'vor 2 Std.');
  assert.equal(formatAge(3 * 86400), 'vor 3 Tagen');
  assert.equal(formatMinute(390), '06:30');
  assert.equal(formatMinute(1440), '24:00');
  assert.equal(formatMinute(-5), '00:00');
  assert.deepEqual(segmentBox({ start_minute: 360, end_minute: 720, mode_name: 'Tag', temperature_c: null, warmth: 0.5 }), { left: 25, width: 25 });
  assert.deepEqual(segmentBox({ start_minute: 1400, end_minute: 2000, mode_name: 'x', temperature_c: null, warmth: 0 }).width, (40 / 1440) * 100);
});

test('isTemperatureField kennt beide Schreibweisen des Servers', () => {
  assert.equal(isTemperatureField('temperature_c'), true);
  assert.equal(isTemperatureField('temperatur'), true);
  assert.equal(isTemperatureField('end'), false);
  assert.equal(isTemperatureField(null), false);
});

test('nowPercent: der Server liefert Prozent, nicht einen Bruchteil', () => {
  assert.equal(nowPercent(29.3), 29.3);
  assert.equal(nowPercent(-1), 0);
  assert.equal(nowPercent(140), 100);
  assert.equal(nowPercent(Number.NaN), 0);
});
