import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, lstat, mkdir, readFile, readdir, rm, truncate, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { temp, zoneFixtures } from './fixtures.mjs';
import { Logger, config, virtualHost } from './matter.mjs';

const { describe, startBridge } = await import('../service.mjs');
const log = await import('../log.mjs');
const { StorageError } = await import('../storage.mjs');
await virtualHost(11);

const URL = 'mqtt://127.0.0.1:1';
const quiet = { output: () => {}, warning: () => {}, info: () => {} };

async function snapshot(dir) {
    const files = {};
    async function walk(path) {
        for (const entry of await readdir(path, { withFileTypes: true })) {
            const full = join(path, entry.name);
            if (entry.isDirectory()) await walk(full);
            // SQLite legt beim Lesen leere -wal/-shm-Dateien an; sie enthalten keine Daten.
            else if (entry.isFile() && entry.name !== 'instance-lock' && !/-(wal|shm)$/.test(entry.name)) files[full] = createHash('sha256').update(await readFile(full)).digest('hex');
        }
    }
    await walk(dir);
    return files;
}

async function paired(t, zones = zoneFixtures) {
    const dir = join(await temp(t), 'bridge');
    const codes = [];
    const bridge = await startBridge(config(dir, URL, zones), { ...quiet, output: code => codes.push(code) });
    const identity = { passcode: bridge.node.state.commissioning.passcode, discriminator: bridge.node.state.commissioning.discriminator };
    await bridge.close();
    return { dir, codes, identity };
}

// Jede Beschaedigung muss klar abbrechen und darf nichts veraendern: Matter.js wuerde fehlende
// Zugangsdaten sonst neu wuerfeln und damit still eine neue Kopplung beginnen, die jede
// bestehende Fabric entwertet.
async function refuses(t, damage, pattern) {
    const { dir } = await paired(t);
    await damage(dir);
    const before = await snapshot(dir);
    await assert.rejects(startBridge(config(dir, URL), quiet), error => {
        assert.ok(error instanceof StorageError, `StorageError erwartet, erhalten: ${error}`);
        assert.match(error.message, pattern);
        return true;
    });
    assert.deepEqual(await snapshot(dir), before, 'Die Bridge darf beschädigten Speicher nicht anfassen');
    return dir;
}

test('der Kopplungscode erscheint beim ersten Start genau einmal und nie wieder', async t => {
    const { dir, codes } = await paired(t);
    assert.equal(codes.length, 1);
    assert.match(codes[0], /^\d{11}$/);
    for (let run = 0; run < 2; run++) {
        const again = [];
        const bridge = await startBridge(config(dir, URL), { ...quiet, output: code => again.push(code) });
        assert.equal(bridge.node.lifecycle.isCommissioned, false);
        await bridge.close();
        assert.deepEqual(again, [], 'Kein zweiter Kopplungscode, auch nicht ohne Kopplung');
    }
});

test('Passcode, Discriminator und Instanzschlüssel bleiben über Neustarts erhalten', async t => {
    const { dir, identity } = await paired(t);
    const key = JSON.parse(await readFile(join(dir, 'instance.json'), 'utf8')).key;
    assert.match(key, /^[a-f0-9]{64}$/);
    const bridge = await startBridge(config(dir, URL), quiet);
    assert.deepEqual({ passcode: bridge.node.state.commissioning.passcode, discriminator: bridge.node.state.commissioning.discriminator }, identity);
    await bridge.close();
    assert.equal(JSON.parse(await readFile(join(dir, 'instance.json'), 'utf8')).key, key);
});

