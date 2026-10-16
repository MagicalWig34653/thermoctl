import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatClock, temperatureScale, temperatureTickLabel, timeTicks, zoneOffsetMs } from '../src/lib/history/scales.ts';
import { tooltipPlacement } from '../src/lib/history/tooltip.ts';
import { createLimiter } from '../src/lib/history/limiter.ts';
import { AUDIT_TEXT_MAX, auditObjectFilter, auditQuery, auditSearchText, buildQuery, classifyRevertError, formatDuration, wearLabel } from '../src/lib/history/reports.ts';
import { ApiError } from '../src/lib/api.ts';

test('temperatureScale: Achse umschließt alle Werte, Marken in gleichen Schritten', () => {
  const s = temperatureScale([19.2, 21.7, 22.4]);
  assert.ok(s.low <= 19.2 && s.high >= 22.4);
  assert.ok(s.ticks.length >= 3 && s.ticks.length <= 9);
  for (let i = 1; i < s.ticks.length; i++) assert.equal(Math.round((s.ticks[i] - s.ticks[i - 1]) * 1000), Math.round(s.step * 1000));
  assert.equal(s.ticks[0], s.low);
  assert.equal(s.ticks.at(-1), s.high);
});

test('temperatureScale: ohne Werte und mit einem einzigen Wert bleibt die Achse endlich und nicht flach', () => {
  const none = temperatureScale([]);
  assert.ok(none.high > none.low);
  const one = temperatureScale([21]);
  assert.ok(one.high > one.low && one.low <= 21 && one.high >= 21);
  assert.ok(temperatureScale([NaN, Infinity]).high > 0);
});

test('temperatureScale: sehr große Spanne wählt grobe Schritte', () => {
  const s = temperatureScale([-5, 35]);
  assert.ok(s.step >= 10);
  assert.ok(s.ticks.length <= 8);
});

test('temperatureTickLabel: Komma und Einheit', () => {
  assert.equal(temperatureTickLabel(21.5), '21,5 °C');
  assert.equal(temperatureTickLabel(20), '20 °C');
});

test('timeTicks: runde Ortszeiten in der Zeitzone der Anlage, nicht der des Rechners', () => {
  const start = Date.UTC(2026, 9, 14, 22, 0); // 15.10. 00:00 in Berlin (Sommerzeit)
  const ticks = timeTicks(start, start + 24 * 3600_000, 'Europe/Berlin', 6);
  assert.ok(ticks.length >= 3 && ticks.length <= 6);
  assert.equal(formatClock(ticks[0].t, 'Europe/Berlin').endsWith(':00'), true);
  for (const t of ticks) assert.ok(t.t >= start && t.t <= start + 24 * 3600_000);
  assert.equal(ticks[0].label, '00:00');
});

test('timeTicks: bei mehreren Tagen mit Datum, bei 30 Tagen Tagesmarken', () => {
  const start = Date.UTC(2026, 9, 1);
  const week = timeTicks(start, start + 7 * 86400_000, 'Europe/Berlin', 6);
  assert.ok(week.every((t) => /\d\d\.\d\d\./.test(t.label)));
  const month = timeTicks(start, start + 30 * 86400_000, 'Europe/Berlin', 6);
  assert.ok(month.length <= 6 && month.length >= 3);
  assert.deepEqual(timeTicks(start, start, 'UTC'), []);
});

test('zoneOffsetMs: Sommer- und Winterzeit, unbekannte Zone fällt auf UTC', () => {
  assert.equal(zoneOffsetMs(Date.UTC(2026, 6, 1), 'Europe/Berlin'), 2 * 3600_000);
  assert.equal(zoneOffsetMs(Date.UTC(2026, 0, 1), 'Europe/Berlin'), 3600_000);
  assert.equal(zoneOffsetMs(Date.UTC(2026, 0, 1), 'Nicht/Vorhanden'), 0);
});

test('Tooltip: rechts vom Anker, wenn Platz ist', () => {
  assert.deepEqual(tooltipPlacement(100, 150, { left: 0, right: 600 }), { left: 114, side: 'right' });
});

