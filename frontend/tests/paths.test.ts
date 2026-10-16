import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildVisibleAreas } from '../src/lib/navigation.ts';
import {
  appHref,
  internalTarget,
  legacyHref,
  relativePath,
  resolveRoute,
  splitTarget,
} from '../src/lib/paths.ts';

test('relativePath: Präfix und Grenze', () => {
  assert.equal(relativePath('/app', ''), '/');
  assert.equal(relativePath('/app/', ''), '/');
  assert.equal(relativePath('/app/geraete/liste', ''), '/geraete/liste');
  assert.equal(relativePath('/ing/app/zeit', '/ing'), '/zeit');
  assert.equal(relativePath('/ing/app', '/ing'), '/');
  // Kein Treffer auf ähnliche Segmente und fremde Pfade
  assert.equal(relativePath('/ing/application', '/ing'), '/');
  assert.equal(relativePath('/application/x', ''), '/');
  assert.equal(relativePath('/zones', ''), '/');
  assert.equal(relativePath('/app/zeit', '/ing'), '/');
});

test('appHref und legacyHref setzen den Präfix zur Laufzeit ein', () => {
  assert.equal(appHref('', '/'), '/app/');
  assert.equal(appHref('/ing', '/zeit/wochenplaene'), '/ing/app/zeit/wochenplaene');
  assert.equal(appHref('/ing', 'zeit'), '/ing/app/zeit');
  assert.equal(legacyHref('', '/zones'), '/zones');
  assert.equal(legacyHref('/ing', '/zones/3'), '/ing/zones/3');
});

test('splitTarget trennt Query/Hash und Schlussstriche', () => {
  assert.deepEqual(splitTarget('/zeit/'), { path: '/zeit', rest: '' });
  assert.deepEqual(splitTarget('/zeit?zone=3#x'), { path: '/zeit', rest: '?zone=3#x' });
  assert.deepEqual(splitTarget(''), { path: '/', rest: '' });
  assert.deepEqual(splitTarget('/'), { path: '/', rest: '' });
});

test('internalTarget: nur gleiche Herkunft unter {prefix}/app', () => {
  const o = 'https://h.example';
  assert.equal(internalTarget('/ing/app/zeit?x=1', '/ing', o), '/zeit?x=1');
  assert.equal(internalTarget('https://h.example/ing/app/', '/ing', o), '/');
  assert.equal(internalTarget('/ing/zones', '/ing', o), null, 'alte HTML-Seite bleibt Vollseitenladen');
  assert.equal(internalTarget('https://evil.example/ing/app/x', '/ing', o), null);
  assert.equal(internalTarget('/ing/application', '/ing', o), null);
  assert.equal(internalTarget('http://[::1', '/ing', o), null);
});

test('resolveRoute: Übersicht, Bereich, Seite, unbekannt, nicht sichtbar', () => {
  const areas = buildVisibleAreas([{ path: '/devices', label: 'Geräte', section: 'main' }]);
  assert.deepEqual(resolveRoute('/', areas), { kind: 'home' });
  assert.deepEqual(resolveRoute('/uebersicht', areas), { kind: 'home' });
  assert.deepEqual(resolveRoute('/geraete/', areas), { kind: 'area', areaId: 'geraete' });
  assert.deepEqual(resolveRoute('/geraete/liste', areas), { kind: 'page', areaId: 'geraete', pageId: 'liste' });
  assert.deepEqual(resolveRoute('/konto', areas), { kind: 'account' });
  assert.deepEqual(resolveRoute('/hilfe', areas), { kind: 'help' });
  assert.deepEqual(resolveRoute('/login', areas), { kind: 'login' });
  // bekannt, aber nicht sichtbar: Darstellung, keine Autorisierung
  assert.deepEqual(resolveRoute('/personen', areas), { kind: 'unavailable', areaId: 'personen', pageId: null });
  assert.deepEqual(resolveRoute('/geraete/karte', areas), { kind: 'unavailable', areaId: 'geraete', pageId: 'karte' });
  // unbekannt
  assert.equal(resolveRoute('/gibt-es-nicht', areas).kind, 'notfound');
  assert.equal(resolveRoute('/geraete/xyz', areas).kind, 'notfound');
  assert.equal(resolveRoute('/geraete/liste/zu/tief', areas).kind, 'notfound');
});

test('resolveRoute: Raumdetail unter /raeume/{id}', () => {
  assert.deepEqual(resolveRoute('/raeume/12', []), { kind: 'room', roomId: 12 });
  assert.equal(resolveRoute('/raeume/abc', []).kind, 'notfound');
  assert.equal(resolveRoute('/raeume/12/x', []).kind, 'notfound');
  assert.equal(resolveRoute('/raeume', []).kind, 'notfound');
});

test('isSetupPath: nur /app/setup mit Grenzprüfung; Verweise der Hauptanwendung führen nicht ohne Neuladen dorthin', async () => {
  const { isSetupPath, internalTarget } = await import('../src/lib/paths.ts');
  assert.equal(isSetupPath('/app/setup', ''), true);
  assert.equal(isSetupPath('/ing/app/setup/', '/ing'), true);
  assert.equal(isSetupPath('/app/setup2', ''), false);
  assert.equal(isSetupPath('/app/zonen', ''), false);
  assert.equal(isSetupPath('/setup', ''), false);
  assert.equal(internalTarget('http://h/app/setup', '', 'http://h'), null);
});
