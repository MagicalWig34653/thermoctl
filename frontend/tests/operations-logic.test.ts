import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import {
  armOutcome, buildParametersPatch, buildSensorFailureBody, decisionEffect, defaultsPayload, displayDetailValue,
  draftFromParameters, errorAt, fromInput, generalError, inheritanceNote, localParameterErrors, opsFailure,
  operationHeadline, outsideLimit, patchedFields, sameNumber, sensorFailureDirty, sensorFailureDraft, solarExample, stateCards,
  SENDING_LATCH_NOTE, subtractDecimal, switchingTable, wearRatioSentence, formatCount, testOutcomeFromFailure, testOutcomeFromResult, thresholdsPayload, toInput,
} from '../src/lib/operations/logic.ts';
import { actuation, ARMED_NO_RESTART, LIVE, parameters, settings } from './operations-fixture.ts';

// -- Zahlen ---------------------------------------------------------------------------------

test('Komma und Punkt: Anzeige mit Komma, Versand mit Punkt, nie gerundet', () => {
  assert.equal(toInput('0.50'), '0,50');
  assert.equal(fromInput(' 0,125 '), '0.125');
  assert.equal(fromInput('21.123456789012345678'), '21.123456789012345678');
});

test('sameNumber vergleicht exakt über die Ziffern, nicht über Gleitkomma', () => {
  assert.ok(sameNumber('0.5', '0,50'));
  assert.ok(!sameNumber('0.1', '0.10000000000000001'));
  assert.ok(sameNumber('abc', 'abc'));
  assert.ok(!sameNumber('abc', 'abd'));
});

test('subtractDecimal rechnet exakt', () => {
  assert.equal(subtractDecimal('21.0', '1.5'), '19.5');
  assert.equal(subtractDecimal('21.0', '0.1'), '20.9');
  assert.equal(subtractDecimal('0.3', '0.1'), '0.2');
  assert.equal(subtractDecimal('1', '2.5'), '-1.5');
  assert.equal(subtractDecimal('21', '21'), '0');
  assert.equal(subtractDecimal('21.0', 'x'), null);
  assert.equal(subtractDecimal('21.0', ''), null);
});

test('Beispielrechnung der Sonne ist rein rechnerisch und exakt', () => {
  assert.deepEqual(solarExample('1,5'), { plan: '21.0', target: '19.5' });
  assert.equal(solarExample('').target, null);
});

// -- Fehlerabbildung ----------------------------------------------------------------------------

test('Feldfehler: nur 422 mit passendem Feld erscheint am Feld', () => {
  const failure = opsFailure(new ApiError('http', 422, 'Zu klein.', false, 'validation', 'hysteresis_k'));
  assert.equal(failure.kind, 'validation');
  assert.equal(errorAt(failure, 'hysteresis_k'), 'Zu klein.');
  assert.equal(errorAt(failure, 'min_on_seconds'), null);
  assert.equal(errorAt(null, 'hysteresis_k'), null);
  const other = opsFailure(new ApiError('http', 409, 'Konflikt.', false, 'no_webhook'));
  assert.equal(errorAt(other, 'hysteresis_k'), null);
});

test('Ein Feldfehler ohne sichtbares Feld verschwindet nicht', () => {
  const failure = opsFailure(new ApiError('http', 422, 'Unbekanntes Feld.', false, 'validation', 'ghost'));
  assert.equal(generalError(failure, () => false), 'Unbekanntes Feld.');
  assert.equal(generalError(failure, (f) => f === 'ghost'), null);
  assert.equal(generalError(null, () => true), null);
  const net = opsFailure(new ApiError('network', 0, 'x'));
  assert.match(generalError(net, () => true)!, /unklar/);
});

test('Sitzungsablauf wird als solcher gemeldet, die Eingabe gilt als nicht gesendet', () => {
  const failure = opsFailure(new ApiError('unauthorized', 401, 'weg'));
  assert.equal(failure.kind, 'session');
  assert.match(failure.message, /nicht gesendet/);
});

