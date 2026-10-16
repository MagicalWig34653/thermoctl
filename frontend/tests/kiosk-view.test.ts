import assert from 'node:assert/strict';
import { test } from 'node:test';
import { initialView, normalizeView, PANEL_MAX_WIDTH, PANEL_QUERY, resolveView } from '../src/lib/kiosk/view.ts';

test('unbekannte Werte werden auto, nie ein Fehler', () => {
  assert.equal(normalizeView('panel'), 'panel');
  assert.equal(normalizeView('tafel'), 'tafel');
  assert.equal(normalizeView('auto'), 'auto');
  for (const v of ['', 'Panel', 'kachel', null, undefined, '<script>']) assert.equal(normalizeView(v), 'auto');
});

test('auto entscheidet nach Breite mit der Grenze des Alt-Kiosks (ab 600 px Tafel)', () => {
  assert.equal(resolveView('auto', 390), 'panel');
  assert.equal(resolveView('auto', PANEL_MAX_WIDTH), 'panel');
  assert.equal(resolveView('auto', 600), 'tafel');
  assert.equal(resolveView('auto', 1512), 'tafel');
  assert.match(PANEL_QUERY, /599\.98px/);
});

test('feste Ansichten ändert die Breite nicht', () => {
  assert.equal(resolveView('panel', 1512), 'panel');
  assert.equal(resolveView('tafel', 320), 'tafel');
});

test('Adresse schlägt gemerkte Wahl und wird gemerkt; ohne Angabe gilt das Gemerkte', () => {
  assert.deepEqual(initialView('?ansicht=panel', 'tafel'), { view: 'panel', remember: true });
  assert.deepEqual(initialView('?ansicht=unsinn', 'tafel'), { view: 'auto', remember: true });
  assert.deepEqual(initialView('', 'tafel'), { view: 'tafel', remember: false });
  assert.deepEqual(initialView('?x=1', null), { view: 'auto', remember: false });
  assert.deepEqual(initialView('', 'kaputt'), { view: 'auto', remember: false });
});
