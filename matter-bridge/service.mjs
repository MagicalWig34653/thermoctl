import { readFileSync } from 'node:fs';
import { Boot, CommissioningServer, Environment, Filesystem, ServerNode, StorageService } from '@matter/main';
import { FlatFileBlobStorageDriver, SqliteStorageDriver } from '@matter/nodejs';
import { attachZones } from './bridge.mjs';
import { connectMqtt } from './mqtt.mjs';
import { nodeId, openStorage, StorageError } from './storage.mjs';

class QuietCommissioning extends CommissioningServer {
    initiateCommissioning() {
        // Disable the Matter.js default output of credentials.
    }
}

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));
const root = ServerNode.RootEndpointWithoutGroupcast.with(QuietCommissioning);

// Kurze Ursache fuer die Startmeldung. Lange Ziffernfolgen (Kopplungsdaten) werden maskiert,
// auch wenn keine Matter.js-Fehlermeldung bekannt ist, die sie enthielte.
export function describe(error) {
    const cause = error?.cause ?? error;
    return `${cause?.name ?? 'Error'}: ${cause?.message ?? ''}`.replace(/\d{8,}/g, '***').slice(0, 200);
}

export async function startBridge(config, { output = () => {}, warning = () => {}, info = () => {}, network = {} } = {}) {
    const log = { warning, info };
    await Boot.ready;
    const storage = await openStorage(config);
    const environment = new Environment(nodeId, Environment.default);
    environment.set(Filesystem, storage.filesystem);
    const driver = new StorageService(environment);
    driver.registerDriver(SqliteStorageDriver);
    driver.registerBlobDriver(FlatFileBlobStorageDriver);
    driver.defaultDriver = 'sqlite';
    driver.defaultBlobDriver = 'file';

    let node;
    let mqtt;
    let zones;
    async function close() {
        try {
            await mqtt?.close();
        } finally {
            try {
                if (zones) await Promise.all([...zones.values()].map(({ backend }) => backend.close()));
                await node?.close();
            } finally {
                await storage.release();
            }
        }
    }
    try {
        node = new ServerNode(root, {
            id: nodeId,
            environment,
            network: { ...network, port: config.matterPort },
            // 0xFFF1 ist die Matter-Test-Hersteller-ID, 0x8000 eine Test-Produkt-ID: Die Bridge ist nicht
            // zertifiziert und behauptet es nicht (Apple: "Add Anyway", Google: Test-VID im Entwicklerkonto).
            basicInformation: {
                nodeLabel: 'thermoctl',
                vendorName: 'thermoctl',
                productName: 'Matter Bridge',
                vendorId: 0xfff1,
                productId: 0x8000,
                hardwareVersion: 1,
                hardwareVersionString: 'container',
                softwareVersion: 1,
                softwareVersionString: version,
            },
        });
        await node.construction;
        const attached = await attachZones(node, config, storage.entries, (path, value) => mqtt?.publish(path, value), log);
        zones = attached.zones;
        await storage.save(node);
        await node.start();
        await storage.issueCode(node, output);
        mqtt = connectMqtt(config, attached.routes, () => {
            for (const { backend } of zones.values()) backend.reset();
        }, log);
        return {
            node,
            zones,
            aggregator: attached.aggregator,
            // Gesund = Matter-Knoten online und Broker verbunden. Nicht erreichbare Zonen
            // (thermoctl schweigt) sind kein Fall fuer einen Neustart und zaehlen hier nicht.
            healthy: () => node.lifecycle.isOnline && mqtt?.connected === true,
            allZonesReachable: () => [...zones.values()].every(({ backend }) => backend.reachable),
            close,
        };
    } catch (error) {
        await close().catch(() => {});
        if (error instanceof StorageError) throw error;
        throw new Error(`Matter-Bridge konnte nicht gestartet werden (${describe(error)}). Speicher, Netzwerkschnittstellen und Matter-Port prüfen`);
    }
}
