import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/lib/api.ts';
import { LayoutSync, layoutBody, mergeLayout, prefsFromServer, type SyncState, type Timer } from '../src/lib/devices/layout.ts';
import { EMPTY_PREFS, type MapPrefs } from '../src/lib/devices/storage.ts';
import type { LayoutBody, ServerLayout } from '../src/lib/devices/types.ts';

const server = (patch: Partial<ServerLayout> = {}): ServerLayout => ({
  source: 'none', version: 0, default_version: 0, has_default: false, may_set_default: false, nodes: [], view: null, ...patch,
});
const prefs = (patch: Partial<MapPrefs> = {}): MapPrefs => ({ ...EMPTY_PREFS, pos: {}, collapsed: [], ...patch });

test('Server: Knoten werden zu Lage und Einklappzustand, Ansicht wird kopiert', () => {
  const p = prefsFromServer(server({
    source: 'personal',
    nodes: [{ key: 'dev-1', x: 1, y: 2, collapsed: false }, { key: 'zone-2', x: null, y: null, collapsed: true }],
    view: { x: 3, y: 4, k: 0.5 },
  }), true);
  assert.deepEqual(p, { pos: { 'dev-1': { x: 1, y: 2 } }, view: { x: 3, y: 4, k: 0.5 }, collapsed: ['zone-2'], technical: true, dirty: false });
});

test('Zusammenführen: sauber lokal gilt der Server (persönlich oder Vorgabe), technische Werte bleiben lokal', () => {
  const personal = server({ source: 'personal', nodes: [{ key: 'dev-1', x: 5, y: 6, collapsed: false }] });
  const local = prefs({ pos: { 'dev-1': { x: 99, y: 99 } }, technical: true, dirty: false });
  const merged = mergeLayout(personal, local);
  assert.deepEqual(merged.prefs.pos, { 'dev-1': { x: 5, y: 6 } });
  assert.equal(merged.prefs.technical, true);
  assert.equal(merged.push, false);
  const fromDefault = mergeLayout(server({ source: 'default', nodes: [{ key: 'dev-1', x: 7, y: 8, collapsed: true }] }), prefs());
  assert.deepEqual(fromDefault.prefs.pos, { 'dev-1': { x: 7, y: 8 } });
  assert.deepEqual(fromDefault.prefs.collapsed, ['dev-1']);
  const none = mergeLayout(server(), prefs({ pos: { 'dev-1': { x: 1, y: 1 } } }));
  assert.deepEqual(none.prefs, prefs());
});

test('Zusammenführen: ungesendete lokale Änderungen schlagen Server und Vorgabe und gehen hinaus', () => {
  const local = prefs({ pos: { 'dev-1': { x: 1, y: 2 } }, collapsed: ['zone-1'], dirty: true });
  const merged = mergeLayout(server({ source: 'default', nodes: [{ key: 'dev-1', x: 7, y: 8, collapsed: false }] }), local);
  assert.equal(merged.push, true);
  assert.deepEqual(merged.prefs.pos, { 'dev-1': { x: 1, y: 2 } });
  assert.notEqual(merged.prefs.pos, local.pos); // Kopie, kein geteilter Zustand
  // Auch „automatisch angeordnet“ (leer, aber ungesendet) hat Vorrang vor der Vorgabe.
  const auto = mergeLayout(server({ source: 'default', nodes: [{ key: 'dev-1', x: 7, y: 8, collapsed: false }] }), prefs({ dirty: true }));
  assert.equal(auto.push, true);
  assert.deepEqual(auto.prefs.pos, {});
});

test('Körper: nur Knoten, die es auf der Karte gibt; eingeklappt ohne Lage bleibt erhalten', () => {
  const body = layoutBody(
    prefs({ pos: { 'dev-1': { x: 1, y: 2 }, 'dev-gone': { x: 3, y: 4 } }, collapsed: ['zone-1', 'dev-gone2'], view: { x: 1, y: 2, k: 1 } }),
    4,
    ['dev-1', 'zone-1', 'dev-9'],
  );
  assert.deepEqual(body, {
    version: 4,
    nodes: [{ key: 'dev-1', x: 1, y: 2, collapsed: false }, { key: 'zone-1', x: null, y: null, collapsed: true }],
    view: { x: 1, y: 2, k: 1 },
  });
  assert.equal(layoutBody(prefs(), 0, ['dev-1']).view, null);
});

// -- LayoutSync ---------------------------------------------------------------------------

