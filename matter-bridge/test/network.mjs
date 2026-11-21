// Stufe 2: echter OS-UDP-Stack, ausschliesslich auf Loopback. Aufruf: npm run test:network
//
// Nichts darf ins LAN. Deshalb startet dieses Skript sich selbst unter macOS in einer
// sandbox-exec-Huelle, die jedes Netzwerk ausser localhost verbietet (auch mDNS-Multicast an
// 224.0.0.251/ff02::fb). Ohne Huelle laeuft der Test nicht an: auf anderen Systemen verweigert
// er den Start, statt ungeschuetzt zu senden.
//
// Was das beweist und was nicht, steht im README (Abschnitt "Tests") und im Kommentar am Ende.
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const self = fileURLToPath(import.meta.url);
const GUARD = 'MATTER_BRIDGE_NETWORK_TEST_ISOLATED';

if (process.env[GUARD] !== '1') {
    const profile = '(version 1) (allow default) (deny network*) '
        + '(allow network-bind (local ip "localhost:*")) (allow network-inbound (local ip "localhost:*")) '
        + '(allow network-outbound (remote ip "localhost:*")) '
        // Unix-Sockets sind rein lokal (Healthcheck-Socket); sie verlassen den Rechner nie.
        + '(allow network-bind (local unix-socket)) (allow network-inbound (local unix-socket)) (allow network-outbound (remote unix-socket))';
    if (process.platform !== 'darwin') {
        console.error('test:network ist nur unter macOS mit sandbox-exec freigegeben. Auf anderen Systemen gibt es hier keine '
            + 'geprüfte Netzisolation; die Bridge-Prüfung im echten Netz bleibt eine Geräteprüfung (README).');
        process.exit(2);
    }
    const child = spawn('/usr/bin/sandbox-exec', ['-p', profile, process.execPath, self], {
        stdio: 'inherit', env: { ...process.env, [GUARD]: '1' },
    });
    const [code] = await once(child, 'close');
    process.exit(code ?? 1);
}

// ---- ab hier: Prozess laeuft in der Huelle ----
import assert from 'node:assert/strict';
import dgram from 'node:dgram';
import net from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { test } from 'node:test';
import { Aedes } from 'aedes';
import { connectAsync } from 'mqtt';
import { Boot, ControllerBehavior, Environment, Filesystem, Logger, Seconds, ServerNode, StorageService } from '@matter/main';
import { NodeJsFilesystem, SqliteStorageDriver } from '@matter/nodejs';
import { ManualPairingCodeCodec } from '@matter/types';
import { simulate } from './simulator.mjs';

const root = join(dirname(self), '..');
const iface = 'lo0';

// Zweite Absicherung: Die Huelle muss ein Ziel ausserhalb von localhost verweigern. TEST-NET-1
// (RFC 5737) ist nicht geroutet; ohne Huelle wuerde hoechstens ein SYN ins Leere gehen.
async function assertIsolated() {
    const result = await new Promise(resolve => {
        const socket = net.connect({ host: '192.0.2.1', port: 9, timeout: 1000 });
        socket.once('error', error => resolve(error.code));
        socket.once('timeout', () => { socket.destroy(); resolve('TIMEOUT'); });
        socket.once('connect', () => { socket.destroy(); resolve('CONNECT'); });
    });
    assert.equal(result, 'EPERM', `Netzisolation wirkt nicht (${result}). Abbruch, bevor etwas gesendet wird`);
}

async function freeUdpPort() {
    const socket = dgram.createSocket('udp4');
    socket.bind(0, '127.0.0.1');
    await once(socket, 'listening');
    const { port } = socket.address();
    await new Promise(resolve => socket.close(resolve));
    return port;
}

