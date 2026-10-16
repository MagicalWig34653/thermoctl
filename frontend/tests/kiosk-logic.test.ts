import assert from 'node:assert/strict';
import { test } from 'node:test';
import { KioskError } from '../src/lib/kiosk/client.ts';
import {
  INVALID_TEXT,
  ageText,
  clockText,
  describeFailure,
  heatText,
  isKioskActionable,
  overrideText,
  replaceRoom,
} from '../src/lib/kiosk/logic.ts';
import type { KioskRoom } from '../src/lib/kiosk/types.ts';
import { appHref, internalTarget, isKioskPath } from '../src/lib/paths.ts';

const room = (id: number, extra: Partial<KioskRoom> = {}): KioskRoom => ({
  id,
  name: `Raum ${id}`,
  mode_label: 'auto',
  temperature: '20.50',
  measured_at: null,
  sensor: null,
  sensor_stuck: false,
  setpoint: { temperature: '21.0', reason: 'Zeitplan' },
  would_heat: null,
  override: null,
  emergency: null,
  actions: { setpoint: false, boost: false, cancel_override: false },
  ...extra,
});

test('Widerrufener Zugang: Hinweis, Sperre, kein Nachladen -- keine Anmeldung', () => {
  const out = describeFailure(new KioskError('invalid', 401, 'x', 'kiosk_token_invalid'), 'step');
  assert.equal(out.invalid, true);
  assert.equal(out.refresh, false);
  assert.equal(out.message, INVALID_TEXT);
  assert.doesNotMatch(out.message, /anmeld/i);
});

test('Fehlertexte nennen keine Rohmeldungen von Netz oder Server', () => {
  const net = describeFailure(new KioskError('network', 0, 'TypeError: Failed to fetch'), 'boost');
  assert.doesNotMatch(net.message, /TypeError|fetch/);
  assert.equal(net.refresh, true, 'nach unklarem Ergebnis den Zustand lesen');
  const http = describeFailure(new KioskError('http', 500, 'Traceback ...'), 'step');
  assert.doesNotMatch(http.message, /Traceback/);
  assert.match(http.message, /500/);
  const other = describeFailure(new Error('boom'), 'step');
  assert.equal(other.invalid, false);
});

test('Ablehnung der Regelung zeigt deren Grund, ohne Nachladen', () => {
  const out = describeFailure(new KioskError('rejected', 422, 'Sollwert muss zwischen 5 und 35 liegen.'), 'step');
  assert.equal(out.message, 'Sollwert muss zwischen 5 und 35 liegen.');
  assert.equal(out.refresh, false);
});

test('Fremder Raum, fehlendes Recht: Anzeige wird neu gelesen', () => {
  assert.equal(describeFailure(new KioskError('not_found', 404, 'x'), 'cancel').refresh, true);
  assert.equal(describeFailure(new KioskError('forbidden', 403, 'x'), 'boost').refresh, true);
  assert.equal(describeFailure(new KioskError('csrf', 403, 'x'), 'boost').refresh, false);
  assert.equal(describeFailure(new KioskError('setup', 503, 'x'), 'load').refresh, false);
});

test('Lesefehler lädt nicht erneut aus sich selbst heraus (der Takt macht das)', () => {
  assert.equal(describeFailure(new KioskError('network', 0, 'x'), 'load').refresh, false);
  assert.equal(describeFailure(new KioskError('http', 502, 'x'), 'load').refresh, false);
});

test('replaceRoom ersetzt nur denselben Raum und fügt nichts hinzu', () => {
  const rooms = [room(1), room(2)];
  const updated = room(2, { name: 'neu' });
  assert.deepEqual(replaceRoom(rooms, updated).map((r) => r.name), ['Raum 1', 'neu']);
  assert.equal(replaceRoom(rooms, room(9)).length, 2);
});

test('ageText', () => {
  const now = '2026-10-16T12:00:00+00:00';
  assert.equal(ageText(null, now), 'kein Messwert');
  assert.equal(ageText('2026-10-16T11:59:30+00:00', now), 'gerade eben');
  assert.equal(ageText('2026-10-16T11:57:00+00:00', now), 'vor 3 Min.');
  assert.equal(ageText('2026-10-16T09:00:00+00:00', now), 'vor 3 Std.');
  assert.equal(ageText('2026-10-13T12:00:00+00:00', now), 'vor 3 Tg.');
  assert.equal(ageText('2026-10-16T12:05:00+00:00', now), 'gerade eben', 'Zukunft wird nicht negativ');
  assert.equal(ageText('kaputt', now), 'kein Messwert');
});

test('clockText nutzt die Zeitzone der Anlage, fällt bei ungültiger zurück', () => {
  assert.equal(clockText('2026-10-16T12:05:00+00:00', 'Europe/Berlin'), '14:05');
  assert.match(clockText('2026-10-16T12:05:00+00:00', 'Nirgendwo/Land'), /^\d\d:\d\d$/);
  assert.equal(clockText('kaputt', 'UTC'), '');
});

test('overrideText', () => {
  assert.equal(overrideText(null, 'UTC'), null);
  assert.equal(overrideText({ ends_at: null, temperature: '23.0' }, 'UTC'), 'Übersteuert, ohne Ende');
  assert.equal(overrideText({ ends_at: '2026-10-16T12:30:00+00:00', temperature: null }, 'UTC'), 'Übersteuert bis 12:30');
  assert.equal(overrideText({ ends_at: 'kaputt', temperature: null }, 'UTC'), 'Übersteuert');
});

test('heatText und isKioskActionable', () => {
  assert.equal(heatText(true), 'Heizanforderung');
  assert.equal(heatText(false), 'Aus');
  assert.equal(heatText(null), 'Noch nicht entschieden');
  assert.equal(isKioskActionable(room(1)), false);
  assert.equal(isKioskActionable(room(1, { actions: { setpoint: false, boost: true, cancel_override: false } })), true);
});

test('Router: /app/kiosk wird als Kiosk erkannt, bevor irgendetwas anderes läuft', () => {
  assert.equal(isKioskPath('/app/kiosk', ''), true);
  assert.equal(isKioskPath('/app/kiosk/', ''), true);
  assert.equal(isKioskPath('/ing/app/kiosk', '/ing'), true);
  assert.equal(isKioskPath('/app', ''), false);
  assert.equal(isKioskPath('/app/kiosk/x', ''), false);
  assert.equal(isKioskPath('/app/kioskfoo', ''), false);
  assert.equal(isKioskPath('/kiosk', ''), false, 'das ist der alte HTML-Kiosk des Servers');
  assert.equal(isKioskPath('/ing/app/kiosk', ''), false, 'anderer Präfix');
});

test('Verweise der Hauptanwendung führen nie ohne Neuladen in den Kiosk', () => {
  assert.equal(internalTarget('https://h.example/app/kiosk', '', 'https://h.example'), null);
  assert.equal(internalTarget('https://h.example/app/raeume/3', '', 'https://h.example'), '/raeume/3');
  assert.equal(appHref('', '/kiosk'), '/app/kiosk');
});