function harness(initial: Partial<ServerLayout> = {}) {
  const calls: string[] = [];
  const bodies: LayoutBody[] = [];
  let current = server(initial);
  let fail: unknown = null;
  const client = {
    layout: async () => { calls.push('get'); if (fail) throw fail; return current; },
    saveLayout: async (b: LayoutBody) => {
      calls.push('put'); bodies.push(b);
      if (fail) throw fail;
      current = { ...current, source: 'personal', version: b.version + 1 };
      return current;
    },
    resetLayout: async () => { calls.push('reset'); if (fail) throw fail; current = { ...current, source: current.has_default ? 'default' : 'none', version: current.version + 1, nodes: [] }; return current; },
    saveDefaultLayout: async (b: LayoutBody) => { calls.push('putdefault'); if (fail) throw fail; current = { ...current, has_default: true, default_version: b.version + 1 }; return current; },
    removeDefaultLayout: async () => { calls.push('rmdefault'); if (fail) throw fail; current = { ...current, has_default: false, default_version: current.default_version + 1 }; return current; },
  };
  const timers: (() => void)[] = [];
  const cleared: unknown[] = [];
  const timer: Timer = { set: (fn) => { timers.push(fn); return timers.length; }, clear: (h) => cleared.push(h) };
  const states: SyncState[] = [];
  let clean = 0;
  const sync = new LayoutSync({ client, ids: () => ['dev-1', 'zone-1'], onState: (s) => states.push(s), onClean: () => { clean += 1; }, timer });
  return {
    sync, calls, bodies, timers, cleared, states,
    get clean() { return clean; },
    set(next: Partial<ServerLayout>) { current = { ...current, ...next }; },
    failWith(e: unknown) { fail = e; },
  };
}
const flushTimers = async (h: ReturnType<typeof harness>) => { const fns = h.timers.splice(0); for (const f of fns) f(); await new Promise((r) => setImmediate(r)); };
const conflict = () => new ApiError('http', 409, 'veraltet', false, 'conflict', 'version');
const offline = () => new ApiError('network', 0, 'weg');

test('Laden: Serverstand kommt zurück, Rechte und Vorgabe werden gemerkt', async () => {
  const h = harness({ source: 'default', has_default: true, may_set_default: true, version: 0, default_version: 3, nodes: [{ key: 'dev-1', x: 1, y: 1, collapsed: false }] });
  const result = await h.sync.load(prefs());
  assert.deepEqual(result?.pos, { 'dev-1': { x: 1, y: 1 } });
  assert.equal(h.sync.state.source, 'default');
  assert.equal(h.sync.state.hasDefault, true);
  assert.equal(h.sync.state.mayDefault, true);
  assert.deepEqual(h.calls, ['get']);
});

test('Laden ohne Netz: lokaler Stand bleibt, Hinweis nennt es', async () => {
  const h = harness();
  h.failWith(offline());
  assert.equal(await h.sync.load(prefs()), null);
  assert.equal(h.sync.state.status, 'offline');
  assert.match(h.sync.state.message!, /nicht erreichbar/);
  h.failWith(new ApiError('http', 500, ''));
  await h.sync.load(prefs());
  assert.equal(h.sync.state.status, 'error');
  assert.match(h.sync.state.message!, /nicht gespeichert/);
});

test('Laden mit ungesendeter lokaler Anordnung schickt sie sofort hinaus', async () => {
  const h = harness({ version: 2 });
  await h.sync.load(prefs({ pos: { 'dev-1': { x: 4, y: 5 } }, dirty: true }));
  await flushTimers(h);
  assert.deepEqual(h.calls, ['get', 'put']);
  assert.equal(h.bodies[0].version, 2);
  assert.equal(h.clean, 1);
});

test('Änderungen werden entprellt: ein Aufruf, der letzte Stand, Version läuft weiter', async () => {
  const h = harness({ version: 1 });
  await h.sync.load(prefs());
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 1, y: 1 } }, dirty: true }));
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 2, y: 2 } }, dirty: true }));
  assert.equal(h.cleared.length, 1); // der erste Timer wurde ersetzt
  assert.equal(h.sync.state.status, 'pending');
  await flushTimers(h);
  assert.deepEqual(h.calls, ['get', 'put']);
  assert.deepEqual(h.bodies[0].nodes, [{ key: 'dev-1', x: 2, y: 2, collapsed: false }]);
  assert.equal(h.sync.state.status, 'saved');
  assert.equal(h.clean, 1);
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 3, y: 3 } }, dirty: true }));
  await flushTimers(h);
  assert.equal(h.bodies[1].version, 2);
});

test('Fehler beim Speichern: Hinweis, nichts wird gelöscht oder wiederholt', async () => {
  const h = harness();
  await h.sync.load(prefs());
  h.failWith(offline());
  h.sync.changed(prefs({ dirty: true }));
  await flushTimers(h);
  assert.equal(h.sync.state.status, 'offline');
  assert.equal(h.clean, 0);
  assert.equal(h.calls.filter((c) => c === 'put').length, 1);
});