// -- Teil-Update ---------------------------------------------------------------------------------

test('Unveränderter Entwurf ergibt ein leeres Teil-Update', () => {
  const p = parameters();
  assert.deepEqual(buildParametersPatch(p, draftFromParameters(p)), {});
});

test('Nur geänderte Felder gehen mit; Komma wird zum Punkt', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.control.hysteresis_k = '0,7';
  d.valve.enabled = true;
  const patch = buildParametersPatch(p, d);
  assert.deepEqual(patch, { hysteresis_k: '0.7', valve_protection_enabled: true });
  assert.deepEqual(patchedFields(patch).sort(), ['hysteresis_k', 'valve_protection_enabled']);
});

test('Eigenwert leeren heißt null (erben); ein geerbtes Feld leer lassen sendet nichts', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.control.min_on_seconds = ''; // war Eigenwert 300
  assert.deepEqual(buildParametersPatch(p, d), { min_on_seconds: null });
  const d2 = draftFromParameters(p);
  d2.control.hysteresis_k = ''; // war schon geerbt
  assert.deepEqual(buildParametersPatch(p, d2), {});
});

test('Gleicher Zahlenwert in anderer Schreibweise ist keine Änderung', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.control.min_on_seconds = '300,0';
  d.solar_gain_factor = '0,5';
  assert.deepEqual(buildParametersPatch(p, d), {});
});

test('PI einschalten sendet die Bestätigung mit, Ausschalten nicht; leere PI-Felder werden nie gesendet', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.pi.enabled = true;
  d.pi.confirm = true;
  d.pi.gain_per_k = '';
  assert.deepEqual(buildParametersPatch(p, d), { pi_enabled: true, pi_confirm: true });
  const on = parameters({ pi: { ...p.pi, enabled: true } });
  const d2 = draftFromParameters(on);
  d2.pi.enabled = false;
  assert.deepEqual(buildParametersPatch(on, d2), { pi_enabled: false });
});

test('Notbetrieb der Zone: Profil und Sollwert erben mit null, nur bei Änderung', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.sensor_failure.profile_id = '1';
  d.sensor_failure.emergency_setpoint_c = '17,5';
  assert.deepEqual(buildParametersPatch(p, d), { sensor_failure_profile_id: 1, sensor_failure_emergency_setpoint_c: '17.5' });
  const own = parameters({ sensor_failure: { ...p.sensor_failure, own_profile_id: 1, own_emergency_setpoint_c: '17.5' } });
  const d2 = draftFromParameters(own);
  d2.sensor_failure.profile_id = '';
  d2.sensor_failure.emergency_setpoint_c = '';
  assert.deepEqual(buildParametersPatch(own, d2), { sensor_failure_profile_id: null, sensor_failure_emergency_setpoint_c: null });
});

test('Ausgleichswerte: nur geänderte Zeilen, leer entfernt; ohne Recht nie gesendet', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  assert.deepEqual(buildParametersPatch(p, d), {});
  d.backup_offsets[9] = '0,4';
  assert.deepEqual(buildParametersPatch(p, d), { backup_offsets: [{ assignment_id: 9, offset_k: '0.4' }] });
  d.backup_offsets[9] = '';
  assert.deepEqual(buildParametersPatch(p, d), { backup_offsets: [{ assignment_id: 9, offset_k: null }] });
  const hidden = parameters({ backup_offsets: null });
  assert.deepEqual(buildParametersPatch(hidden, draftFromParameters(hidden)), {});
});

test('Lokale Pflichtangaben: leere Nicht-Erb-Felder und fehlende PI-Bestätigung', () => {
  const p = parameters();
  const d = draftFromParameters(p);
  d.valve.interval_days = ' ';
  d.pi.enabled = true;
  const errors = localParameterErrors(p, d);
  assert.equal(errors.valve_protection_interval_days, 'Bitte einen Wert angeben.');
  assert.ok(errors.pi_confirm);
  d.pi.confirm = true;
  d.valve.interval_days = '7';
  assert.deepEqual(localParameterErrors(p, d), {});
});

