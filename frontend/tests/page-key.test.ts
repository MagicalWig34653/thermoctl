import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pageKeyOf } from '../src/lib/paths.ts';

test('pageKeyOf liest Bereich und Seite aus der Adresse, mit und ohne Präfix', () => {
  assert.equal(pageKeyOf('/app/geraete/karte', ''), 'geraete/karte');
  assert.equal(pageKeyOf('/api/hassio_ingress/AAA/app/auswertung/verlauf', '/api/hassio_ingress/AAA'), 'auswertung/verlauf');
});

test('pageKeyOf ergibt null für Startseite, Bereichsseite, Unbekanntes und zu lange Pfade', () => {
  assert.equal(pageKeyOf('/app/', ''), null);
  assert.equal(pageKeyOf('/app/geraete', ''), null);
  assert.equal(pageKeyOf('/app/geraete/gibtsnicht', ''), null);
  assert.equal(pageKeyOf('/app/gibtsnicht/karte', ''), null);
  assert.equal(pageKeyOf('/app/geraete/karte/zu/lang', ''), null);
  assert.equal(pageKeyOf('/zones', ''), null);
});

test('pageKeyOf ignoriert Abfrage und Fragment nicht fälschlich als Seite', () => {
  assert.equal(pageKeyOf('/app/geraete/karte', ''), 'geraete/karte');
});
