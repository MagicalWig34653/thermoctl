import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildVisibleAreas } from '../src/lib/navigation';
import { resolveRoute } from '../src/lib/paths';
import {
  choiceFromSearch,
  entryFromHash,
  periodFromSearch,
  termFromSearch,
  zoneFromSearch,
} from '../src/lib/preselect';
import { roomJumps } from '../src/lib/rooms/jumps';

const zones = [{ id: 3 }, { id: 12 }];

test('zone: nur ein sichtbarer Raum aus einer kurzen Ziffernfolge gilt', () => {
  assert.equal(zoneFromSearch('?zone=12', zones), 12);
  assert.equal(zoneFromSearch('?a=1&zone=3', zones), 3);
  assert.equal(zoneFromSearch('?zone=03', zones), 3);
});

test('zone: unbekannte, fremde und gefälschte Werte werden verworfen', () => {
  for (const search of [
    '', '?zone=', '?zone=99', '?zone=0', '?zone=-3', '?zone=3.0', '?zone=abc', '?zone=3%20',
    '?zone=3,12', '?zone=1234567890', '?zone=%C2%B2', '?zone=%D9%A3', '?zone=3%26zone%3D12',
    '?zone=https://evil.example', '?zone=__proto__', '?zone[]=3',
  ]) {
    assert.equal(zoneFromSearch(search, zones), search === '?zone=03' ? 3 : null, search);
  }
  assert.equal(zoneFromSearch('?zone=3', []), null);
});

test('period: nur bekannte Zeiträume', () => {
  assert.equal(periodFromSearch('?period=24h'), '24h');
  assert.equal(periodFromSearch('?period=30d'), '30d');
  for (const s of ['', '?period=', '?period=90d', '?period=7', '?period=7D', '?period=<script>']) {
    assert.equal(periodFromSearch(s), null, s);
  }
});

test('choice: nur Werte aus der Liste', () => {
  const tabs = ['karte', 'liste', 'datenfluss'] as const;
  assert.equal(choiceFromSearch('?tab=datenfluss', 'tab', tabs), 'datenfluss');
  assert.equal(choiceFromSearch('?tab=Datenfluss', 'tab', tabs), null);
  assert.equal(choiceFromSearch('?tab=constructor', 'tab', tabs), null);
  assert.equal(choiceFromSearch('', 'tab', tabs), null);
});

test('suche: gekürzt und ohne Steuerzeichen', () => {
  assert.equal(termFromSearch('?suche=Sollwert'), 'Sollwert');
  assert.equal(termFromSearch('?suche=%0d%0aA%00B'), 'A B');
  assert.equal(termFromSearch(`?suche=${'x'.repeat(200)}`).length, 80);
  assert.equal(termFromSearch(''), '');
});

test('Fragment: nur ein vorhandener Begriff', () => {
  const ids = ['sollwert', 'zeitplan'];
  assert.equal(entryFromHash('#sollwert', ids), 'sollwert');
  assert.equal(entryFromHash('zeitplan', ids), 'zeitplan');
  for (const h of ['', '#', '#unbekannt', '#%E0%A4%A', '#sollwert%20', '#term-sollwert', '#<img>']) {
    assert.equal(entryFromHash(h, ids), null, h);
  }
});

test('Sprunglinks nur zu sichtbaren Seiten, mit Raumvorwahl', () => {
  const admin = buildVisibleAreas([
    { path: '/zones', label: 'Zonen', section: 'main' },
    { path: '/devices', label: 'Geräte', section: 'main' },
    { path: '/devices/map', label: 'Karte', section: 'main' },
    { path: '/statistics', label: 'Verlauf', section: 'analysis' },
  ]);
  assert.deepEqual(
    roomJumps(admin, 7).map((j) => [j.label, j.to]),
    [
      ['Zeitplan', '/zeit/wochenplaene?zone=7'],
      ['Geräte dieses Raums', '/geraete/liste?zone=7'],
      ['Datenfluss', '/geraete/karte?zone=7&tab=datenfluss'],
      ['Verlauf', '/auswertung/verlauf?zone=7'],
    ],
  );
  const tenant = buildVisibleAreas([{ path: '/schedule', label: 'Zeitplan', section: 'tenant' }]);
  assert.deepEqual(roomJumps(tenant, 7).map((j) => j.label), ['Zeitplan']);
  assert.deepEqual(roomJumps(buildVisibleAreas([]), 7), []);
  assert.deepEqual(roomJumps(admin, 0), []);
  assert.deepEqual(roomJumps(admin, 1.5), []);
});

test('jeder Sprunglink führt auf eine auflösbare Seite', () => {
  const admin = buildVisibleAreas([
    { path: '/zones', label: 'Zonen', section: 'main' },
    { path: '/devices', label: 'Geräte', section: 'main' },
    { path: '/devices/map', label: 'Karte', section: 'main' },
    { path: '/statistics', label: 'Verlauf', section: 'analysis' },
  ]);
  for (const jump of roomJumps(admin, 7)) {
    assert.equal(resolveRoute(jump.to, admin).kind, 'page', jump.to);
  }
});