test('Vererbung: Eigenwert und geerbter Wert tragen verschiedene Beschriftung', () => {
  assert.equal(inheritanceNote('zone', false), 'Eigenwert dieses Raums');
  assert.equal(inheritanceNote('plant', true), 'Geerbt von der Anlage');
  assert.equal(inheritanceNote('default', true), 'Vorgabe, nicht eigens gesetzt');
});

// -- Anlagenvorgaben -------------------------------------------------------------------------------

test('Regelvorgaben gehen vollständig: nicht bearbeitete Felder aus dem Serverstand, Zeitzone mit', () => {
  const s = settings();
  const body = defaultsPayload(s, { default_hysteresis_k: '0,6' });
  assert.deepEqual(body.values, {
    default_hysteresis_k: '0.6',
    default_min_on_seconds: '300',
    default_solar_setback_max_k: '1.0',
    solar_setback_lookahead_hours: '3',
  });
  assert.equal(body.timezone, 'Europe/Berlin');
  assert.equal(defaultsPayload(s, {}, ' UTC ').timezone, 'UTC');
});

test('Schwellen: alle Felder der Gruppe, Komma wird Punkt', () => {
  const s = settings();
  assert.deepEqual(thresholdsPayload(s.window_alarm, { window_alarm_open_minutes: '45' }), { values: { window_alarm_open_minutes: '45' } });
});

// -- Notbetrieb der Anlage: eine Einheit --------------------------------------------------------------

test('Notbetrieb wird als eine Einheit mit allen Teilen gebaut', () => {
  const s = settings();
  let k = 0;
  const d = sensorFailureDraft(s, () => ++k);
  assert.ok(!sensorFailureDirty(s, d));
  d.emergency_setpoint_c = '17,5';
  d.curve.push({ key: 99, outdoor_c: '-10,5', on_seconds: '300', off_seconds: '120' });
  assert.ok(sensorFailureDirty(s, d));
  const built = buildSensorFailureBody(d);
  assert.ok(built.ok);
  if (built.ok) {
    assert.deepEqual(built.body, {
      fixed_on_seconds: '60',
      fixed_off_seconds: '300',
      recovery_seconds: '600',
      recovery_samples: '3',
      warm_restart_hysteresis_k: '0.5',
      emergency_setpoint_c: '17.5',
      curve_points: [
        { outdoor_c: '-5.0', on_seconds: 120, off_seconds: 240 },
        { outdoor_c: '-10.5', on_seconds: 300, off_seconds: 120 },
      ],
    });
  }
});

test('Notbetrieb: keine ganze Zahl in der Kurve ergibt Feldfehler und nichts wird gebaut', () => {
  let k = 0;
  const d = sensorFailureDraft(settings(), () => ++k);
  d.curve[0].on_seconds = '12,5';
  const built = buildSensorFailureBody(d);
  assert.ok(!built.ok);
  if (!built.ok) assert.deepEqual(Object.keys(built.errors), ['curve_points[0].on_seconds']);
});

// -- Drei Freigaben + Trockenlauf -----------------------------------------------------------------------

test('Die Zustände sind vier getrennte Karten, keine folgt aus einer anderen', () => {
  const cards = stateCards(ARMED_NO_RESTART);
  assert.deepEqual(cards.map((c) => c.id), ['armed', 'sending', 'role', 'dry_run']);
  const byId = Object.fromEntries(cards.map((c) => [c.id, c]));
  assert.equal(byId.armed.value, 'Freigegeben');
  assert.equal(byId.sending.value, 'Gesperrt');
  assert.equal(byId.sending.tone, 'warn');
  assert.match(byId.sending.detail, /Neustart/);
  assert.equal(byId.role.value, 'Primär');
  assert.equal(byId.dry_run.value, 'Ja');
});