test('Endpoint-Nummern werden nie wiederverwendet, UniqueIDs hängen am Instanzschlüssel und der Zonen-ID', async t => {
    const dir = join(await temp(t), 'bridge');
    const run = async ids => {
        const bridge = await startBridge(config(dir, URL, ids.map(id => ({ id, name: `Zone ${id}` }))), quiet);
        const result = new Map([...bridge.zones].map(([id, { endpoint }]) => [id, [endpoint.number, endpoint.state.bridgedDeviceBasicInformation.uniqueId]]));
        await bridge.close();
        return result;
    };
    const first = await run([1, 2]);
    const second = await run([2]);
    const third = await run([3, 1, 2]);
    assert.deepEqual(first.get(1)[0], 2);
    assert.deepEqual(first.get(2)[0], 3);
    assert.deepEqual(second.get(2), first.get(2));
    assert.equal(third.get(3)[0], 4, 'Neue Zone bekommt eine neue Nummer, nicht die der entfernten');
    assert.deepEqual(third.get(1), first.get(1));
    assert.deepEqual(third.get(2), first.get(2));
    const ids = [...third.values()].map(([, uniqueId]) => uniqueId);
    assert.equal(new Set(ids).size, 3);
    for (const id of ids) assert.match(id, /^[a-f0-9]{32}$/);
    // Eine zweite Installation (anderer Schluessel) liefert fuer dieselbe Zonen-ID eine andere UniqueID.
    const other = join(await temp(t), 'bridge');
    const bridge = await startBridge(config(other, URL, [{ id: 1, name: 'Zone 1' }]), quiet);
    assert.notEqual(bridge.zones.get(1).endpoint.state.bridgedDeviceBasicInformation.uniqueId, first.get(1)[1]);
    await bridge.close();
    // Weder Name noch Anzeigename gehen in die Identitaet ein: Umbenennen aendert nichts daran.
    const renamed = await startBridge(config(dir, URL, [{ id: 1, name: 'Ganz anders' }]), quiet);
    assert.equal(renamed.zones.get(1).endpoint.state.bridgedDeviceBasicInformation.uniqueId, first.get(1)[1]);
    assert.equal(renamed.zones.get(1).endpoint.state.bridgedDeviceBasicInformation.nodeLabel, 'Ganz anders');
    await renamed.close();
});

test('beschädigtes Identitätsregister bricht ab und lässt alles unangetastet', async t => {
    await refuses(t, dir => writeFile(join(dir, 'instance.json'), '{kaputt'), /beschädigt/);
    await refuses(t, dir => writeFile(join(dir, 'instance.json'), '{"version":1,"key":"zu kurz","allocations":[],"codeIssued":false,"pairingFingerprint":"x"}'), /beschädigt/);
    await refuses(t, dir => writeFile(join(dir, 'instance.json'), JSON.stringify({ version: 1, key: 'a'.repeat(64), pairingFingerprint: 'b'.repeat(64), codeIssued: false, allocations: [3, 3] })), /beschädigt/);
});

test('fehlendes Identitätsregister bei vorhandenem Matter-Speicher bricht ab', async t => {
    await refuses(t, dir => rm(join(dir, 'instance.json')), /instance\.json fehlt/);
});

test('beschädigte, fehlende oder fremde Matter-Datenbank bricht ab', async t => {
    await refuses(t, dir => writeFile(join(dir, 'thermoctl-bridge', 'storage.db'), 'kein sqlite'), /beschädigt/);
    await refuses(t, async dir => truncate(join(dir, 'thermoctl-bridge', 'storage.db'), 100), /beschädigt/);
    await refuses(t, dir => rm(join(dir, 'thermoctl-bridge', 'storage.db')), /beschädigt/);
    await refuses(t, dir => rm(join(dir, 'thermoctl-bridge'), { recursive: true }), /beschädigt/);
    await refuses(t, dir => writeFile(join(dir, 'thermoctl-bridge', 'driver.json'), '{"kind":"file","type":"kv"}'), /beschädigt/);
});

test('eine Datenbank mit anderer Kopplungsidentität (zurückgesetzter Speicher) wird nicht übernommen', async t => {
    const other = await paired(t);
    const victim = await paired(t);
    await writeFile(join(victim.dir, 'thermoctl-bridge', 'storage.db'), await readFile(join(other.dir, 'thermoctl-bridge', 'storage.db')));
    const before = await snapshot(victim.dir);
    await assert.rejects(startBridge(config(victim.dir, URL), quiet), error => error instanceof StorageError && /Kopplungsidentität/.test(error.message));
    assert.deepEqual(await snapshot(victim.dir), before);
});

