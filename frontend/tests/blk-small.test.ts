import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApi } from '../src/lib/api.ts';
import { AREAS, appPathOf } from '../src/lib/navigation.ts';
import { COPY_FAILED, COPY_OK, copyText, type ClipboardEnv } from '../src/lib/operations/clipboard.ts';
import { commandKind } from '../src/lib/devices/commands.ts';
import { foldAll } from '../src/lib/devices/geometry.ts';
import { formatWearDay } from '../src/lib/history/reports.ts';
import { roomFieldsLocked } from '../src/lib/access/matrix.ts';
import { createZonesClient } from '../src/lib/zones/client.ts';
import { roomShortcuts } from '../src/lib/zones/logic.ts';

const env = (over: Partial<ClipboardEnv> = {}): ClipboardEnv & { copied: string[] } => {
  const copied: string[] = [];
  return {
    isSecureContext: true,
    writeText: async (t) => void copied.push(`api:${t}`),
    selectAndCopy: (t) => (copied.push(`sel:${t}`), true),
    ...over,
    copied,
  };
};

test('Kopieren: sicherer Kontext nutzt die Zwischenablage-Schnittstelle', async () => {
  const e = env();
  assert.deepEqual(await copyText('x', e), { ok: true, text: COPY_OK });
  assert.deepEqual(e.copied, ['api:x']);
});

test('Kopieren: ohne sicheren Kontext oder Schnittstelle greift der Rückfall', async () => {
  const insecure = env({ isSecureContext: false });
  assert.equal((await copyText('x', insecure)).ok, true);
  assert.deepEqual(insecure.copied, ['sel:x']);
  const absent = env({ writeText: null });
  assert.equal((await copyText('y', absent)).ok, true);
  assert.deepEqual(absent.copied, ['sel:y']);
});

test('Kopieren: abgelehnte Schnittstelle fällt auf die Auswahl zurück', async () => {
  const e = env({ writeText: async () => Promise.reject(new Error('verweigert')) });
  assert.deepEqual(await copyText('z', e), { ok: true, text: COPY_OK });
  assert.deepEqual(e.copied, ['sel:z']);
});

test('Kopieren: scheitert auch der Rückfall, sagt die Meldung es ehrlich', async () => {
  const refused = env({ isSecureContext: false, selectAndCopy: () => false });
  assert.deepEqual(await copyText('x', refused), { ok: false, text: COPY_FAILED });
  const throws = env({ isSecureContext: false, selectAndCopy: () => { throw new Error('weg'); } });
  assert.deepEqual(await copyText('x', throws), { ok: false, text: COPY_FAILED });
});

test('Schaltprotokoll: Art je Eintrag', () => {
  assert.equal(commandKind('entscheidung').label, 'Entscheidung');
  assert.equal(commandKind('absenkung').label, 'Absenkung');
  assert.equal(commandKind('befehl').label, 'Befehl');
  assert.equal(commandKind('befehl').hint, null);
  assert.equal(commandKind(undefined).kind, 'befehl');
  assert.notEqual(commandKind('entscheidung').hint, null);
});

test('Relaisverschleiß: Datum deutsch', () => {
  assert.equal(formatWearDay('2026-10-16'), '16.10.2026');
});

test('Gerätekarte: alle ein- und ausklappen', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }];
  assert.deepEqual(foldAll(nodes, true), ['a', 'b']);
  assert.deepEqual(foldAll(nodes, false), []);
  assert.deepEqual(foldAll([], true), []);
});

test('Rechte-Matrix: anlagenweites Recht sperrt die Raumfelder', () => {
  assert.equal(roomFieldsLocked({ plantWide: true }, false), true);
  assert.equal(roomFieldsLocked({ plantWide: false }, false), false);
  assert.equal(roomFieldsLocked({ plantWide: false }, true), true);
});

test('Alte Server-Pfade ergeben Pfade der Oberfläche', () => {
  assert.equal(appPathOf('/'), '/');
  assert.equal(appPathOf('/account'), '/konto');
  assert.equal(appPathOf('/devices'), '/geraete/liste');
  assert.equal(appPathOf('/tokens'), '/personen/tokens');
  assert.equal(appPathOf('/control'), '/system/betrieb');
  assert.equal(appPathOf('/interfaces'), '/system/verbindungen');
  assert.equal(appPathOf('/gibt-es-nicht'), null);
  // jeder Pfad jeder Seite findet sein eigenes Ziel
  for (const a of AREAS) for (const p of a.pages) for (const path of p.paths) {
    assert.equal(appPathOf(path), `/${a.id}/${p.id}`);
  }
});

test('Räume-Liste: Sprunglinks nur zu sichtbaren Ansichten', () => {
  const all = [
    { id: 'zeit', pages: [{ id: 'wochenplaene' }] },
    { id: 'geraete', pages: [{ id: 'liste' }, { id: 'karte' }] },
  ];
  assert.deepEqual(roomShortcuts(all, 7), [
    { label: 'Zeitplan', to: '/zeit/wochenplaene?zone=7' },
    { label: 'Geräte', to: '/geraete/liste' },
    { label: 'Datenfluss', to: '/geraete/karte' },
  ]);
  assert.deepEqual(roomShortcuts([{ id: 'geraete', pages: [{ id: 'liste' }] }], 1), [
    { label: 'Geräte', to: '/geraete/liste' },
  ]);
  assert.deepEqual(roomShortcuts([], 1), []);
});

test('Räume: Sortierwerte kommen von /ui-api/room-orders', async () => {
  const calls: string[] = [];
  const fakeFetch = (async (url: string) => {
    calls.push(url);
    return new Response(JSON.stringify({ rooms: [{ id: 1, sort_order: 3 }] }), { status: 200 });
  }) as unknown as typeof fetch;
  const api = createApi({ fetch: fakeFetch, prefix: '/p', cookies: () => '' });
  assert.deepEqual(await createZonesClient(api).orders(), { rooms: [{ id: 1, sort_order: 3 }] });
  assert.deepEqual(calls, ['/p/ui-api/room-orders']);
});