test('Alle acht Kombinationen: jede Karte zeigt nur ihre eigene Tatsache', () => {
  for (const armed of [false, true]) for (const sending of [false, true]) for (const standby of [false, true]) {
    const a = actuation({ armed, sending_allowed: sending, cluster_standby: standby, live: armed && sending, dry_run: !(armed && sending), restart_required: armed && !sending, state: armed && sending ? 'live' : armed ? 'armed_restart_missing' : 'dry_run' });
    const c = Object.fromEntries(stateCards(a).map((x) => [x.id, x.value]));
    assert.equal(c.armed, armed ? 'Freigegeben' : 'Nicht freigegeben');
    assert.equal(c.sending, sending ? 'Offen' : 'Gesperrt');
    assert.equal(c.role, standby ? 'Standby' : 'Primär');
    assert.equal(c.dry_run, a.dry_run ? 'Ja' : 'Nein');
  }
});

test('Ausstehender Neustart wird nie als schaltend dargestellt', () => {
  const headline = operationHeadline(ARMED_NO_RESTART);
  assert.equal(headline.tone, 'warn');
  assert.match(headline.text, /erst beim Neustart/);
  assert.match(headline.text, /nichts geschaltet/);
  const effect = decisionEffect(ARMED_NO_RESTART);
  assert.equal(effect.executes, false);
  assert.match(effect.text, /Neustart/);
});

test('Entscheidungen werden nur ausgeführt, wenn live und führend', () => {
  assert.equal(decisionEffect(LIVE).executes, true);
  assert.equal(decisionEffect(actuation()).executes, false);
  assert.equal(decisionEffect({ ...LIVE, cluster_standby: true }).executes, false);
  assert.match(operationHeadline({ ...LIVE, cluster_standby: true }).text, /Standby/);
  assert.equal(operationHeadline(actuation()).tone, 'neutral');
});

test('Freigabe-Ergebnis kommt aus der Serverantwort: restart_required', () => {
  const o = armOutcome(true, { changed: true, actuation: ARMED_NO_RESTART });
  assert.equal(o.tone, 'warn');
  assert.match(o.text, /gespeichert/);
  assert.match(o.text, /erst beim Neustart/);
  assert.match(o.text, /nichts geschaltet/);
  assert.doesNotMatch(o.text, /schaltet jetzt|werden ausgeführt/);
});

test('Freigabe-Ergebnis: live, Standby, keine Änderung, Widerspruch zur Anfrage', () => {
  assert.equal(armOutcome(true, { changed: true, actuation: LIVE }).tone, 'success');
  assert.match(armOutcome(true, { changed: true, actuation: { ...LIVE, cluster_standby: true } }).text, /Standby/);
  assert.match(armOutcome(true, { changed: false, actuation: LIVE }).text, /Keine Änderung/);
  // Server meldet trotz Anfrage „nicht freigegeben“: nie als Erfolg zeigen.
  assert.equal(armOutcome(true, { changed: false, actuation: actuation() }).tone, 'warn');
});

test('Zurücknehmen: Erfolg nur, wenn der Server „nicht freigegeben“ meldet', () => {
  const ok = armOutcome(false, { changed: true, actuation: actuation() });
  assert.equal(ok.tone, 'success');
  assert.match(ok.text, /Trockenlauf/);
  assert.match(armOutcome(false, { changed: false, actuation: actuation() }).text, /Keine Änderung/);
  assert.equal(armOutcome(false, { changed: true, actuation: LIVE }).tone, 'warn');
});

// -- Test-Versand ------------------------------------------------------------------------------------------