test('Konflikt: Hinweis, kein stilles Überschreiben, danach Neu laden oder Meine behalten', async () => {
  const h = harness({ version: 1 });
  await h.sync.load(prefs());
  h.failWith(conflict());
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 1, y: 1 } }, dirty: true }));
  await flushTimers(h);
  assert.equal(h.sync.state.status, 'conflict');
  assert.match(h.sync.state.message!, /nicht verloren/);
  // Solange nicht entschieden ist, geht nichts hinaus.
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 2, y: 2 } }, dirty: true }));
  await flushTimers(h);
  assert.equal(h.calls.filter((c) => c === 'put').length, 1);

  // Meine behalten: neue Version lesen, dann mit ihr speichern.
  h.failWith(null);
  h.set({ version: 7 });
  await h.sync.keepMine();
  assert.equal(h.bodies.at(-1)!.version, 7);
  assert.deepEqual(h.bodies.at(-1)!.nodes, [{ key: 'dev-1', x: 2, y: 2, collapsed: false }]);
  assert.equal(h.sync.state.status, 'saved');
});

test('Konflikt: Neu laden übernimmt den Server und verwirft die lokale Änderung', async () => {
  const h = harness({ version: 1 });
  await h.sync.load(prefs());
  h.failWith(conflict());
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 1, y: 1 } }, dirty: true }));
  await flushTimers(h);
  h.failWith(null);
  h.set({ source: 'personal', version: 4, nodes: [{ key: 'zone-1', x: 9, y: 9, collapsed: false }] });
  const adopted = await h.sync.adoptServer(true);
  assert.deepEqual(adopted?.pos, { 'zone-1': { x: 9, y: 9 } });
  assert.equal(adopted?.technical, true);
  assert.equal(h.sync.state.status, 'idle');
  assert.equal(h.clean, 1);
  h.failWith(offline());
  assert.equal(await h.sync.adoptServer(false), null);
  assert.equal(h.sync.state.status, 'offline');
  await h.sync.keepMine();
  assert.equal(h.sync.state.status, 'offline');
});

test('Änderung während des Speicherns: danach wird nachgespeichert', async () => {
  const h = harness();
  await h.sync.load(prefs());
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 1, y: 1 } }, dirty: true }));
  const first = flushTimers(h);
  h.sync.changed(prefs({ pos: { 'dev-1': { x: 2, y: 2 } }, dirty: true })); // vor der Antwort
  await first;
  await flushTimers(h);
  assert.deepEqual(h.bodies.map((b) => b.nodes[0].x), [1, 2]);
  assert.equal(h.clean, 1); // erst der letzte Stand ist bestätigt
});

test('Zurücksetzen: Timer weg, Server gefragt, Stand von Vorgabe oder leer', async () => {
  const h = harness({ has_default: true, source: 'personal' });
  await h.sync.load(prefs());
  h.sync.changed(prefs({ dirty: true }));
  const back = await h.sync.reset(true);
  assert.equal(h.cleared.length >= 1, true);
  assert.equal(h.sync.state.source, 'default');
  assert.equal(back?.technical, true);
  assert.equal(h.clean, 1);
  await flushTimers(h);
  assert.equal(h.calls.includes('put'), false); // die ausstehende Änderung ist verworfen
  h.failWith(offline());
  assert.equal(await h.sync.reset(false), null);
  assert.equal(h.sync.state.status, 'offline');
});

test('Vorgabe speichern und entfernen: eigene Version, Fehler werden gemeldet', async () => {
  const h = harness({ may_set_default: true, default_version: 2 });
  await h.sync.load(prefs());
  assert.equal(await h.sync.saveDefault(prefs({ pos: { 'dev-1': { x: 1, y: 1 } } })), true);
  assert.equal(h.sync.state.hasDefault, true);
  assert.equal(await h.sync.removeDefault(), true);
  assert.equal(h.sync.state.hasDefault, false);
  h.failWith(conflict());
  assert.equal(await h.sync.saveDefault(prefs()), false);
  assert.match(h.sync.state.message!, /Vorgabe wurde inzwischen geändert/);
  h.failWith(offline());
  assert.equal(await h.sync.removeDefault(), false);
  assert.equal(h.sync.state.status, 'offline');
  assert.equal(await h.sync.saveDefault(prefs()), false);
});

test('Verlassen: ausstehende Änderung geht noch hinaus, bei Konflikt nicht', async () => {
  const h = harness();
  await h.sync.load(prefs());
  h.sync.changed(prefs({ dirty: true }));
  h.sync.dispose();
  await new Promise((r) => setImmediate(r));
  assert.equal(h.calls.includes('put'), true);
  const c = harness();
  await c.sync.load(prefs());
  c.failWith(conflict());
  c.sync.changed(prefs({ dirty: true }));
  await flushTimers(c);
  c.sync.dispose();
  assert.equal(c.calls.filter((x) => x === 'put').length, 1);
  await c.sync.flush(); // nichts zu tun im Konflikt
  const idle = harness();
  await idle.sync.flush(); // nichts vorgemerkt
  assert.deepEqual(idle.calls, []);
});
