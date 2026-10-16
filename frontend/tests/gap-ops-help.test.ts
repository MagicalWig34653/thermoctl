// Testlücken der abgelösten Alt-Oberfläche: Hilfe-Popover der Betriebs-, Regelvorgaben- und
// Raumtechnik-Seiten. Die Popover hängen an Begriffen aus `OPS_TERMS`; hier steht fest, welche
// Seite welchen Begriff anbietet und dass kein Verweis ins Leere zeigt.

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { test } from 'node:test';
import { OPS_TERMS } from '../src/lib/operations/glossary.ts';
import { stateCards } from '../src/lib/operations/logic.ts';
import type { OperationActuation } from '../src/lib/operations/types.ts';

const COMPONENTS = new URL('../src/lib/components/operations/', import.meta.url);
const source = (name: string) => readFileSync(new URL(name, COMPONENTS), 'utf8');

/** Alle Begriffe, die eine Datei als Zeichenkette nennt und die es im Glossar gibt. */
function termsIn(text: string): Set<string> {
  const found = new Set<string>();
  for (const match of text.matchAll(/['"]([a-z_]+)['"]/g)) if (match[1] in OPS_TERMS) found.add(match[1]);
  return found;
}

const actuation: OperationActuation = {
  armed: false,
  sending_allowed: false,
  cluster_standby: false,
  live: false,
  dry_run: true,
  restart_required: false,
  state: 'dry_run',
};

test('Jeder Begriff ist im Glossar mit Titel und Text vorhanden', () => {
  for (const [key, term] of Object.entries(OPS_TERMS)) {
    assert.ok(term.title.trim().length > 0, `${key}: Titel`);
    assert.ok(term.text.trim().length > 0, `${key}: Text`);
  }
});

test('Betriebsseite: die Zustandskarten bieten Hilfe, darunter der Trockenlauf', () => {
  const cards = stateCards(actuation);
  assert.deepEqual(cards.map((c) => c.help), ['armed', 'sending', 'role', 'dry_run']);
  for (const card of cards) assert.ok(card.help in OPS_TERMS, card.help);
  const dryRun = OPS_TERMS[cards.find((c) => c.id === 'dry_run')!.help];
  assert.equal(dryRun.title, 'Trockenlauf');
  assert.match(dryRun.text, /sendet aber nichts an die Aktoren/);
  // Die Karten und die Betriebsseite hängen den Popover wirklich an.
  assert.match(source('StateCards.svelte'), /<OpsHelp term=\{card\.help\}/);
  assert.match(source('BetriebPage.svelte'), /<StateCards /);
  assert.match(source('ArmPanel.svelte'), /<OpsHelp term="arm_reason"/);
});

test('Regelvorgaben: Hysterese, Mindestzeiten, Notsollwert, Außentemperaturkurve und Rückkehr', () => {
  const vorgaben = termsIn(source('RegelvorgabenPage.svelte'));
  assert.ok(vorgaben.has('hysteresis'));
  assert.ok(vorgaben.has('min_times'));
  const notbetrieb = source('EmergencyPage.svelte');
  assert.match(notbetrieb, /help="emergency_curve"/);
  assert.match(notbetrieb, /help="emergency_setpoint"/);
  assert.match(notbetrieb, /help="recovery"/);
  assert.equal(OPS_TERMS.emergency_setpoint.title, 'Notsollwert');
  assert.equal(OPS_TERMS.emergency_curve.title, 'Außentemperaturkurve');
  // Die Hilfe hängt an den richtigen Feldern der Anlagenvorgaben.
  const page = source('RegelvorgabenPage.svelte');
  assert.match(page, /'default_hysteresis_k' \? 'hysteresis'/);
  assert.match(page, /'default_min_on_seconds' \|\| name === 'default_min_off_seconds' \? 'min_times'/);
});

test('Raumtechnik: Hysterese, Mindestzeiten, Sonnenabsenkung, PI, Ventilschutz, Fenster, Notbetrieb', () => {
  const page = source('RoomTechnik.svelte');
  const offered = termsIn(page);
  for (const term of ['hysteresis', 'min_times', 'solar_max', 'valve_protection', 'pi', 'temp_drop', 'emergency_setpoint']) {
    assert.ok(offered.has(term), `Raumtechnik bietet keine Hilfe zu „${term}“`);
  }
  assert.match(page, /name === 'hysteresis_k' \? 'hysteresis'/);
  assert.match(page, /name === 'min_on_seconds' \|\| name === 'min_off_seconds' \? 'min_times'/);
  assert.match(page, /help="solar_max"/);
  assert.match(page, /help="valve_protection"/);
  assert.match(page, /help="pi"/);
  assert.match(page, /help="temp_drop"/);
});

test('Kein Hilfeverweis einer Betriebsseite zeigt auf einen fehlenden Glossareintrag', () => {
  const direct = /(?:help|term)=["']([a-z_]+)["']/g;
  let checked = 0;
  for (const file of readdirSync(COMPONENTS)) {
    if (!file.endsWith('.svelte')) continue;
    for (const match of source(file).matchAll(direct)) {
      checked += 1;
      assert.ok(match[1] in OPS_TERMS, `${file}: Hilfe zu „${match[1]}“ gibt es nicht im Glossar`);
    }
  }
  assert.ok(checked >= 15, `nur ${checked} Hilfeverweise gefunden — stimmt die Suche noch?`);
});
