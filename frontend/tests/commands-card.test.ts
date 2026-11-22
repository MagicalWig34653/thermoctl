import assert from 'node:assert/strict';
import { test } from 'node:test';
import { commandCard, commandRowKey } from '../src/lib/devices/commands.ts';
import type { CommandEntry, CommandLog } from '../src/lib/devices/types.ts';

const NOW = new Date('2026-11-25T10:00:00Z'); // ein anderer Tag: die Zeit trägt dann das Datum

const entry = (over: Partial<CommandEntry> = {}): CommandEntry => ({
  id: 7,
  sent_at: '2026-11-20T11:01:00+00:00',
  kind: 'befehl',
  source: 'system',
  zone_name: 'Wohnzimmer',
  device_name: 'Heizkörper',
  command: 'setpoint',
  payload: '{"occupied_heating_setpoint": 21.0}',
  outcome: 'executed',
  error: null,
  reason: 'Zeitplan',
  simulated: false,
  ...over,
});

const log: Pick<CommandLog, 'timezone' | 'outcome_labels' | 'source_labels'> = {
  timezone: 'Europe/Berlin',
  outcome_labels: { executed: 'Ausgeführt', suppressed: 'Unterdrückt (Trockenlauf)', absenkung_beginn: 'Absenkung beginnt' },
  source_labels: { system: 'System', sonnenabsenkung: 'Regelung (Sonnenabsenkung)' },
};

const row = (e: CommandEntry, key: string, l = log) => commandCard(e, l, NOW).rows.find((r) => r.key === key);

test('Schlüssel: gleiche Nummer aus verschiedenen Tabellen ergibt verschiedene Schlüssel', () => {
  // Befehl, Entscheidung und Absenkung kommen aus drei Tabellen mit eigener Zählung.
  const keys = ['befehl', 'entscheidung', 'absenkung'].map((kind) => commandRowKey(entry({ id: 4, kind })));
  assert.equal(new Set(keys).size, 3);
});

test('Karte (Befehl): Ergebnis wird übersetzt, Quelle und Zeit stehen in der Ortszeit der Anlage', () => {
  const card = commandCard(entry(), log, NOW);
  assert.equal(card.kind.label, 'Befehl');
  assert.match(card.time, /20\.11\..*12:01/);
  assert.equal(row(entry(), 'ergebnis')?.value, 'Ausgeführt');
  assert.equal(row(entry(), 'quelle')?.value, 'System');
  assert.equal(row(entry(), 'raum')?.value, 'Wohnzimmer');
  assert.equal(row(entry(), 'geraet')?.value, 'Heizkörper');
});

test('Karte: Nutzlast hängt am Befehl, Begründung steht vollständig und ungekürzt', () => {
  const lang = 'Der Zeitplan wechselt in dieser Minute auf den Nachtwert. '.repeat(8) + 'Ende.';
  const befehl = row(entry({ reason: lang }), 'befehl');
  assert.equal(befehl?.value, 'setpoint');
  assert.equal(befehl?.detail, '{"occupied_heating_setpoint": 21.0}');
  assert.equal(row(entry({ reason: lang }), 'begruendung')?.value, lang);
});

test('Karte: Fehler und Begründung nur, wenn es sie gibt', () => {
  const ohne = commandCard(entry({ reason: null, error: null }), log, NOW);
  assert.deepEqual(
    ohne.rows.map((r) => r.key),
    ['raum', 'geraet', 'befehl', 'quelle', 'ergebnis'],
  );
  const mit = commandCard(entry({ error: 'Keine Antwort.', reason: 'Zeitplan' }), log, NOW);
  assert.deepEqual(
    mit.rows.map((r) => r.key),
    ['raum', 'geraet', 'befehl', 'quelle', 'ergebnis', 'fehler', 'begruendung'],
  );
  assert.equal(mit.rows.find((r) => r.key === 'fehler')?.tone, 'fehler');
});

test('Karte: Trockenlauf wird am Ergebnis gekennzeichnet', () => {
  assert.equal(commandCard(entry({ simulated: true }), log, NOW).simulated, true);
  assert.equal(commandCard(entry({ simulated: false }), log, NOW).simulated, false);
});

test('Karte (Absenkung): ohne Gerät und Nutzlast bleibt sie lesbar, Art und Ergebnis stehen da', () => {
  const absenkung = entry({
    id: 2,
    kind: 'absenkung',
    source: 'sonnenabsenkung',
    device_name: '',
    command: 'sonnenabsenkung',
    payload: '',
    outcome: 'absenkung_beginn',
    reason: 'Sonnenabsenkung -1,5 K: Sollwert vor Absenkung 20,0 °C, wirksam 18,5 °C',
  });
  const card = commandCard(absenkung, log, NOW);
  assert.equal(card.kind.label, 'Absenkung');
  assert.equal(row(absenkung, 'geraet')?.value, '—');
  assert.equal(row(absenkung, 'befehl')?.detail, undefined);
  assert.equal(row(absenkung, 'ergebnis')?.value, 'Absenkung beginnt');
  assert.equal(row(absenkung, 'quelle')?.value, 'Regelung (Sonnenabsenkung)');
  assert.match(row(absenkung, 'begruendung')?.value ?? '', /18,5 °C/);
});

test('Karte (Entscheidung): Art „Entscheidung“, Begründung des Notbetriebs bleibt erhalten', () => {
  const entscheidung = entry({ kind: 'entscheidung', source: 'regelung', command: 'no_write', payload: '', simulated: true, reason: 'Fühler ausgefallen.' });
  const card = commandCard(entscheidung, log, NOW);
  assert.equal(card.kind.kind, 'entscheidung');
  assert.equal(card.simulated, true);
  assert.equal(row(entscheidung, 'quelle')?.value, 'regelung'); // ohne Bezeichnung: der Code
  assert.equal(row(entscheidung, 'begruendung')?.value, 'Fühler ausgefallen.');
});

test('Karte: unbekannter Ergebniscode erscheint als Code, fehlendes als Strich', () => {
  assert.equal(row(entry({ outcome: 'neu_erfunden' }), 'ergebnis')?.value, 'neu_erfunden');
  assert.equal(row(entry({ outcome: null }), 'ergebnis')?.value, '—');
  assert.equal(row(entry({ zone_name: null, source: null, command: null }), 'raum')?.value, '—');
  assert.equal(row(entry({ source: null }), 'quelle')?.value, '—');
  assert.equal(row(entry({ command: null }), 'befehl')?.value, '—');
});

test('Karte: ohne Zeitzone der Anlage gilt UTC, ohne Zeitpunkt ein Strich', () => {
  const utc = commandCard(entry(), { ...log, timezone: null }, NOW);
  assert.match(utc.time, /11:01/);
  assert.equal(commandCard(entry({ sent_at: null }), log, NOW).time, '—');
});