test('Test-Versand: ok:false wird ehrlich als Fehlschlag gezeigt', () => {
  const o = testOutcomeFromResult({ ok: false, status_code: 502, duration_seconds: 0.4, error: 'Bad Gateway' });
  assert.equal(o.tone, 'error');
  assert.ok(o.sent);
  assert.match(o.text, /fehlgeschlagen/);
  assert.match(o.text, /502/);
  assert.match(o.text, /Bad Gateway/);
  const named = testOutcomeFromResult({ ok: false, status_code: 500, duration_seconds: 0.1, error: 'Die Gegenstelle antwortete mit Status 500.' });
  assert.equal(named.text, 'Versand fehlgeschlagen. Die Gegenstelle antwortete mit Status 500.');
  const ok = testOutcomeFromResult({ ok: true, status_code: 204, duration_seconds: 0.25, error: null });
  assert.equal(ok.tone, 'success');
});

test('Test-Versand: 409 no_webhook und 429 cooldown senden nichts', () => {
  const no = testOutcomeFromFailure(opsFailure(new ApiError('http', 409, 'x', false, 'no_webhook')));
  assert.equal(no.sent, false);
  assert.match(no.text, /kein Webhook/);
  const cool = testOutcomeFromFailure(opsFailure(new ApiError('http', 429, 'x', false, 'cooldown')));
  assert.equal(cool.sent, false);
  assert.equal(cool.tone, 'warn');
  const net = testOutcomeFromFailure(opsFailure(new ApiError('network', 0, 'x')));
  assert.equal(net.tone, 'error');
  assert.equal(net.sent, false);
});

// -- Geheimnisse ----------------------------------------------------------------------------------------------

test('Geheimnisse erscheinen nie im Klartext', () => {
  const detail = (variable: string | null, value: string) => ({ name: 'n', value, source: 's', variable });
  assert.equal(displayDetailValue(detail('THERMOCTL_MQTT_PASSWORD', 'geheim123')), 'hinterlegt');
  assert.equal(displayDetailValue(detail('THERMOCTL_API_TOKEN', 'abc')), 'hinterlegt');
  assert.equal(displayDetailValue(detail('THERMOCTL_MQTT_PASSWORD', 'nicht hinterlegt')), 'nicht hinterlegt');
  assert.equal(displayDetailValue(detail('THERMOCTL_MQTT_PASSWORD', 'hinterlegt')), 'hinterlegt');
  assert.equal(displayDetailValue(detail('THERMOCTL_MQTT_HOST', 'broker.local')), 'broker.local');
  assert.equal(displayDetailValue(detail('THERMOCTL_NOTIFY_WEBHOOK', 'https://hook.example/…')), 'https://hook.example/…');
  assert.equal(displayDetailValue(detail(null, 'frei')), 'frei');
});

test('Feldnamen der Datenbank werden auf die Eingabe abgebildet, der technische Präfix entfällt', () => {
  const lat = opsFailure(new ApiError('http', 422, 'Bitte einen Wert zwischen -90 und 90 angeben.', false, 'validation', 'solar_forecast_latitude'));
  assert.equal(lat.field, 'latitude');
  assert.equal(errorAt(lat, 'latitude'), 'Bitte einen Wert zwischen -90 und 90 angeben.');
  const sf = opsFailure(new ApiError('http', 422, 'emergency_setpoint_c: Wert zwischen 5 und 30 erforderlich.', false, 'validation', 'emergency_setpoint_c'));
  assert.equal(sf.message, 'Wert zwischen 5 und 30 erforderlich.');
  assert.equal(sf.field, 'emergency_setpoint_c');
  const noField = opsFailure(new ApiError('http', 422, 'Kennlinie: genau ein oberer Aus-Punkt erforderlich.', false, 'validation', 'curve_points'));
  assert.equal(noField.message, 'Kennlinie: genau ein oberer Aus-Punkt erforderlich.');
});

