import assert from 'node:assert/strict';
import { test } from 'node:test';
import { roleLabel, roleOf, untilText } from '../src/lib/cluster-role.ts';
import { isSwitching, notSwitchingLine } from '../src/lib/rooms/plant.ts';
import type { Actuation } from '../src/lib/rooms/types.ts';

const live = {
  control_armed: true,
  sending_allowed: true,
  state: 'live',
} as const;

function act(extra: Partial<Actuation>): Actuation {
  return { ...live, cluster_standby: false, ...extra } as Actuation;
}

test('roleOf nimmt cluster_state, sonst das alte Feld cluster_standby', () => {
  assert.equal(roleOf({ cluster_standby: false, cluster_state: 'determining' }), 'determining');
  assert.equal(roleOf({ cluster_standby: true, cluster_state: 'standby' }), 'standby');
  assert.equal(roleOf({ cluster_standby: true }), 'standby');
  assert.equal(roleOf({ cluster_standby: false }), 'leading');
});

test('determining heißt „wird ermittelt“ und ist nie Standby oder Primär', () => {
  const a = { cluster_standby: true, cluster_state: 'determining' as const, determining_until: '2026-10-17T08:05:00' };
  const label = roleLabel(a);
  assert.match(label.value, /^Wird ermittelt \(bis ca\. \d\d:\d\d\)$/);
  assert.equal(label.tone, 'neutral');
  assert.doesNotMatch(label.value, /Standby|Primär/);
});

test('ohne Zeitangabe bleibt der Text ohne Uhrzeit', () => {
  assert.equal(roleLabel({ cluster_standby: true, cluster_state: 'determining' }).value, 'Wird ermittelt');
  assert.equal(untilText(null), null);
  assert.equal(untilText('kein Datum'), null);
});

test('naive und ausdrückliche UTC-Zeit ergeben dieselbe Uhrzeit', () => {
  assert.equal(untilText('2026-10-17T08:05:00'), untilText('2026-10-17T08:05:00Z'));
});

test('determining schaltet nie und die Meldung nennt den Grund', () => {
  const a = act({ cluster_standby: true, cluster_state: 'determining', determining_until: '2026-10-17T08:05:00Z' });
  assert.equal(isSwitching(a), false);
  const line = notSwitchingLine(a);
  assert.equal(line?.reason, 'determining');
  assert.match(line?.text ?? '', /Primärrolle wird ermittelt/);
  assert.match(line?.text ?? '', /schaltet noch nichts/);
});

test('Standby und Primär bleiben unverändert', () => {
  assert.equal(notSwitchingLine(act({ cluster_standby: true, cluster_state: 'standby' }))?.reason, 'standby');
  assert.equal(notSwitchingLine(act({ cluster_standby: false, cluster_state: 'leading' })), null);
  assert.equal(isSwitching(act({ cluster_standby: false, cluster_state: 'leading' })), true);
});

test('ältere Antwort ohne cluster_state verhält sich wie vorher', () => {
  assert.equal(notSwitchingLine(act({ cluster_standby: true }))?.reason, 'standby');
  assert.equal(isSwitching(act({ cluster_standby: false })), true);
});
