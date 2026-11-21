import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { test } from 'node:test';
import { Aedes } from 'aedes';
import { connectAsync } from 'mqtt';
import { connectMqtt } from '../mqtt.mjs';
import { until } from './fixtures.mjs';

async function freePort() {
    const server = createServer();
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const { port } = server.address();
    await new Promise(resolve => server.close(resolve));
    return port;
}

async function startBroker(port, hooks = {}) {
    const broker = await Aedes.createBroker();
    Object.assign(broker, hooks);
    const server = createServer(broker.handle);
    server.listen(port, '127.0.0.1');
    await once(server, 'listening');
    return { broker, close: async () => { await broker.close(); await new Promise(resolve => server.close(resolve)); } };
}

const recorder = () => {
    const warnings = [];
    const infos = [];
    return { warnings, infos, log: { warning: m => warnings.push(m), info: m => infos.push(m) } };
};

test('Broker beim Start nicht erreichbar: eine Warnung, dann Verbindung, Abo und Neuabgleich ohne Neustart', async t => {
    const port = await freePort();
    const received = [];
    const resets = [];
    const { warnings, log } = recorder();
    const routes = new Map([['t/zones/1/state/setpoint', payload => received.push(payload.toString())]]);
    const client = connectMqtt({ mqttUrl: `mqtt://127.0.0.1:${port}` }, routes, () => resets.push(performance.now()), log);
    t.after(() => client.close());
    await until(() => warnings.length >= 1, 'Keine Warnung bei fehlendem Broker');
    await new Promise(resolve => setTimeout(resolve, 2500));
    assert.equal(warnings.length, 1, 'Die Warnung darf sich nicht je Versuch wiederholen');
    assert.match(warnings[0], /^MQTT-Verbindung fehlgeschlagen \(\w+\)/);
    assert.equal(client.connected, false);
    client.publish('t/zones/1/command/operating_mode', 'off');

    const { broker, close } = await startBroker(port);
    t.after(close);
    await until(() => client.connected, 'Keine Verbindung nach Brokerstart', 5000);
    assert.ok(resets.length >= 2, 'reset beim Verlust und bei jeder Verbindung');
    const publisher = await connectAsync(`mqtt://127.0.0.1:${port}`);
    t.after(() => publisher.endAsync(true));
    await until(() => broker.connectedClients >= 2, 'Abonnent fehlt');
    await publisher.publishAsync('t/zones/1/state/setpoint', '21.0', { qos: 1 });
    await until(() => received.length === 1, 'Zustand kam nicht an');
    assert.equal(received[0], '21.0');
});

test('retained Nachrichten werden nicht zugestellt, andere Topics nie ausgewertet', async t => {
    const port = await freePort();
    const { broker, close } = await startBroker(port);
    t.after(close);
    const publisher = await connectAsync(`mqtt://127.0.0.1:${port}`);
    t.after(() => publisher.endAsync(true));
    await publisher.publishAsync('t/zones/1/state/setpoint', '99', { qos: 1, retain: true });
    const received = [];
    const { log } = recorder();
    const client = connectMqtt({ mqttUrl: `mqtt://127.0.0.1:${port}` },
        new Map([['t/zones/1/state/setpoint', payload => received.push(payload.toString())]]), () => {}, log);
    t.after(() => client.close());
    await until(() => broker.connectedClients >= 2, 'Bridge nicht verbunden');
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.deepEqual(received, [], 'Retained-Wert aus der Zeit vor der Verbindung darf nicht als frisch gelten');
    await publisher.publishAsync('t/zones/1/state/setpoint', '20', { qos: 1 });
    await until(() => received.length === 1, 'Live-Wert fehlt');
});

test('falsche Zugangsdaten und verweigerte Abonnements werden gemeldet, ohne das Passwort zu nennen', async t => {
    const port = await freePort();
    const secret = 'sehr-geheim-4711';
    const { close } = await startBroker(port, {
        authenticate: (client, user, pass, callback) => callback(null, user === 'bridge' && pass?.toString() === 'richtig'),
        authorizeSubscribe: (client, subscription, callback) => callback(null, subscription.topic.endsWith('/allowed') ? subscription : null),
    });
    t.after(close);
    const wrong = recorder();
    const bad = connectMqtt({ mqttUrl: `mqtt://127.0.0.1:${port}`, mqttUsername: 'bridge', mqttPassword: secret },
        new Map([['t/allowed', () => {}]]), () => {}, wrong.log);
    t.after(() => bad.close());
    await until(() => wrong.warnings.length >= 1, 'Keine Warnung bei falschem Passwort');
    assert.equal(bad.connected, false);
    assert.equal(wrong.warnings.join('\n').includes(secret), false);

    const acl = recorder();
    const denied = connectMqtt({ mqttUrl: `mqtt://127.0.0.1:${port}`, mqttUsername: 'bridge', mqttPassword: 'richtig' },
        new Map([['t/forbidden', () => {}], ['t/allowed', () => {}]]), () => {}, acl.log);
    t.after(() => denied.close());
    await until(() => acl.warnings.some(m => /Abonnement fehlgeschlagen.*ACL/.test(m)), 'Keine Warnung bei verweigertem Abonnement');
});