test('Bereichswarnung: exakt über die Ziffern, leer und ungültig warnen nicht', () => {
  const limit = { minimum: '0.1', maximum: '5.0' };
  assert.equal(outsideLimit('99', limit), true);
  assert.equal(outsideLimit('0,05', limit), true);
  assert.equal(outsideLimit('5,00', limit), false);
  assert.equal(outsideLimit('5,001', limit), true);
  assert.equal(outsideLimit('0,1', limit), false);
  assert.equal(outsideLimit('', limit), false);
  assert.equal(outsideLimit('abc', limit), false);
});

test('Sende-Riegel: benennt beide Wege (MQTT und Meross), nicht nur MQTT', () => {
  assert.match(SENDING_LATCH_NOTE, /alle Wege, auf denen Befehle hinausgehen \(MQTT und Meross\)/);
  assert.doesNotMatch(SENDING_LATCH_NOTE, /sitzt im MQTT-Client/);
  const card = stateCards(ARMED_NO_RESTART).find((c) => c.id === 'sending');
  assert.match(card?.title ?? '', /MQTT und Meross/);
  assert.match(decisionEffect(ARMED_NO_RESTART).text, /MQTT und Meross/);
  assert.match(armOutcome(true, { changed: true, actuation: ARMED_NO_RESTART }).text, /MQTT und Meross/);
});

test('Sende-Riegel: Glossar und ArmPanel tragen den Meross-Weg', async () => {
  const { readFileSync } = await import('node:fs');
  const read = (path: string) => readFileSync(new URL(`../src/lib/${path}`, import.meta.url), 'utf8');
  assert.match(read('components/operations/ArmPanel.svelte'), /SENDING_LATCH_NOTE/);
  assert.match(read('components/operations/ArmPanel.svelte'), /Meross/);
  assert.match(read('operations/glossary.ts'), /MQTT und Meross/);
  assert.match(read('rooms/glossary.ts'), /Meross/);
});

test('PI-Warnung: Schalttabelle und Verhältnis stammen vom Server, es wird nur formatiert', () => {
  const model = parameters().pi_switching_model;
  const rows = switchingTable(model);
  assert.deepEqual(rows.map((r) => [r.id, r.perHour, r.perDay, r.perYear]), [
    ['pi_worst_case', '30', '720', '262.800'],
    ['pi_30_percent_duty', '18', '432', '157.680'],
    ['hysteresis_max', '6', '144', '52.560'],
  ]);
  assert.match(rows[0].label, /ungünstigster Fall/);
  assert.equal(switchingTable({ ...model, rows: [{ id: 'neu', per_hour: 1, per_day: 2, per_year: 3 }] })[0].label, 'neu');
  assert.equal(formatCount(500000), '500.000');
  // Das Verhältnis wird nicht im Browser berechnet: ein anderer Serverwert erscheint unverändert.
  const sentence = wearRatioSentence({ ...model, assumed_lifetime_operations: 500000, worst_case_ratio: '0.53' });
  assert.match(sentence, /500\.000 elektrischen Betätigungen/);
  assert.match(sentence, /rund 0,53 solcher Relaislebensdauern im Jahr/);
  assert.match(wearRatioSentence(model), /rund 2,63 /);
  assert.match(wearRatioSentence({ ...model, worst_case_ratio: '9.99' }), /rund 9,99 /);
});

test('PI-Warnung: RoomTechnik bindet sie ein, mit Verweis auf den Relaisverschleiß und Kessel/Verdichter', async () => {
  const { readFileSync } = await import('node:fs');
  const read = (path: string) => readFileSync(new URL(`../src/lib/${path}`, import.meta.url), 'utf8');
  assert.match(read('components/operations/RoomTechnik.svelte'), /<PiWarning model=\{params\.pi_switching_model\}/);
  const warning = read('components/operations/PiWarning.svelte');
  assert.match(warning, /\/auswertung\/verschleiss/);
  assert.match(warning, /Kessel oder Verdichter/);
  assert.doesNotMatch(warning, /262/);
});
