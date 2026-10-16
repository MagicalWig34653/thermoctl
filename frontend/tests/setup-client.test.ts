import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApi } from '../src/lib/api.ts';
import {
  LIMITS,
  TOKEN_REFUSED,
  loadSetupStatus,
  submitSetup,
  suggestedTimezone,
  validateLocal,
  type SetupForm,
} from '../src/lib/setup/client.ts';

const FORM: SetupForm = {
  setup_token: ' tok ',
  username: ' lino ',
  display_name: ' Lino ',
  password: ' passwort-lang-genug ',
  timezone: ' Europe/Berlin ',
};

function fakeApi(status: number, body: unknown, fail = false) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchFake = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    if (fail) throw new TypeError('offline');
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  return { api: createApi({ fetch: fetchFake, prefix: '/ing', cookies: () => '' }), calls };
}

test('loadSetupStatus: offen, abgeschlossen (404), sonst Fehler', async () => {
  assert.equal(await loadSetupStatus(fakeApi(200, { open: true }).api), 'open');
  assert.equal(await loadSetupStatus(fakeApi(200, { open: false }).api), 'error');
  assert.equal(await loadSetupStatus(fakeApi(404, { detail: 'zu' }).api), 'closed');
  assert.equal(await loadSetupStatus(fakeApi(500, {}).api), 'error');
  assert.equal(await loadSetupStatus(fakeApi(0, {}, true).api), 'error');
});

test('suggestedTimezone liefert einen nichtleeren Namen', () => {
  assert.ok(suggestedTimezone().length > 0);
});

test('validateLocal: leer und zu lang, Passwort wird nicht verändert', () => {
  assert.deepEqual(validateLocal(FORM), {});
  const empty = validateLocal({ setup_token: ' ', username: '', display_name: ' ', password: '', timezone: '' });
  assert.deepEqual(Object.keys(empty).sort(), ['display_name', 'password', 'setup_token', 'timezone', 'username']);
  const long = validateLocal({ ...FORM, username: 'x'.repeat(LIMITS.username + 1) });
  assert.equal(long.username, `Höchstens ${LIMITS.username} Zeichen.`);
  assert.equal(validateLocal({ ...FORM, display_name: 'x'.repeat(LIMITS.display_name) }).display_name, undefined);
});

test('submitSetup: getrimmt gesendet, Passwort unverändert, Erfolg', async () => {
  const { api, calls } = fakeApi(200, { ok: true });
  assert.deepEqual(await submitSetup(api, FORM), { kind: 'done' });
  assert.equal(calls[0].url, '/ing/ui-api/setup');
  assert.deepEqual(JSON.parse(calls[0].init.body as string), {
    setup_token: 'tok',
    username: 'lino',
    display_name: 'Lino',
    password: ' passwort-lang-genug ',
    timezone: 'Europe/Berlin',
  });
});

test('submitSetup: 404 heißt abgeschlossen', async () => {
  assert.deepEqual(await submitSetup(fakeApi(404, { detail: 'zu' }).api, FORM), { kind: 'closed' });
});

test('submitSetup: Feldfehler; Tokenfehler mit einheitlichem Text', async () => {
  assert.deepEqual(await submitSetup(fakeApi(403, { detail: 'Ungueltiges', field: 'setup_token' }).api, FORM), {
    kind: 'field',
    field: 'setup_token',
    message: TOKEN_REFUSED,
  });
  assert.deepEqual(await submitSetup(fakeApi(422, { detail: 'Zu kurz.', field: 'password' }).api, FORM), {
    kind: 'field',
    field: 'password',
    message: 'Zu kurz.',
  });
});

test('submitSetup: unbekanntes Feld, Listen-Fehler, Netz, veraltet und Unerwartetes werden allgemein gemeldet', async () => {
  const general = { kind: 'error', message: 'Die Einrichtung ist fehlgeschlagen.' };
  assert.deepEqual(await submitSetup(fakeApi(422, { detail: 'x', field: 'unbekannt' }).api, FORM), general);
  assert.deepEqual(await submitSetup(fakeApi(422, { detail: [{ msg: 'x' }] }).api, FORM), general);
  assert.deepEqual(await submitSetup(fakeApi(500, {}).api, FORM), general);
  assert.deepEqual(await submitSetup(fakeApi(0, {}, true).api, FORM), {
    kind: 'error',
    message: 'Der Server ist nicht erreichbar.',
  });
  const stale = await submitSetup(fakeApi(403, { detail: 'x', stale: true }).api, FORM);
  assert.equal(stale.kind, 'error');
  const broken = createApi({
    fetch: (async () => {
      throw new Error('x');
    }) as unknown as typeof fetch,
    prefix: '',
    cookies: () => {
      throw new Error('kaputt');
    },
  });
  assert.equal((await submitSetup(broken, FORM)).kind, 'error');
});