test('nicht leeres Verzeichnis ohne Identitätsregister wird nie überschrieben', async t => {
    const dir = join(await temp(t), 'bridge');
    await mkdir(dir);
    await writeFile(join(dir, 'fremd.txt'), 'wichtig');
    await assert.rejects(startBridge(config(dir, URL), quiet), error => error instanceof StorageError && /nicht leer/.test(error.message));
    assert.equal(await readFile(join(dir, 'fremd.txt'), 'utf8'), 'wichtig');
});

test('nicht beschreibbarer Speicher bricht mit klarer Meldung ab und gibt die Sperre wieder frei', { skip: process.getuid?.() === 0 && 'als root nicht prüfbar' }, async t => {
    const { dir } = await paired(t);
    await chmod(dir, 0o500);
    t.after(() => chmod(dir, 0o700).catch(() => {}));
    const before = await snapshot(dir);
    await assert.rejects(startBridge(config(dir, URL), quiet), error => error instanceof StorageError && /nicht beschreibbar/.test(error.message));
    await chmod(dir, 0o700);
    assert.deepEqual(await snapshot(dir), before);
    // Nach der Reparatur startet dieselbe Instanz wieder: die Sperre wurde nicht festgehalten.
    const bridge = await startBridge(config(dir, URL), quiet);
    await bridge.close();
});

test('schreibgeschützte Datenbankdatei bricht ab', { skip: process.getuid?.() === 0 && 'als root nicht prüfbar' }, async t => {
    const { dir } = await paired(t);
    const db = join(dir, 'thermoctl-bridge', 'storage.db');
    await chmod(db, 0o400);
    t.after(() => chmod(db, 0o600).catch(() => {}));
    await assert.rejects(startBridge(config(dir, URL), quiet), error => error instanceof StorageError);
});

test('eine zweite Instanz auf demselben Speicher wird abgewiesen', async t => {
    const dir = join(await temp(t), 'bridge');
    const first = await startBridge(config(dir, URL), quiet);
    await assert.rejects(startBridge(config(dir, URL), quiet), error => error instanceof StorageError && /belegt|doppelt/.test(error.message));
    await first.close();
    const again = await startBridge(config(dir, URL), quiet);
    await again.close();
});

test('der Kopplungscode taucht in keinem Matter.js-Log auf, auch nicht bei debug', async t => {
    const dir = join(await temp(t), 'bridge');
    const lines = [];
    const destination = Logger.destinations.default;
    const write = destination.write;
    const level = Logger.level;
    destination.write = text => { lines.push(text); };
    Logger.level = 'debug';
    const codes = [];
    const warnings = [];
    try {
        const bridge = await startBridge(config(dir, URL), { output: code => codes.push(code), warning: m => warnings.push(m), info: m => warnings.push(m) });
        const { passcode } = bridge.node.state.commissioning;
        const { qrPairingCode, manualPairingCode } = bridge.node.state.commissioning.pairingCodes;
        await bridge.close();
        const restarted = await startBridge(config(dir, URL), quiet);
        await restarted.close();
        const everything = [...lines, ...warnings].join('\n');
        assert.ok(lines.length > 20, 'Der debug-Log sollte nicht leer sein, sonst prüft dieser Test nichts');
        assert.equal(codes[0], manualPairingCode);
        for (const secret of [String(passcode), manualPairingCode, qrPairingCode, qrPairingCode.replace(/^MT:/, '')]) {
            assert.equal(everything.includes(secret), false, 'Kopplungsdaten im Log');
        }
        assert.equal(/MT:[A-Z0-9.\-]{10,}/.test(everything), false, 'QR-Code im Log');
    } finally {
        destination.write = write;
        Logger.level = level;
    }
});

