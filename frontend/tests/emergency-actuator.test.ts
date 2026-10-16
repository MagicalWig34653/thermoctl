import assert from 'node:assert/strict';
import { test } from 'node:test';
import { actuatorCycle, sensorTimeoutText } from '../src/lib/operations/logic.ts';
import type { EmergencyActuator } from '../src/lib/operations/types.ts';

const base: EmergencyActuator = {
  device_id: 1, device_name: 'Ventil', kind: 'thermostat', phase: null, phase_deadline_at: null,
  on_seconds: null, off_seconds: null, cycle_source: null, handover_status_text: '', restore_status_text: '',
};

test('Aktor im Takt: Phase, Taktquelle, Frist in Anlagenzeit und Takt', () => {
  const c = actuatorCycle(
    { ...base, phase: 'ein', cycle_source: 'kennlinie', phase_deadline_at: '2026-10-16T10:30:00Z', on_seconds: 120, off_seconds: 480 },
    'Europe/Berlin',
  );
  assert.equal(c.phase, 'Ein');
  assert.equal(c.source, 'Kennlinie');
  assert.match(c.deadline ?? '', /12:30 Uhr$/);
  assert.equal(c.cycle, '120 s an / 480 s aus');
});

test('Aktor: Aus, Festtakt; ohne Takt bleibt alles leer', () => {
  const c = actuatorCycle({ ...base, phase: 'aus', cycle_source: 'festtakt', on_seconds: 60, off_seconds: null }, 'Europe/Berlin');
  assert.deepEqual([c.phase, c.source, c.deadline, c.cycle], ['Aus', 'Festtakt', null, null]);
  assert.deepEqual(actuatorCycle(base, 'Europe/Berlin'), { phase: null, source: null, deadline: null, cycle: null });
});

test('Sensor-Timeout: nur mit Wert', () => {
  assert.equal(sensorTimeoutText(900), '900 s');
  assert.equal(sensorTimeoutText(0), null);
  assert.equal(sensorTimeoutText(null), null);
});
