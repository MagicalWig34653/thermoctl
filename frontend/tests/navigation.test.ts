import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ACCOUNT_PATHS,
  AREAS,
  buildVisibleAreas,
  searchNavigation,
  type ServerNavItem,
} from '../src/lib/navigation.ts';

// Die Quelle der Wahrheit für die Pfade ist die Python-Datei; der Test liest sie direkt,
// damit ein neuer Server-Eintrag ohne Abbildung hier auffällt.
const PY = new URL('../../thermoctl/web/navigation.py', import.meta.url);

function serverItems(): ServerNavItem[] {
  const source = readFileSync(PY, 'utf8');
  const block = source.slice(source.indexOf('NAVIGATION_ITEMS'));
  const items = [...block.matchAll(/NavigationItem\(\s*"(\/[^"]*)",\s*"([^"]+)"/g)];
  return items.map((m) => ({ path: m[1], label: m[2], section: 'main' }));
}

test('Python-Datei wird vollständig gelesen (Schutz gegen leeren Parser)', () => {
  const items = serverItems();
  assert.ok(items.length >= 24, `nur ${items.length} Einträge gefunden`);
  assert.equal(new Set(items.map((i) => i.path)).size, items.length, 'Pfade im Server nicht eindeutig');
});

test('jeder Server-Pfad erscheint genau einmal in einem Bereich (oder im Konto)', () => {
  const placed = [...AREAS.flatMap((a) => a.pages.flatMap((p) => p.paths)), ...ACCOUNT_PATHS];
  for (const { path } of serverItems()) {
    assert.equal(placed.filter((p) => p === path).length, 1, `${path} steht nicht genau einmal in der Abbildung`);
  }
});

test('die Abbildung enthält keine Pfade, die der Server nicht kennt', () => {
  const known = new Set(serverItems().map((i) => i.path));
  const placed = [...AREAS.flatMap((a) => a.pages.flatMap((p) => p.paths)), ...ACCOUNT_PATHS];
  assert.deepEqual(
    placed.filter((p) => !known.has(p)),
    [],
  );
});

test('sieben Bereiche mit eindeutigen Kennungen, Seiten-IDs je Bereich eindeutig', () => {
  assert.equal(AREAS.length, 7);
  assert.equal(new Set(AREAS.map((a) => a.id)).size, 7);
  assert.deepEqual(
    AREAS.map((a) => a.label),
    [
      'Übersicht',
      'Zeit & Abwesenheit',
      'Auswertung',
      'Geräte',
      'Heizung & Regelung',
      'System & Betrieb',
      'Personen & Zugänge',
    ],
  );
  for (const area of AREAS) {
    assert.equal(new Set(area.pages.map((p) => p.id)).size, area.pages.length, area.id);
  }
});

test('Sichtbarkeit kommt nur aus der Server-Navigation', () => {
  const none = buildVisibleAreas([]);
  assert.deepEqual(none.map((a) => a.id), ['uebersicht']);
  assert.equal(none[0].pages.length, 0);

  const some = buildVisibleAreas([
    { path: '/devices', label: 'Geräte', section: 'main' },
    { path: '/audit', label: 'Protokoll', section: 'analysis' },
    { path: '/changes', label: 'Änderungsprotokoll', section: 'analysis' },
  ]);
  assert.deepEqual(some.map((a) => a.id), ['uebersicht', 'auswertung', 'geraete']);
  const events = some.find((a) => a.id === 'auswertung')!.pages;
  assert.equal(events.length, 1);
  assert.deepEqual(events[0].links.map((l) => l.path), ['/audit', '/changes']);
});

test('ein Server-Pfad ohne Abbildung erscheint in keinem Bereich', () => {
  const areas = buildVisibleAreas([
    { path: '/neu-analyse', label: 'Neu', section: 'analysis' },
    { path: '/neu-zugang', label: 'Zugang', section: 'access' },
  ]);
  assert.deepEqual(areas.map((a) => a.id), ['uebersicht']);
  assert.ok(areas.every((a) => a.pages.every((p) => !p.links.some((l) => l.path.startsWith('/neu')))));
});

test('Einstellungssuche findet Seiten und Begriffe, nur im sichtbaren Teil', () => {
  const areas = buildVisibleAreas([
    { path: '/settings', label: 'Regelvorgaben', section: 'system' },
    { path: '/devices', label: 'Geräte', section: 'main' },
  ]);
  assert.deepEqual(searchNavigation(areas, 'hysterese').map((r) => r.to), ['/heizung/regelvorgaben']);
  assert.deepEqual(searchNavigation(areas, 'GERÄT austauschen').map((r) => r.to), ['/geraete/liste']);
  assert.deepEqual(searchNavigation(areas, 'token'), [], 'nicht sichtbarer Bereich darf nicht auftauchen');
  assert.deepEqual(searchNavigation(areas, '   '), []);
});

test('alsoWith: die Seite erscheint auch ohne eigenen Pfad, aber nur wenn der genannte Pfad da ist', () => {
  const zones = { path: '/zones', label: 'Zonen', section: 'main' };
  const admin = buildVisibleAreas([zones]);
  const page = admin.find((a) => a.id === 'zeit')?.pages.find((p) => p.id === 'wochenplaene');
  assert.ok(page, 'Wochenpläne fehlen in der Verwaltungssicht');
  assert.deepEqual(page.links, []);
  assert.equal(buildVisibleAreas([]).find((a) => a.id === 'zeit'), undefined);
  for (const area of AREAS) {
    for (const p of area.pages) {
      for (const extra of p.alsoWith ?? []) {
        assert.ok(serverItems().some((i) => i.path === extra), `${extra} kennt der Server nicht`);
      }
    }
  }
});

test('Anlagenbild steht unter Geräte und erscheint mit der Geräteübersicht, nicht ohne', () => {
  const geraete = AREAS.find((a) => a.id === 'geraete')!;
  const page = geraete.pages.find((p) => p.id === 'anlagenbild')!;
  assert.deepEqual(page.paths, []);
  assert.deepEqual(page.alsoWith, ['/devices']);
  const some = buildVisibleAreas([{ path: '/devices', label: 'Geräte', section: 'main' }]);
  assert.ok(some.find((a) => a.id === 'geraete')!.pages.some((p) => p.id === 'anlagenbild'));
  const none = buildVisibleAreas([{ path: '/audit', label: 'Protokoll', section: 'analysis' }]);
  assert.equal(none.some((a) => a.id === 'geraete'), false);
  assert.deepEqual(searchNavigation(some, 'Anlagenbild').map((r) => r.to), ['/geraete/anlagenbild']);
});
