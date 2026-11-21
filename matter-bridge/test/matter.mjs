import './no-udp.mjs';
import { Boot, Environment, Filesystem, Logger, Network, NetworkSimulator, ServerNode, StorageService } from '@matter/main';
import { NodeJsFilesystem, SqliteStorageDriver } from '@matter/nodejs';
import { readConfig } from '../config.mjs';
import { prefix, zoneFixtures } from './fixtures.mjs';

Logger.level = 'fatal';

export function config(storage, url, zones = zoneFixtures, extra = {}) {
    return readConfig({
        MATTER_BRIDGE_STORAGE_DIR: storage,
        MATTER_BRIDGE_MQTT_URL: url,
        MATTER_BRIDGE_TOPIC_PREFIX: prefix,
        MATTER_BRIDGE_ZONES: JSON.stringify(zones),
        MATTER_BRIDGE_MIN_SETPOINT: '5.2',
        MATTER_BRIDGE_MAX_SETPOINT: '30.1',
        ...extra,
    });
}

// Ein virtuelles Matter-Netz fuer den Prozess. Matter.js nutzt es statt OS-Sockets.
export async function virtualHost(id = 11) {
    await Boot.ready;
    const host = new NetworkSimulator().addHost(id);
    Environment.default.set(Network, host);
    return host;
}

// Ein Matter-Controller mit eigenem Speicher (hier: ein "zweiter Admin" ist ein weiterer Controller).
export async function createController(t, dir, id, port) {
    const env = new Environment(`controller-${id}`, Environment.default);
    env.set(Filesystem, new NodeJsFilesystem(dir));
    const driver = new StorageService(env);
    driver.registerDriver(SqliteStorageDriver);
    driver.defaultDriver = 'sqlite';
    const controller = await ServerNode.create(ServerNode.RootEndpointWithoutGroupcast, {
        id: `controller-${id}`,
        environment: env,
        network: { port },
        commissioning: { enabled: false },
    });
    t.after(() => controller.close());
    await controller.start();
    return controller;
}

export { Logger };