test('die Startmeldung nennt die Ursache, maskiert aber lange Ziffernfolgen und kürzt', () => {
    const message = describe(new Error('Verbindung abgelehnt', { cause: new TypeError('Port 5540 belegt, Code 20202021 und 12345678901') }));
    assert.equal(message, 'TypeError: Port 5540 belegt, Code *** und ***');
    assert.equal(describe(new Error('x'.repeat(1000))).length, 200);
    assert.equal(describe(undefined), 'Error: ');
});

const mode = async path => (await lstat(path)).mode & 0o777;

test('Speicherrechte: Verzeichnis 0700, Register 0600 unabhängig von der umask; mit umask 077 nichts für Gruppe oder andere zugänglich', async t => {
    const old = process.umask(0o022);
    t.after(() => process.umask(old));
    // 1) Großzügige umask: die explizit gesetzten Modi müssen trotzdem gelten.
    const open = join(await temp(t), 'bridge');
    const first = await startBridge(config(open, URL), quiet);
    await first.close();
    assert.equal(await mode(open), 0o700);
    assert.equal(await mode(join(open, 'instance.json')), 0o600);
    const again = await startBridge(config(open, URL), quiet);
    await again.close();
    assert.equal(await mode(join(open, 'instance.json')), 0o600, 'atomares Ersetzen behält den Modus');
    // 2) umask 077 wie in main.mjs: auch alle Matter.js/SQLite-Dateien sind privat (am echten Prozess in test:network).
    process.umask(0o077);
    const dir = join(await temp(t), 'bridge');
    const bridge = await startBridge(config(dir, URL), quiet);
    await bridge.close();
    async function walk(path) {
        for (const entry of await readdir(path, { withFileTypes: true })) {
            const full = join(path, entry.name);
            assert.equal(await mode(full) & 0o077, 0, `${full} ist für Gruppe oder andere zugänglich`);
            if (entry.isDirectory()) await walk(full);
        }
    }
    await walk(dir);
});

test('Standard-Loglevel: stdout enthält ausschließlich den Kopplungscode, Meldungen und Matter.js-Logs gehen nach stderr', async t => {
    const dir = join(await temp(t), 'bridge');
    const stdout = [];
    const stderr = [];
    const realOut = process.stdout.write;
    const realErr = process.stderr.write;
    const level = Logger.level;
    const destination = Logger.destinations.default;
    const write = destination.write;
    process.stdout.write = (chunk, ...rest) => { stdout.push(String(chunk)); rest.find(f => typeof f === 'function')?.(); return true; };
    process.stderr.write = (chunk, ...rest) => { stderr.push(String(chunk)); rest.find(f => typeof f === 'function')?.(); return true; };
    try {
        log.routeMatterLogsToStderr(Logger);
        Logger.level = 'debug';
        // Genau die Senken, die main.mjs verwendet.
        const bridge = await startBridge(config(dir, URL), {
            warning: log.warning, info: log.info,
            output: code => new Promise(resolve => process.stdout.write(`Matter-Kopplungscode: ${code}\n`, resolve)),
        });
        log.info('Probe-Info');
        log.warning('Probe-Warnung');
        await bridge.close();
    } finally {
        process.stdout.write = realOut;
        process.stderr.write = realErr;
        destination.write = write;
        Logger.level = level;
    }
    assert.equal(stdout.length, 1, `stdout: ${stdout.join('|').slice(0, 300)}`);
    assert.match(stdout[0], /^Matter-Kopplungscode: \d{11}\n$/);
    assert.ok(stderr.join('').length > 500, 'Matter.js-debug-Ausgabe und Bridge-Meldungen müssen auf stderr stehen');
    assert.match(stderr.join(''), /NOTICE Node thermoctl-bridge is online/, 'Matter.js-Log fehlt auf stderr');
    assert.match(stderr.join(''), /Probe-Info\nProbe-Warnung\n/, 'Bridge-Meldungen fehlen auf stderr');
    assert.equal(stderr.join('').includes(stdout[0].slice(-12, -1)), false, 'Der Code steht nie auf stderr');
});
