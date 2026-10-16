// Testlücke der Alt-Oberfläche: der Mieter-Hinweis „Heizung läuft normal“ gibt es nicht mehr;
// seine Entsprechung ist die Kachel „issues“, und die muss einen Notbetrieb mitzählen.

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { agendaItems, metricValue } from '../src/lib/dashboard/tiles.ts';
import type { Room, Rooms } from '../src/lib/rooms/types.ts';

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

const rooms = (list: Room[]): Rooms => ({
  timezone: 'Europe/Berlin',
  poll_interval_seconds: 30,
  now_fraction: 10,
  actuation: { control_armed: true, sending_allowed: true, cluster_standby: false, state: 'live' },
  rooms: list,
});

const notbetrieb = { stage: 'ersatzquelle', headline: 'Ersatzquelle aktiv: Thermostat Flur', detail: 'Die Regelung nutzt ersatzweise ein anderes Gerät.' };

test('Ein Raum im Notbetrieb zählt als Auffälligkeit, obwohl sein Sensor in Ordnung ist', () => {
  const data = rooms([room({ id: 1, name: 'Ruhig' }), room({ id: 2, name: 'Flur', emergency: notbetrieb })]);
  const issues = metricValue('issues', data);
  assert.equal(issues.value, '1');
  assert.equal(issues.note, 'Raum mit Störung oder Fenster');
});

test('Ohne Notbetrieb und ohne Störung bleibt es bei „Keine Auffälligkeiten“', () => {
  const calm = metricValue('issues', rooms([room({ id: 1, name: 'A' }), room({ id: 2, name: 'B' })]));
  assert.equal(calm.value, '0');
  assert.equal(calm.note, 'Keine Auffälligkeiten');
});

test('Notbetrieb und gestörter Sensor im selben Raum zählen einmal, in zwei Räumen zweimal', () => {
  const stale = { status_code: 'stale', status_label: 'veraltet', stuck: false, window_open: false, window_open_by_temperature: false, window_alarm: false };
  const both = rooms([room({ id: 1, name: 'Beides', sensor: stale, emergency: notbetrieb })]);
  assert.equal(metricValue('issues', both).value, '1');
  const two = rooms([room({ id: 1, name: 'A', emergency: notbetrieb }), room({ id: 2, name: 'B', emergency: notbetrieb })]);
  assert.equal(metricValue('issues', two).value, '2');
  assert.equal(metricValue('issues', two).note, 'Räume mit Störung oder Fenster');
});

test('Der Notbetrieb steht mit seiner Überschrift unter „Heute relevant“', () => {
  const items = agendaItems(rooms([room({ id: 2, name: 'Flur', emergency: notbetrieb })]));
  assert.deepEqual(items, [{ roomId: 2, roomName: 'Flur', text: 'Ersatzquelle aktiv: Thermostat Flur', tone: 'warn' }]);
});
