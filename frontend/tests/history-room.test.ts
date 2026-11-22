import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ALL_ZONES, defaultZoneId, resolveHistoryZone, withZoneChoice, zoneChoiceFromSearch } from '../src/lib/preselect';

// Reihenfolge wie auf der Seite (Server: sort_order, Name): 12 steht vor 3.
const zones = [{ id: 12 }, { id: 3 }, { id: 7 }];

test('Standardraum: der erste sichtbare Raum in der Reihenfolge der Seite', () => {
  assert.equal(defaultZoneId(zones), 12);
  assert.equal(defaultZoneId([{ id: 3 }]), 3);
  assert.equal(defaultZoneId([]), null);
});

test('Auswahl aus der Adresse: Raum, „alle“ oder keine Angabe', () => {
  assert.equal(zoneChoiceFromSearch('?zone=7', zones), 7);
  assert.equal(zoneChoiceFromSearch(`?zone=${ALL_ZONES}`, zones), 'all');
  assert.equal(zoneChoiceFromSearch('?zone=alle&period=30d', zones), 'all');
  assert.equal(zoneChoiceFromSearch('', zones), null);
  assert.equal(zoneChoiceFromSearch('?period=24h', zones), null);
});

test('Auswahl aus der Adresse: Unbekanntes und Gefälschtes zählt als keine Angabe', () => {
  for (const s of ['?zone=99', '?zone=ALLE', '?zone=alle%20', '?zone=', '?zone=-1', '?zone=alle,3', '?zone[]=alle']) {
    assert.equal(zoneChoiceFromSearch(s, zones), null, s);
  }
  // „alle“ gilt nur, wenn es etwas anzuzeigen gibt.
  assert.equal(zoneChoiceFromSearch('?zone=alle', []), null);
});

test('Anzeige: ohne Angabe der erste Raum, „alle“ nur auf ausdrücklichen Wunsch', () => {
  assert.equal(resolveHistoryZone('', zones), 12);
  assert.equal(resolveHistoryZone('?period=30d', zones), 12);
  assert.equal(resolveHistoryZone('?zone=7', zones), 7);
  assert.equal(resolveHistoryZone('?zone=alle', zones), null);
  // Unbekannter oder nicht sichtbarer Raum: zurück auf den Standard, nie ein fremder Raum.
  assert.equal(resolveHistoryZone('?zone=99', zones), 12);
  assert.equal(resolveHistoryZone('?zone=3', [{ id: 12 }]), 12);
  assert.equal(resolveHistoryZone('', []), null);
});

test('Adresse bei Wahl: nur „zone“ ändern, alles andere bleibt', () => {
  assert.equal(withZoneChoice('', 7), '?zone=7');
  assert.equal(withZoneChoice('', null), '?zone=alle');
  assert.equal(withZoneChoice('?zone=3&period=30d', 7), '?zone=7&period=30d');
  assert.equal(withZoneChoice('?period=30d&zone=3', null), '?period=30d&zone=alle');
  assert.equal(withZoneChoice('?period=24h', 3), '?period=24h&zone=3');
});

test('Wahl und Auslesen sind zueinander umkehrbar', () => {
  for (const choice of [12, 3, 7, null]) {
    assert.equal(resolveHistoryZone(withZoneChoice('?period=7d', choice), zones), choice);
  }
});
