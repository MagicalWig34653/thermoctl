// Treiber fuer tests/test_matter_bridge_integration.py (Python startet ihn als Unterprozess).
//
// Er stellt den Node-Teil des Integrationstests bereit: einen echten MQTT-Broker (Aedes) auf
// 127.0.0.1, die echte Matter-Bridge im virtuellen Matter-Netz (kein OS-UDP, kein mDNS) und einen
// Matter-Controller, der wie Apple Home liest und schreibt. thermoctl selbst ist NICHT hier -- das
// ist der Python-Teil, der sich als MQTT-Client am selben Broker anmeldet.
//
// Protokoll: Python schreibt je Zeile ein JSON-Objekt {id, op, ...} auf stdin, dieser Prozess
// antwortet je Zeile mit "@@" + JSON ({id, ok, result | error}). Zeilen ohne "@@" sind Diagnose.
// Faellt stdin weg (Python beendet), raeumt der Prozess auf und endet.
import '../test/no-udp.mjs';
import { once } from 'node:events';
import { mkdir } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { Aedes } from 'aedes';
import { readConfig } from '../config.mjs';
import { until } from '../test/fixtures.mjs';
import { createController, virtualHost } from '../test/matter.mjs';

const { Status } = await import('@matter/types');
const { startBridge } = await import('../service.mjs');

const workdir = process.argv[2];
if (!workdir) throw new Error('Arbeitsverzeichnis fehlt (argv[2])');

const emit = message => process.stdout.write(`@@${JSON.stringify(message)}\n`);
const cleanups = [];
const t = { after: fn => cleanups.push(fn) };

// Teilnehmer der Python-Seite tragen die Endung "-py" und zaehlen nicht als Bridge.
const isPython = id => String(id).endsWith('-py');

const broker = await Aedes.createBroker();
const server = createServer(broker.handle);
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const url = `mqtt://127.0.0.1:${server.address().port}`;
const clients = new Map();
const bridgePublishes = [];
broker.on('clientReady', client => clients.set(client.id, new Set()));
broker.on('subscribe', (list, client) => {
    for (const { topic } of list) clients.get(client.id)?.add(topic);
});
broker.on('clientDisconnect', client => clients.delete(client.id));
broker.authorizePublish = (client, packet, callback) => {
    if (client && !isPython(client.id)) {
        bridgePublishes.push({ topic: packet.topic, payload: packet.payload.toString(), qos: packet.qos, retain: packet.retain });
    }
    callback(null);
};
cleanups.push(async () => {
    await broker.close();
    await new Promise(resolve => server.close(resolve));
});

let bridge;
let remote;

async function startAll({ zones, prefix, minSetpoint, maxSetpoint, maxAgeSeconds = 300 }) {
    await mkdir(join(workdir, 'controller'), { recursive: true });
    await mkdir(join(workdir, 'bridge'), { recursive: true });
    const config = readConfig({
        MATTER_BRIDGE_STORAGE_DIR: join(workdir, 'bridge'),
        MATTER_BRIDGE_MQTT_URL: url,
        MATTER_BRIDGE_TOPIC_PREFIX: prefix,
        MATTER_BRIDGE_ZONES: JSON.stringify(zones),
        MATTER_BRIDGE_MIN_SETPOINT: String(minSetpoint),
        MATTER_BRIDGE_MAX_SETPOINT: String(maxSetpoint),
        MATTER_BRIDGE_MAX_AGE_SECONDS: String(maxAgeSeconds),
    });
    await virtualHost(11);
    const controller = await createController(t, join(workdir, 'controller'), 'a', 5541);
    bridge = await startBridge(config, { output: () => {}, warning: message => emit({ event: 'bridge-warning', message }) });
    cleanups.push(() => bridge.close());
    const { passcode, discriminator } = bridge.node.state.commissioning;
    const peer = await controller.peers.commission({ passcode, discriminator, timeout: 8000 });
    const endpoints = () => [...peer.parts].flatMap(part => [...part.parts]);
    await until(() => endpoints().length === zones.length, 'Thermostate fehlen im Aggregator');
    remote = new Map(zones.map(({ id }) => [id, endpoints().find(ep => ep.number === bridge.zones.get(id).endpoint.number)]));
    return { zones: zones.length };
}

const endpointOf = id => {
    const endpoint = remote?.get(id);
    if (!endpoint) throw new Error(`Unbekannte Zone ${id}`);
    return endpoint;
};

const operations = {
    async start(args) {
        return startAll(args);
    },
    async status() {
        return {
            clients: Object.fromEntries([...clients].map(([id, topics]) => [id, [...topics]])),
            bridgePublishes,
            healthy: bridge?.healthy() ?? false,
        };
    },
    async read({ zone }) {
        const endpoint = endpointOf(zone);
        const thermostat = await endpoint.getStateOf('thermostat');
        const basic = await endpoint.getStateOf('bridgedDeviceBasicInformation');
        return {
            reachable: basic.reachable,
            localTemperature: thermostat.localTemperature,
            occupiedHeatingSetpoint: thermostat.occupiedHeatingSetpoint,
            systemMode: thermostat.systemMode,
            heat: thermostat.thermostatRunningState?.heat ?? null,
            minHeat: thermostat.minHeatSetpointLimit,
            maxHeat: thermostat.maxHeatSetpointLimit,
        };
    },
    async write({ zone, attribute, value }) {
        try {
            await endpointOf(zone).setStateOf('thermostat', { [attribute]: value });
            return { accepted: true };
        } catch (error) {
            const code = error?.code;
            const name = Object.entries(Status).find(([, number]) => number === code)?.[0] ?? null;
            return { accepted: false, code: code ?? null, name, message: String(error?.message ?? error).slice(0, 200) };
        }
    },
};

let stopping;
async function stop() {
    stopping ??= (async () => {
        for (const cleanup of cleanups.reverse()) {
            try {
                await cleanup();
            } catch {
                // Beim Beenden zaehlt, dass alles andere noch geschlossen wird.
            }
        }
    })();
    await stopping;
}

let queue = Promise.resolve();
const lines = createInterface({ input: process.stdin });
lines.on('line', line => {
    queue = queue.then(async () => {
        let request;
        try {
            request = JSON.parse(line);
        } catch {
            emit({ ok: false, error: 'Ungueltige Anfrage' });
            return;
        }
        try {
            if (request.op === 'stop') {
                await stop();
                emit({ id: request.id, ok: true, result: null });
                process.exit(0);
            }
            const handler = operations[request.op];
            if (!handler) throw new Error(`Unbekannte Operation ${request.op}`);
            emit({ id: request.id, ok: true, result: await handler(request) });
        } catch (error) {
            emit({ id: request.id, ok: false, error: String(error?.stack ?? error).slice(0, 1500) });
        }
    });
});
lines.on('close', async () => {
    await stop();
    process.exit(0);
});

emit({ event: 'broker', url });