function run(env) {
    const child = spawn(process.execPath, [join(root, 'main.mjs')], {
        env: { PATH: process.env.PATH, MATTER_MDNS_NETWORKINTERFACE: iface, ...env }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const out = { stdout: '', stderr: '', child };
    child.stdout.on('data', chunk => { out.stdout += chunk; });
    child.stderr.on('data', chunk => { out.stderr += chunk; });
    out.exited = once(child, 'close').then(([code]) => code);
    return out;
}

async function until(predicate, message, timeout = 20_000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (await predicate()) return;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(typeof message === 'function' ? message() : message);
}

test('echter Prozess auf Loopback-UDP: Start, Kopplungscode einmalig, Healthcheck, PASE bis AddNOC, Neustart', { timeout: 120_000 }, async t => {
    await assertIsolated();
    await Boot.ready;
    Logger.level = 'fatal';
    const dir = await mkdtemp(join(tmpdir(), 'tcn-'));
    t.after(() => rm(dir, { recursive: true, force: true }));

    const broker = await Aedes.createBroker();
    const server = net.createServer(broker.handle);
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const mqtt = await connectAsync(`mqtt://127.0.0.1:${server.address().port}`, { reconnectPeriod: 0 });
    t.after(async () => { await mqtt.endAsync(true); await broker.close(); await new Promise(resolve => server.close(resolve)); });
    const simulator = await simulate(mqtt, 'net/test', [
        { id: 5, current_temperature: '20.5', setpoint: '21.0', operating_mode: 'auto', would_heat: 'false', override_active: 'false' },
    ]);

    const port = await freeUdpPort();
    const env = {
        MATTER_BRIDGE_MQTT_URL: `mqtt://127.0.0.1:${server.address().port}`,
        MATTER_BRIDGE_TOPIC_PREFIX: 'net/test',
        MATTER_BRIDGE_ZONES: '[{"id":5,"name":"Netzwerktest"}]',
        MATTER_BRIDGE_STORAGE_DIR: join(dir, 'bridge'),
        MATTER_BRIDGE_MATTER_PORT: String(port),
        MATTER_BRIDGE_LOG_LEVEL: 'info',
    };
    const healthy = () => new Promise(resolve => {
        const probe = spawn(process.execPath, [join(root, 'healthcheck.mjs')], { env: { PATH: process.env.PATH, ...env } });
        probe.on('close', code => resolve(code === 0));
    });

    // --- erster Start
    const first = run(env);
    t.after(() => first.child.kill('SIGKILL'));
    await until(() => /Matter-Bridge gestartet/.test(first.stderr), () => `Bridge startet nicht: ${first.stderr.slice(-400)}`);
    const code = first.stdout.match(/^Matter-Kopplungscode: (\d{11})$/m)?.[1];
    assert.ok(code, 'Kopplungscode fehlt auf stdout');
    assert.equal(first.stdout.split('\n').filter(Boolean).length, 1, 'auf stdout steht ausschliesslich der Code');
    assert.equal(first.stderr.includes(code), false, 'Code darf nicht in stderr stehen');
    await until(() => /MQTT verbunden/.test(first.stderr), 'MQTT nicht verbunden');
    await until(healthy, 'Healthcheck wird nicht gesund');
    await simulator.refresh();
    await until(() => /Zone 5: erreichbar/.test(first.stderr), 'Zone wird nicht erreichbar');

    // --- Controller im Testprozess, echtes UDP auf Loopback. mDNS ist durch die Huelle blockiert,
    // deshalb gibt der Test die Adresse vor. PASE, Attestierung, CSR und AddNOC laufen ueber den
    // echten Socket-Stack; der operative Schritt (CASE) braucht mDNS und scheitert erwartbar.
    Environment.default.vars.set('mdns.networkInterface', iface);
    const env2 = new Environment('net-controller', Environment.default);
    env2.set(Filesystem, new NodeJsFilesystem(join(dir, 'controller')));
    const driver = new StorageService(env2);
    driver.registerDriver(SqliteStorageDriver);
    driver.defaultDriver = 'sqlite';
    const controller = await ServerNode.create(ServerNode.RootEndpointWithoutGroupcast.with(ControllerBehavior), {
        id: 'net-controller', environment: env2, network: { port: await freeUdpPort() }, commissioning: { enabled: false },
    });
    t.after(() => controller.close());
    await controller.start();
    const address = { type: 'udp', ip: '127.0.0.1', port };
    const node = await controller.peers.forDescriptor({ addresses: [address], operationalAddress: address, deviceIdentifier: 'bridge' });
    const error = await node.commission({ passcode: ManualPairingCodeCodec.decode(code).passcode, caseConnectionTimeout: Seconds(4) })
        .then(() => null, e => e);
    assert.ok(error, 'Ohne mDNS kann der operative Schritt hier nicht gelingen');
    assert.match(error.message, /Operative reconnection/, `Kopplung muss erst am operativen Schritt scheitern: ${error.message}`);
    await controller.close();

    // --- sauberes Beenden und Neustart mit demselben Speicher
    first.child.kill('SIGTERM');
    assert.equal(await first.exited, 0, 'SIGTERM beendet die Bridge sauber');
    assert.equal(first.stdout.match(/Matter-Kopplungscode/g).length, 1);
    const second = run(env);
    t.after(() => second.child.kill('SIGKILL'));
    await until(() => /Matter-Bridge gestartet/.test(second.stderr), () => `Neustart schlaegt fehl: ${second.stderr.slice(-400)}`);
    assert.equal(second.stdout, '', 'Kein zweiter Kopplungscode');
    await until(healthy, 'Healthcheck nach Neustart');
    second.child.kill('SIGTERM');
    assert.equal(await second.exited, 0);
});
