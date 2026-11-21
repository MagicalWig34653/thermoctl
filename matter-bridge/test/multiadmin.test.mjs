import assert from 'node:assert/strict';
import { join } from 'node:path';
import { test } from 'node:test';
import { command, loopbackBroker, temp, until } from './fixtures.mjs';
import { config, createController, virtualHost } from './matter.mjs';

const { startBridge } = await import('../service.mjs');
const { Thermostat } = await import('@matter/main/clusters/thermostat');
const { Minutes } = await import('@matter/main');

// Apple Home und Google Home sind zwei Controller mit je eigener Fabric an derselben Bridge. Das
// zweite Oeffnen des Kopplungsfensters ("Weitergeben" in Apple Home, "Verknuepfte Apps" in Google
// Home) macht der erste Controller selbst: er ruft OpenCommissioningWindow auf der Bridge auf.
// Die Bridge gibt dafuer keinen weiteren Code aus und braucht keine eigene Funktion.
test('Multi-Admin: ein zweiter Controller koppelt über ein vom ersten geöffnetes Kopplungsfenster', { timeout: 60_000 }, async t => {
    const storage = await temp(t);
    const fixtures = [{ id: 11, current_temperature: '19.50', setpoint: '21.0', operating_mode: 'auto', would_heat: 'true', override_active: 'false' }];
    const broker = await loopbackBroker(t, fixtures, { zoneCount: 1 });
    await virtualHost(11);
    const first = await createController(t, await temp(t), 'erster', 5541);
    const second = await createController(t, await temp(t), 'zweiter', 5542);
    const codes = [];
    const bridge = await startBridge(config(join(storage, 'bridge'), broker.url, [{ id: 11, name: 'Testzone A' }]),
        { output: code => codes.push(code) });
    t.after(() => bridge.close());
    await until(broker.connected, 'Bridge verbindet sich nicht');
    await broker.simulator.refresh();
    await until(bridge.allZonesReachable, 'Zone nicht erreichbar');

    const { passcode, discriminator } = bridge.node.state.commissioning;
    const peer = await first.peers.commission({ passcode, discriminator, timeout: 8000 });
    assert.equal(bridge.node.state.operationalCredentials.commissionedFabrics, 1);
    // Nach der ersten Kopplung ist das Standardfenster zu: der gedruckte Code taugt kein zweites Mal.
    await assert.rejects(second.peers.commission({ passcode, discriminator, timeout: 3000 }));
    assert.equal(bridge.node.state.operationalCredentials.commissionedFabrics, 1);

    const window = await peer.openEnhancedCommissioningWindow(Minutes(5));
    assert.notEqual(window.manualPairingCode, codes[0], 'Das Fenster hat einen eigenen Code');
    const peer2 = await second.peers.commission({ passcode: window.passcode, discriminator: window.discriminator, timeout: 8000 });
    assert.equal(bridge.node.state.operationalCredentials.commissionedFabrics, 2);
    assert.equal(codes.length, 1, 'Die Bridge selbst gibt keinen weiteren Code aus');

    const zone = id => [...peer2.parts].flatMap(ep => [...ep.parts]).find(ep => ep.number === bridge.zones.get(id).endpoint.number);
    await until(() => zone(11), 'Thermostat fehlt in der zweiten Fabric');
    assert.equal((await zone(11).getStateOf('thermostat')).occupiedHeatingSetpoint, 2100);
    // Beide Fabrics bedienen dieselbe Zone ueber denselben Befehlsweg.
    await zone(11).setStateOf('thermostat', { occupiedHeatingSetpoint: 2250 });
    await until(() => broker.simulator.state(11).setpoint === '22.5', 'Befehl der zweiten Fabric fehlt');
    assert.deepEqual(broker.commands, [command(11, 'temporary_setpoint', '22.5')]);
    const zoneOfFirst = [...peer.parts].flatMap(ep => [...ep.parts]).find(ep => ep.number === bridge.zones.get(11).endpoint.number);
    await until(async () => (await zoneOfFirst.getStateOf('thermostat')).occupiedHeatingSetpoint === 2250,
        'Die erste Fabric sieht die Änderung der zweiten nicht');
    assert.equal(Thermostat.SystemMode.Heat, (await zone(11).getStateOf('thermostat')).systemMode);
    broker.assertWireDiscipline();
});
