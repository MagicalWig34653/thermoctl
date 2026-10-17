import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildVisibleAreas, type ServerNavItem } from '../src/lib/navigation.ts';
import {
  STORAGE_KEY,
  buildNavEntries,
  groupIds,
  loadOpen,
  nextFocusIndex,
  openGroups,
  parseStored,
  saveOpen,
  toggleGroup,
} from '../src/lib/nav-menu.ts';

const item = (path: string): ServerNavItem => ({ path, label: path, section: 'main' });
const admin = ['/zones', '/schedule', '/vacation', '/statistics', '/audit', '/relay-wear', '/devices', '/controllers',
  '/settings', '/modes', '/control', '/interfaces', '/users', '/tokens', '/kiosk-tokens'].map(item);

test('Bereiche mit mehreren Seiten werden Gruppen, Übersicht bleibt Link auf die Startseite', () => {
  const entries = buildNavEntries(buildVisibleAreas(admin));
  const uebersicht = entries[0];
  assert.equal(uebersicht.kind, 'link');
  assert.equal(uebersicht.kind === 'link' && uebersicht.to, '/');
  assert.deepEqual(groupIds(entries), ['zeit', 'auswertung', 'geraete', 'heizung', 'system', 'personen']);
  const geraete = entries.find((e) => e.area.id === 'geraete')!;
  assert.ok(geraete.kind === 'group');
  assert.equal(geraete.pages[0].to, '/geraete/liste');
});

test('Ein Bereich mit nur einer sichtbaren Seite wird ein einfacher Link auf diese Seite', () => {
  const entries = buildNavEntries(buildVisibleAreas([item('/audit')]));
  const auswertung = entries.find((e) => e.area.id === 'auswertung')!;
  assert.equal(auswertung.kind, 'link');
  assert.equal(auswertung.kind === 'link' && auswertung.to, '/auswertung/ereignisse');
  assert.deepEqual(groupIds(entries), []);
});

test('Fehlende Unterseiten erscheinen nicht, Bereiche ohne Seite auch nicht', () => {
  const entries = buildNavEntries(buildVisibleAreas([item('/statistics'), item('/audit')]));
  const auswertung = entries.find((e) => e.area.id === 'auswertung')!;
  assert.ok(auswertung.kind === 'group');
  assert.deepEqual(auswertung.pages.map((p) => p.page.id), ['verlauf', 'ereignisse']);
  assert.deepEqual(entries.map((e) => e.area.id), ['uebersicht', 'auswertung']);
  assert.deepEqual(buildNavEntries(buildVisibleAreas([])).map((e) => e.area.id), ['uebersicht']);
});

test('offen: Bereich der aktuellen Seite immer, gemerkte nur wenn es die Gruppe gibt', () => {
  const groups = ['zeit', 'geraete'];
  assert.deepEqual([...openGroups(null, groups, 'geraete')], ['geraete']);
  assert.deepEqual([...openGroups(['zeit', 'system', 'xyz'], groups, 'geraete')].sort(), ['geraete', 'zeit']);
  assert.deepEqual([...openGroups(['zeit'], groups, null)], ['zeit']);
  assert.deepEqual([...openGroups(null, groups, 'uebersicht')], [], 'Einzel-Link ist keine Gruppe');
});

test('toggleGroup verändert das Original nicht', () => {
  const a = new Set(['zeit']);
  const b = toggleGroup(a, 'geraete');
  assert.deepEqual([...a], ['zeit']);
  assert.deepEqual([...b].sort(), ['geraete', 'zeit']);
  assert.deepEqual([...toggleGroup(b, 'zeit')], ['geraete']);
});

test('Merken: Rundlauf, kaputte Werte und werfender Speicher', () => {
  const mem = new Map<string, string>();
  const ok = () => ({ getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) });
  saveOpen(ok, new Set(['zeit', 'geraete']));
  assert.equal(mem.get(STORAGE_KEY), '["geraete","zeit"]');
  assert.deepEqual(loadOpen(ok), ['geraete', 'zeit']);
  assert.equal(parseStored('{kaputt'), null);
  assert.equal(parseStored('{"a":1}'), null);
  assert.deepEqual(parseStored('["a",3,null,"b"]'), ['a', 'b']);
  const boom = () => {
    throw new Error('gesperrt');
  };
  assert.equal(loadOpen(boom), null);
  assert.doesNotThrow(() => saveOpen(boom, new Set(['x'])));
  const throwing = () => ({ getItem: boom, setItem: boom });
  assert.equal(loadOpen(throwing), null);
  assert.doesNotThrow(() => saveOpen(throwing, new Set(['x'])));
});

test('Pfeiltasten: kein Umbruch, Home/End, Start ohne Fokus', () => {
  assert.equal(nextFocusIndex(0, -1, 'ArrowDown'), -1);
  assert.equal(nextFocusIndex(4, 1, 'ArrowDown'), 2);
  assert.equal(nextFocusIndex(4, 3, 'ArrowDown'), 3);
  assert.equal(nextFocusIndex(4, 0, 'ArrowUp'), 0);
  assert.equal(nextFocusIndex(4, 2, 'ArrowUp'), 1);
  assert.equal(nextFocusIndex(4, -1, 'ArrowDown'), 0);
  assert.equal(nextFocusIndex(4, -1, 'ArrowUp'), 3);
  assert.equal(nextFocusIndex(4, 2, 'Home'), 0);
  assert.equal(nextFocusIndex(4, 1, 'End'), 3);
  assert.equal(nextFocusIndex(4, 1, 'a'), 1);
});