test('Tooltip: am rechten Rand links vom Anker und nie über die Ansicht hinaus', () => {
  const p = tooltipPlacement(590, 150, { left: 0, right: 600 });
  assert.equal(p.side, 'left');
  assert.ok(p.left >= 0 && p.left + 150 <= 600);
});

test('Tooltip: am linken Rand bleibt er sichtbar; bei zu schmaler Ansicht wird geklemmt', () => {
  assert.ok(tooltipPlacement(2, 150, { left: 0, right: 600 }).left >= 0);
  const narrow = tooltipPlacement(100, 300, { left: 0, right: 250 });
  assert.equal(narrow.left, 0);
  const shifted = tooltipPlacement(10, 100, { left: 40, right: 400 });
  assert.ok(shifted.left >= 40);
});

test('Limiter: höchstens n gleichzeitig, Reihenfolge bleibt, Fehler gibt den Platz frei', async () => {
  const limiter = createLimiter(2);
  let running = 0;
  let peak = 0;
  const order: number[] = [];
  const task = (i: number, fail = false) => () =>
    new Promise<number>((resolve, reject) => {
      running++;
      peak = Math.max(peak, running);
      order.push(i);
      setTimeout(() => {
        running--;
        fail ? reject(new Error('x')) : resolve(i);
      }, 5);
    });
  const results = await Promise.allSettled([1, 2, 3, 4, 5].map((i) => limiter.run(task(i, i === 2))));
  assert.equal(peak, 2);
  assert.deepEqual(order, [1, 2, 3, 4, 5]);
  assert.equal(results.filter((r) => r.status === 'rejected').length, 1);
  assert.equal(limiter.active, 0);
});

test('buildQuery: leere Werte entfallen, Werte werden kodiert', () => {
  assert.equal(buildQuery({ a: '1', b: '', c: undefined, d: null, e: false }), '?a=1');
  assert.equal(buildQuery({ q: 'a b&c' }), '?q=a+b%26c');
  assert.equal(buildQuery({}), '');
  assert.equal(buildQuery({ page: 2, hide: true }), '?page=2&hide=true');
});

test('formatDuration', () => {
  assert.equal(formatDuration(0), '0 Min.');
  assert.equal(formatDuration(30), '< 1 Min.');
  assert.equal(formatDuration(5400), '1 Std. 30 Min.');
  assert.equal(formatDuration(7200), '2 Std.');
  assert.equal(formatDuration(NaN), '0 Min.');
});

test('wearLabel: Stufen des Servers, Unbekanntes bleibt sichtbar', () => {
  assert.equal(wearLabel('danger').tone, 'high');
  assert.equal(wearLabel('normal').tone, 'ok');
  assert.equal(wearLabel('xyz').label, 'xyz');
});

test('Rücknahme: 409 conflict mit Konflikten und Token bietet Erzwingen an', () => {
  const e = new ApiError('http', 409, 'Konflikt', false, 'conflict', null, {
    conflicts: [{ field: 'temperature', current: '22.0', expected: '21.0' }],
    force_token: 'abc',
  });
  const r = classifyRevertError(e);
  assert.equal(r.kind, 'conflict');
  if (r.kind === 'conflict') {
    assert.equal(r.forceToken, 'abc');
    assert.deepEqual(r.conflicts, [{ field: 'temperature', current: '22.0', expected: '21.0' }]);
  }
});

test('Rücknahme: 409 conflict ohne Token oder mit kaputten Konflikten bietet NICHT Erzwingen an', () => {
  const noToken = new ApiError('http', 409, 'Konflikt', false, 'conflict', null, { conflicts: [] });
  assert.equal(classifyRevertError(noToken).kind, 'message');
  const broken = new ApiError('http', 409, 'K', false, 'conflict', null, { conflicts: [1], force_token: 'x' });
  assert.equal(classifyRevertError(broken).kind, 'message');
  const noBody = new ApiError('http', 409, 'K', false, 'conflict');
  assert.equal(classifyRevertError(noBody).kind, 'message');
});

