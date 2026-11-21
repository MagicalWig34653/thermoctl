import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { lstat, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { serveHealth } from '../health.mjs';
import { temp } from './fixtures.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

async function run(script, env) {
    const child = spawn(process.execPath, [join(root, script)], {
        env: { PATH: process.env.PATH, ...env }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    const [code] = await once(child, 'close');
    return { code, stdout, stderr };
}

const valid = dir => ({
    MATTER_BRIDGE_MQTT_URL: 'mqtt://127.0.0.1:1',
    MATTER_BRIDGE_ZONES: '[{"id":1,"name":"Zone"}]',
    MATTER_BRIDGE_STORAGE_DIR: dir,
});

test('ungültige Konfiguration beendet den Start mit klarer Meldung, ohne Passwort und ohne stdout', async () => {
    const secret = 'geheim-0815';
    for (const env of [
        {},
        { MATTER_BRIDGE_MQTT_URL: 'kaputt', MATTER_BRIDGE_MQTT_USERNAME: 'u', MATTER_BRIDGE_MQTT_PASSWORD: secret },
        { MATTER_BRIDGE_MQTT_URL: `mqtt://u:${secret}@broker`, MATTER_BRIDGE_ZONES: '[]' },
        { MATTER_BRIDGE_MQTT_URL: 'mqtt://broker', MATTER_BRIDGE_ZONES: '[{"id":1,"name":"x"}]', MATTER_BRIDGE_MATTER_PORT: '99999', MATTER_BRIDGE_MQTT_PASSWORD: secret },
    ]) {
        const { code, stdout, stderr } = await run('main.mjs', env);
        assert.equal(code, 1);
        assert.equal(stdout, '', 'stdout ist dem Kopplungscode vorbehalten');
        assert.match(stderr, /^Konfiguration |^Datei aus /);
        assert.equal(stderr.includes(secret), false);
    }
});

test('Healthcheck: ready bei gesund, unready sonst, Fehler ohne Socket', async t => {
    const dir = await temp(t);
    const env = valid(dir);
    assert.equal((await run('healthcheck.mjs', env)).code, 1, 'ohne Socket ist die Bridge nicht gesund');
    let healthy = true;
    const close = await serveHealth(dir, () => healthy, () => {});
    t.after(close);
    assert.equal((await run('healthcheck.mjs', env)).code, 0);
    healthy = false;
    assert.equal((await run('healthcheck.mjs', env)).code, 1);
    assert.equal((await run('healthcheck.mjs', { ...env, MATTER_BRIDGE_MQTT_URL: 'kaputt' })).code, 1, 'ungültige Konfiguration ist ungesund');
});

test('ein zu langer Socket-Pfad wird mit klarer Meldung abgewiesen', async t => {
    const dir = join(await temp(t), 'x'.repeat(100));
    await assert.rejects(serveHealth(dir, () => true, () => {}), /zu lang/);
});

test('ein verwaister Socket nach hartem Abbruch wird ersetzt, eine normale Datei nie gelöscht', async t => {
    const dir = await temp(t);
    const socketPath = join(dir, 'health.sock');
    const child = spawn(process.execPath, ['-e',
        `require('net').createServer().listen(process.argv[1], () => console.log('bereit'))`, socketPath], { stdio: ['ignore', 'pipe', 'ignore'] });
    await once(child.stdout, 'data');
    child.kill('SIGKILL');
    await once(child, 'close');
    assert.ok((await lstat(socketPath)).isSocket(), 'Voraussetzung: Socket-Datei ohne Listener bleibt liegen');
    const close = await serveHealth(dir, () => true, () => {});
    assert.equal((await run('healthcheck.mjs', valid(dir))).code, 0);
    await close();
    await writeFile(socketPath, 'Nutzerdaten');
    await assert.rejects(serveHealth(dir, () => true, () => {}));
    assert.equal(await readFile(socketPath, 'utf8'), 'Nutzerdaten');
});
