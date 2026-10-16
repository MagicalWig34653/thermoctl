import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildMatrix, changeCount, grantSummary, profileLabel, sameGrants, toggleGrant } from '../src/lib/access/matrix.ts';
import { base64urlToBytes, bytesToBase64url } from '../src/lib/access/passkey.ts';
import { deviceLabel, tokenState, validDays } from '../src/lib/access/format.ts';
import type { PermissionArea, ZoneRef } from '../src/lib/access/types.ts';

const areas: PermissionArea[] = [
  {
    title: 'Sehen und bedienen',
    hint: 'Alltag',
    permissions: [
      { code: 'zone.read', description: 'Räume ansehen', zone_scoped: true },
      { code: 'user.manage', description: 'Benutzer verwalten', zone_scoped: false },
    ],
  },
];
const zones: ZoneRef[] = [
  { id: 1, name: 'Bad' },
  { id: 2, name: 'Küche' },
];

test('Rechtematrix: raumbezogene Rechte haben Räume, anlagenweite nicht', () => {
  const [area] = buildMatrix(areas, zones, [{ code: 'zone.read', zone_id: 2 }, { code: 'user.manage', zone_id: null }]);
  const [read, manage] = area.rows;
  assert.equal(read.plantWide, false);
  assert.deepEqual(read.rooms.map((r) => [r.zoneName, r.granted]), [['Bad', false], ['Küche', true]]);
  assert.equal(manage.plantWide, true);
  assert.deepEqual(manage.rooms, []);
});

test('Anlagenweit erteilt ist getrennt von einzelnen Räumen', () => {
  const [area] = buildMatrix(areas, zones, [{ code: 'zone.read', zone_id: null }]);
  assert.equal(area.rows[0].plantWide, true);
  assert.equal(area.rows[0].rooms.every((r) => !r.granted), true);
});

test('Umschalten ändert genau eine Erteilung und liefert eine stabile Reihenfolge', () => {
  let g = toggleGrant([], 'zone.read', 2);
  g = toggleGrant(g, 'zone.read', null);
  g = toggleGrant(g, 'a.first', null);
  assert.deepEqual(g.map((x) => `${x.code}|${x.zone_id}`), ['a.first|null', 'zone.read|null', 'zone.read|2']);
  g = toggleGrant(g, 'zone.read', 2);
  assert.deepEqual(g.map((x) => `${x.code}|${x.zone_id}`), ['a.first|null', 'zone.read|null']);
});

test('Änderungen zählen, Gleichheit ignoriert die Reihenfolge', () => {
  const saved = [{ code: 'a', zone_id: null }, { code: 'b', zone_id: 1 }];
  assert.equal(changeCount(saved, [...saved].reverse()), 0);
  assert.equal(sameGrants(saved, [...saved].reverse()), true);
  assert.equal(changeCount(saved, [{ code: 'a', zone_id: null }, { code: 'c', zone_id: null }]), 2);
});

test('Zusammenfassung und Profilnamen', () => {
  assert.equal(grantSummary([]), 'Keine Rechte');
  assert.equal(grantSummary([{ code: 'a', zone_id: null }, { code: 'b', zone_id: 3 }, { code: 'c', zone_id: 4 }]), '1 anlagenweit, 2 für einzelne Räume');
  assert.deepEqual([profileLabel('admin'), profileLabel('tenant'), profileLabel('x')], ['Anlagensicht', 'Mietersicht', 'x']);
});

test('Hilfen: Token-Zustand, Gültigkeit, Gerät', () => {
  assert.deepEqual(tokenState('revoked'), { label: 'widerrufen', tone: 'muted' });
  assert.equal(validDays(''), null);
  assert.equal(validDays('90'), 90);
  assert.equal(deviceLabel('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit Chrome/120 Safari/537'), 'Chrome auf macOS');
  assert.equal(deviceLabel(null), 'Unbekanntes Gerät');
});

test('base64url: Rundlauf ohne Auffüllung mit - und _', () => {
  const bytes = new Uint8Array([251, 255, 254, 0, 1]);
  const text = bytesToBase64url(bytes);
  assert.equal(/[+/=]/.test(text), false);
  assert.deepEqual([...base64urlToBytes(text)], [...bytes]);
});
