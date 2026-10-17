import assert from 'node:assert/strict';
import test from 'node:test';
import { effectiveState, emergencyRooms, emptyState, homeLine, isSwitching, notSwitchingLine, plantChips, restartRequired } from '../src/lib/rooms/plant.ts';
import type { Actuation, Plant, Room } from '../src/lib/rooms/types.ts';

const act = (over: Partial<Actuation> = {}): Actuation => ({ control_armed: false, sending_allowed: false, cluster_standby: false, state: 'dry_run', ...over });
const plant = (over: Partial<Plant> = {}): Plant => ({
  bridge_reachable: true,
  silent_sensor_rooms: [],
  stuck_sensor_rooms: [],
  window_alarm_rooms: [],
  vacation: null,
  may_create_zone: false,
  ...over,
});
const fmt = (iso: string) => iso.slice(0, 10);
const ref = (id: number, name: string) => ({ id, name });

test('Trockenlauf wird nie als schaltend dargestellt', () => {
  const dry = act();
  assert.equal(effectiveState(dry), 'dry_run');
  assert.equal(isSwitching(dry), false);
  assert.equal(notSwitchingLine(dry)?.reason, 'dry_run');
  // Auch bei widersprüchlichen Angaben („live“, aber nicht freigegeben, oder MQTT frei im Trockenlauf).
  assert.equal(isSwitching(act({ state: 'live', sending_allowed: true })), false);
  assert.equal(notSwitchingLine(act({ state: 'live', sending_allowed: true }))?.reason, 'dry_run');
  assert.equal(notSwitchingLine(act({ sending_allowed: true }))?.reason, 'dry_run');
});

test('scharf: Neustart fehlt vs. freigegeben', () => {
  const pending = act({ control_armed: true, state: 'armed_restart_missing' });
  assert.equal(restartRequired(pending), true);
  assert.equal(isSwitching(pending), false);
  assert.equal(notSwitchingLine(pending)?.reason, 'restart');
  assert.match(notSwitchingLine(pending)!.text, /Neustart fehlt/);
  const live = act({ control_armed: true, sending_allowed: true, state: 'live' });
  assert.equal(restartRequired(live), false);
  assert.equal(isSwitching(live), true);
  // Schaltet die Anlage, gibt es keine Meldung.
  assert.equal(notSwitchingLine(live), null);
  // „live“ nach Server, aber Riegel zu: vorsichtiger Wert, nicht „schaltet“.
  assert.equal(effectiveState(act({ control_armed: true, state: 'live' })), 'armed_restart_missing');
  assert.equal(notSwitchingLine(act({ control_armed: true, state: 'live' }))?.reason, 'restart');
});

test('Standby (freigegeben, aber nicht primär) schaltet nie', () => {
  const standby = act({ control_armed: true, sending_allowed: true, state: 'live', cluster_standby: true });
  assert.equal(isSwitching(standby), false);
  assert.equal(notSwitchingLine(standby)?.reason, 'standby');
  assert.match(notSwitchingLine(standby)!.text, /Standby/);
});

test('Sammelchips: nur was anliegt, Brücke nur bei false (null ist unbekannt)', () => {
  assert.deepEqual(plantChips(plant(), fmt), []);
  assert.deepEqual(plantChips(plant({ bridge_reachable: null }), fmt), []);
  assert.deepEqual(plantChips(plant({ bridge_reachable: false }), fmt).map((c) => c.text), ['Brücke nicht erreichbar']);
  const chips = plantChips(
    plant({
      silent_sensor_rooms: [ref(1, 'Bad'), ref(2, 'Flur')],
      stuck_sensor_rooms: [ref(3, 'Küche')],
      window_alarm_rooms: [ref(4, 'Büro')],
    }),
    fmt,
  );
  assert.deepEqual(chips.map((c) => c.id), ['silent', 'stuck', 'window']);
  assert.equal(chips[0].text, 'Kein Messwert: Bad, Flur');
  assert.match(chips[1].text, /Regelung läuft normal weiter: Küche/);
  assert.equal(chips[2].text, 'Fenster vergessen: Büro');
});

test('Urlaub: laufend und geplant getrennt beschriftet', () => {
  const v = { starts_at: '2099-01-01T00:00:00+00:00', ends_at: '2099-01-09T00:00:00+00:00', setback_temperature_c: '15.0' };
  const planned = plantChips(plant({ vacation: { ...v, running: false } }), fmt)[0];
  assert.equal(planned.text, 'Urlaub geplant');
  assert.equal(planned.tone, 'quiet');
  const running = plantChips(plant({ vacation: { ...v, running: true } }), fmt)[0];
  assert.equal(running.text, 'Urlaub läuft');
  assert.equal(running.title, '2099-01-01 – 2099-01-09');
});

test('Leerzustand: Knopf nur mit Recht', () => {
  assert.deepEqual(emptyState([], plant({ may_create_zone: false })), { show: true, canCreate: false });
  assert.deepEqual(emptyState([], plant({ may_create_zone: true })), { show: true, canCreate: true });
  assert.equal(emptyState([{ id: 1 } as Room], plant({ may_create_zone: true })).show, false);
});

test('Notbetrieb: nur Räume mit Banner', () => {
  const rooms = [
    { id: 1, name: 'A', emergency: null },
    { id: 2, name: 'B', emergency: { stage: 'x', headline: 'Notbetrieb: B', detail: 'weil' } },
  ] as unknown as Room[];
  assert.deepEqual(emergencyRooms(rooms).map((e) => [e.room.id, e.headline, e.detail]), [[2, 'Notbetrieb: B', 'weil']]);
});

test('Mieterzeile: Beruhigung nur wenn nichts anliegt', () => {
  assert.deepEqual(homeLine({ calm: true, notice: null }), { calm: true, title: 'Heizung läuft normal', text: 'Alle Räume werden wie geplant geregelt.' });
  const win = homeLine({ calm: false, notice: { kind: 'window', zone_name: 'Bad', stage_label: null, detail: null, measured_age_seconds: null } });
  assert.equal(win.calm, false);
  assert.equal(win.title, 'Bad: Fenster steht offen');
  // Widerspruch (calm, aber Hinweis): nie beruhigen.
  assert.equal(homeLine({ calm: true, notice: { kind: 'window', zone_name: 'Bad', stage_label: null, detail: null, measured_age_seconds: null } }).calm, false);
  // calm=false ohne Hinweis: ebenfalls keine Beruhigung.
  assert.equal(homeLine({ calm: false, notice: null }).calm, false);
  const notb = homeLine({ calm: false, notice: { kind: 'notbetrieb', zone_name: 'Bad', stage_label: 'Ersatzquelle', detail: 'Details', measured_age_seconds: null } });
  assert.deepEqual([notb.title, notb.text], ['Bad – Ersatzquelle', 'Details']);
  const sensor = homeLine({ calm: false, notice: { kind: 'sensor', zone_name: 'Bad', stage_label: null, detail: null, measured_age_seconds: 7200 } });
  assert.match(sensor.title, /Messwert möglicherweise nicht aktuell/);
  assert.match(sensor.text, /vor 2 Std\./);
  assert.match(homeLine({ calm: false, notice: { kind: 'sensor', zone_name: 'Bad', stage_label: null, detail: null, measured_age_seconds: null } }).text, /noch keine Temperatur/);
});