test('Rücknahme: andere 409 (bereits zurückgenommen), 403, Sitzung, Netzwerk -- nur Meldungen', () => {
  const done = classifyRevertError(new ApiError('http', 409, 'Schon zurückgenommen', false, 'already_reverted'));
  assert.equal(done.kind, 'message');
  const forbidden = classifyRevertError(new ApiError('forbidden', 403, 'Nein', false, 'forbidden'));
  assert.equal(forbidden.kind === 'message' && forbidden.message, 'Nein');
  const session = classifyRevertError(new ApiError('unauthorized', 401, 'x'));
  assert.equal(session.kind === 'message' && session.session, true);
  const net = classifyRevertError(new ApiError('network', 0, 'x'));
  assert.equal(net.kind === 'message' && net.tone, 'warn');
  assert.equal(net.kind === 'message' && /unklar/.test(net.message), true);
  assert.equal(classifyRevertError(new Error('boom')).kind, 'message');
});

import { describeBucket, formatHeat, formatRange, formatResolution, formatSetback, periodMs } from '../src/lib/history/format.ts';

test('formatRange: gleiche Grenzen einmal, verschiedene als Spanne, ohne Float-Rundung', () => {
  assert.equal(formatRange('20.50', '20.5'), '20,5 °C');
  assert.equal(formatRange('20.10', '21.07'), '20,1–21,07 °C');
  assert.equal(formatRange(null, '20'), '—');
  assert.equal(formatRange('19.999', '19.999'), '19,999 °C');
});

test('formatSetback: unbekannt (null) ist nicht „keine“ (0)', () => {
  assert.equal(formatSetback(null), 'unbekannt');
  assert.equal(formatSetback('0'), 'keine');
  assert.equal(formatSetback('0.0'), 'keine');
  assert.equal(formatSetback('1.5'), '1,5 K');
});

test('formatHeat, formatResolution, periodMs', () => {
  assert.equal(formatHeat(40), '40 %');
  assert.equal(formatHeat(null), '—');
  assert.equal(formatResolution(120), '2 Min.');
  assert.equal(formatResolution(3600), '1 Std.');
  assert.equal(formatResolution(45), '45 Sek.');
  assert.equal(periodMs('7d'), 7 * 86_400_000);
});

test('describeBucket: Lücke wird als Lücke benannt, nicht als Wert', () => {
  const gap = describeBucket({ start: 'x', rows: 0, measurements: 0, temperature_c: null, temperature_min_c: null, temperature_max_c: null, scheduled_min_c: null, scheduled_max_c: null, effective_min_c: null, effective_max_c: null, heat_percent: null, solar_setback_k: null }, '10:00');
  assert.match(gap, /Messlücke/);
  assert.doesNotMatch(gap, /°C/);
});

const emptyFilter = { fromDate: '', toDate: '', user: '', actionCode: '', zoneId: '', objectText: '', source: '', text: '' };

test('auditSearchText: Ränder weg, auf die Obergrenze gekürzt', () => {
  assert.equal(auditSearchText('  frost  '), 'frost');
  assert.equal(auditSearchText('x'.repeat(AUDIT_TEXT_MAX + 20)).length, AUDIT_TEXT_MAX);
});

test('auditObjectFilter: Raum geht dem Objekttext vor, Unsinn wird nicht zum Raum', () => {
  assert.equal(auditObjectFilter('7', 'token'), 'zone:7');
  assert.equal(auditObjectFilter('', ' token '), 'token');
  assert.equal(auditObjectFilter('7; DROP', 'x'), 'x');
});

test('auditQuery: Filter kodiert, leere entfallen, Seite nur wenn gesetzt', () => {
  assert.equal(auditQuery(emptyFilter), '');
  assert.equal(auditQuery({ ...emptyFilter, text: ' 100% & _ ', zoneId: '3', source: 'web' }, 2),
    '?object=zone%3A3&source=web&q=100%25+%26+_&page=2');
  assert.equal(auditQuery({ ...emptyFilter, actionCode: 'zone.updated' }), '?action_code=zone.updated');
});
