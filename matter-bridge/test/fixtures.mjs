import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { Aedes } from 'aedes';
import { connectAsync } from 'mqtt';
import { simulate } from './simulator.mjs';

export const zoneFixtures = [
    { id: 11, name: 'Testzone A' },
    { id: 22, name: 'Testzone B' },
];
export const prefix = 'test/bridge';
export const statesPerZone = 5;

// Kurzer Pfad unter dem Systemtemp: Der Healthcheck-Socket braucht < 100 Bytes.
export async function temp(t) {
    const path = await mkdtemp(join(tmpdir(), 'tcb-'));
    t.after(() => rm(path, { recursive: true, force: true, maxRetries: 5 }));
    return path;
}

export async function until(predicate, message, timeout = 4500) {
    const start = performance.now();
    do {
        const result = await predicate();
        if (result) return result;
        await pause(20);
    } while (performance.now() - start < timeout);
    assert.fail(message);
}

export const command = (id, key, payload) =>
    ({ topic: `${prefix}/zones/${id}/command/${key}`, payload, qos: 1, retain: false });

const allowedPublishes = /^test\/bridge\/zones\/\d+\/command\/(temporary_setpoint|operating_mode)$/;

// Echter MQTT-Broker (Aedes) auf 127.0.0.1 mit zufaelligem Port. Er zeichnet alles auf, was ein
// Bridge-Client sendet und abonniert -- die Tests pruefen die Leitung, nicht den Bridge-Code.
export async function loopbackBroker(t, fixtures, { zoneCount = 2 } = {}) {
    const broker = await Aedes.createBroker();
    const server = createServer(broker.handle);
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const url = `mqtt://127.0.0.1:${server.address().port}`;
    const mqtt = await connectAsync(url, { reconnectPeriod: 0, clientId: 'thermoctl-simulator' });
    const publishes = [];
    const subscriptions = new Map();
    const connects = [];
    let holdCommandAcks = false;
    broker.on('clientReady', client => {
        if (client.id === 'thermoctl-simulator') return;
        subscriptions.set(client, new Set());
        connects.push(client.id);
    });
    broker.on('subscribe', (list, client) => {
        for (const { topic } of list) subscriptions.get(client)?.add(topic);
    });
    broker.on('clientDisconnect', client => subscriptions.delete(client));
    broker.authorizePublish = (client, packet, callback) => {
        if (client && subscriptions.has(client)) {
            publishes.push({ topic: packet.topic, payload: packet.payload.toString(), qos: packet.qos, retain: packet.retain });
            if (holdCommandAcks) return;
        }
        callback(null);
    };
    t.after(async () => {
        await mqtt.endAsync(true);
        await broker.close();
        await new Promise(resolve => server.close(resolve));
    });
    const simulator = await simulate(mqtt, prefix, fixtures);
    const subscribed = () => [...subscriptions.values()].flatMap(set => [...set]);
    return {
        // Alles, was ein Bridge-Client veroeffentlicht hat. Ausser den zwei erlaubten Befehlen darf nichts darin stehen.
        commands: publishes,
        publishes,
        subscribed,
        connects,
        simulator,
        url,
        connected: () => subscribed().length >= zoneCount * statesPerZone,
        dropConnection: () => {
            assert.equal(subscriptions.size, 1);
            [...subscriptions.keys()][0].conn.destroy();
        },
        holdAcks: value => { holdCommandAcks = value; },
        // Die Bridge darf ausschliesslich diese zwei Befehlsarten senden -- nie command/setpoint.
        assertWireDiscipline() {
            for (const { topic } of publishes) assert.match(topic, allowedPublishes, `Unerlaubtes Topic ${topic}`);
            for (const topic of subscribed()) {
                assert.match(topic, /^test\/bridge\/zones\/\d+\/state\/(current_temperature|setpoint|operating_mode|would_heat|override_active)$/,
                    `Unerlaubtes Abonnement ${topic}`);
            }
        },
    };
}
