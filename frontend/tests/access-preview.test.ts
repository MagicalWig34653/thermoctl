import assert from 'node:assert/strict';
import { test } from 'node:test';
import { navigationTree, permissionLine, permissionLines } from '../src/lib/access/preview.ts';

test('navigationTree: Bereiche mit mehreren Seiten werden Gruppen, einzelne Seiten Einträge', () => {
  const tree = navigationTree([
    { path: '/zones', label: 'Zonen', section: 'main' },
    { path: '/devices', label: 'Geräte', section: 'main' },
    { path: '/controllers', label: 'Bediengeräte', section: 'system' },
    { path: '/users', label: 'Benutzer', section: 'access' },
  ]);
  const geraete = tree.find((n) => n.label === 'Geräte');
  assert.ok(geraete, 'Bereich Geräte fehlt');
  assert.deepEqual(geraete.children, ['Geräte & Zuordnung', 'Bediengeräte', 'Anlagenbild']);
  const personen = tree.find((n) => n.label === 'Personen & Zugänge');
  assert.deepEqual(personen, { label: 'Personen & Zugänge', children: [] });
  assert.equal(tree[0].label, 'Übersicht');
});

test('navigationTree: ohne Einträge bleibt nur die Übersicht', () => {
  assert.deepEqual(navigationTree([]), [{ label: 'Übersicht', children: [] }]);
});

test('navigationTree: ein Server-Pfad ohne Abbildung erscheint nicht', () => {
  assert.deepEqual(navigationTree([{ path: '/gibt-es-nicht', label: 'X', section: 'main' }]), [{ label: 'Übersicht', children: [] }]);
});

test('permissionLine: anlagenweit und auf einen Raum beschränkt', () => {
  assert.equal(permissionLine({ code: 'zone.read', description: 'Räume ansehen', zone_id: null, zone_name: null }), 'Räume ansehen');
  assert.equal(permissionLine({ code: 'zone.read', description: 'Räume ansehen', zone_id: 3, zone_name: 'Bad' }), 'Räume ansehen (Bad)');
  assert.equal(permissionLine({ code: 'zone.read', description: 'Räume ansehen', zone_id: 9, zone_name: null }), 'Räume ansehen (unbekannter Raum)');
});

test('permissionLines: Doppelte entfallen, Reihenfolge bleibt', () => {
  const a = { code: 'a', description: 'Eins', zone_id: null, zone_name: null };
  const b = { code: 'b', description: 'Zwei', zone_id: null, zone_name: null };
  assert.deepEqual(permissionLines([a, b, a]), ['Eins', 'Zwei']);
});
