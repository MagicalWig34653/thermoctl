import assert from 'node:assert/strict';
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { test } from 'node:test';
import { command, loopbackBroker, prefix, temp, until, zoneFixtures } from './fixtures.mjs';
import { config, createController, virtualHost } from './matter.mjs';

const { Thermostat } = await import('@matter/main/clusters/thermostat');
const { Status } = await import('@matter/types');
const { startBridge } = await import('../service.mjs');

const { Heat, Off, Cool, Auto } = Thermostat.SystemMode;
const thermostat = 'thermostat';
const basic = 'bridgedDeviceBasicInformation';
const read = endpoint => endpoint.getStateOf(thermostat);
const write = (endpoint, patch) => endpoint.setStateOf(thermostat, patch);
const reachable = async endpoint => (await endpoint.getStateOf(basic)).reachable;

const fixtures = [
    { id: 11, current_temperature: '19.50', setpoint: '21.0', operating_mode: 'auto', would_heat: 'true', override_active: 'false' },
    { id: 22, current_temperature: '17.25', setpoint: '18.3', operating_mode: 'manual', would_heat: 'false', override_active: 'false' },
    // Zone 33 ist beim ersten Start nicht konfiguriert: ihre Zustaende duerfen niemanden stoeren.
    { id: 33, current_temperature: '16.00', setpoint: '19.1', operating_mode: 'auto', would_heat: 'false', override_active: 'false' },
];

test('virtuelle Matter-Fabric mit zwei simulierten MQTT-Zonen', { timeout: 120_000 }, async t => {
    const storage = await temp(t);
    const controllerDir = await temp(t);
    const broker = await loopbackBroker(t, fixtures);
    const { simulator, commands, url, connected, dropConnection, holdAcks } = broker;
    await virtualHost(11);
    const controller = await createController(t, controllerDir, 'a', 5541);
    const codeLines = [];
    const warnings = [];
    const infos = [];
    const options = {
        output: code => codeLines.push(code),
        warning: message => warnings.push(message),
        info: message => infos.push(message),
    };
    const initial = config(join(storage, 'bridge'), url);
    let bridge = await startBridge(initial, options);
    t.after(() => bridge.close());
    assert.match(codeLines[0], /^\d{11}$/);
    await until(connected, 'Bridge verbindet sich nicht');
    await simulator.refresh();
    await until(bridge.allZonesReachable, 'Simulierte Zonen werden nicht erreichbar');

    const { passcode, discriminator } = bridge.node.state.commissioning;
    const peer = await controller.peers.commission({ passcode, discriminator, timeout: 8000 });
    const endpoints = () => [...peer.parts].flatMap(ep => [...ep.parts]);
    const remote = id => endpoints().find(ep => ep.number === bridge.zones.get(id).endpoint.number);
    await until(() => endpoints().length === 2, 'Zwei Thermostate fehlen im Aggregator');
    const a = remote(11);
    const b = remote(22);

    await t.test('CASE liest beide Heating-only-Thermostate getrennt mit konfigurierten Grenzen', async () => {
        assert.equal([...peer.parts].length, 1);
        assert.notEqual(a.number, b.number);
        const first = await read(a);
        const second = await read(b);
        // Zone B meldet "manual": gelesen wird Heat. Zone A meldet "auto": ebenfalls Heat.
        assert.deepEqual([first.localTemperature, first.occupiedHeatingSetpoint, first.systemMode, first.thermostatRunningState.heat],
            [1950, 2100, Heat, true]);
        assert.deepEqual([second.localTemperature, second.occupiedHeatingSetpoint, second.systemMode, second.thermostatRunningState.heat],
            [1725, 1830, Heat, false]);
        assert.equal(first.controlSequenceOfOperation, Thermostat.ControlSequenceOfOperation.HeatingOnly);
        assert.equal(first.occupiedCoolingSetpoint, undefined);
        assert.deepEqual([first.absMinHeatSetpointLimit, first.minHeatSetpointLimit, first.absMaxHeatSetpointLimit, first.maxHeatSetpointLimit],
            [520, 520, 3010, 3010]);
        assert.equal((await a.getStateOf(basic)).nodeLabel, 'Testzone A');
        assert.equal((await b.getStateOf(basic)).nodeLabel, 'Testzone B');
        // Test-Hersteller-ID: die Bridge behauptet keine Zertifizierung.
        assert.equal(bridge.node.state.basicInformation.vendorId, 0xfff1);
    });

    await t.test('Sollwertrad erreicht getrennte Zonen und sendet QoS 1 ohne Retain an temporary_setpoint', async () => {
        await write(a, { occupiedHeatingSetpoint: 2230 });
        await write(b, { occupiedHeatingSetpoint: 1870 });
        await until(() => simulator.state(11).setpoint === '22.3' && simulator.state(22).setpoint === '18.7',
            'Mindestens ein temporärer Sollwert fehlt');
        assert.deepEqual(commands.slice().sort((x, y) => x.topic.localeCompare(y.topic)),
            [command(11, 'temporary_setpoint', '22.3'), command(22, 'temporary_setpoint', '18.7')]);
        assert.equal(commands.some(({ topic }) => topic.endsWith('/command/setpoint')), false);
        // Der bestaetigte Sollwert kommt aus dem MQTT-Zustand zurueck, nicht aus einem Echo.
        await until(async () => (await read(a)).occupiedHeatingSetpoint === 2230, 'Bestätigter Sollwert fehlt');
    });

    await t.test('Heat sendet auto auch bei bereits angezeigtem Heat; Off sendet off und sperrt Sollwerte', async () => {
        const first = commands.length;
        await write(b, { systemMode: Heat });
        await until(() => simulator.state(22).operating_mode === 'auto', 'Heat aus manual hat auto nicht gesendet');
        await write(a, { systemMode: Off });
        await until(() => simulator.state(11).operating_mode === 'off' && a.stateOf(thermostat).systemMode === Off,
            'Off kam nicht an');
        // Sollwert-Writes bei Matter-Modus Off werden abgelehnt, auch mit dem schon geltenden Wert.
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 2230 }), { code: Status.InvalidInState });
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 2250 }), { code: Status.InvalidInState });
        await write(a, { systemMode: Heat });
        // Direkt nach Heat ist ein Sollwert zulaessig, obwohl thermoctl "off" noch nicht bestaetigt hat.
        await write(a, { occupiedHeatingSetpoint: 2250 });
        await until(() => simulator.state(11).setpoint === '22.5', 'Sollwert direkt nach Heat fehlt');
        assert.deepEqual(commands.slice(first), [
            command(22, 'operating_mode', 'auto'), command(11, 'operating_mode', 'off'), command(11, 'operating_mode', 'auto'),
            command(11, 'temporary_setpoint', '22.5'),
        ]);
        // Der Modus "off" in thermoctl wird als Off gelesen, manual/auto als Heat.
        await simulator.set(22, 'operating_mode', 'manual');
        await until(() => b.stateOf(thermostat).systemMode === Heat, 'manual wird nicht als Heat gelesen');
        await simulator.set(22, 'operating_mode', 'off');
        await until(() => b.stateOf(thermostat).systemMode === Off, 'off wird nicht als Off gelesen');
        await simulator.set(22, 'operating_mode', 'auto');
        await until(() => b.stateOf(thermostat).systemMode === Heat, 'auto wird nicht als Heat gelesen');
    });

    await t.test('Matter weist Grenzverletzungen, Hundertstel, Kühlmodus, Auto und fremde Attribute ab', async () => {
        const first = commands.length;
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 510 }), { code: Status.ConstraintError });
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 3020 }), { code: Status.ConstraintError });
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 2231 }), { code: Status.ConstraintError });
        await assert.rejects(write(a, { systemMode: Cool }), { code: Status.ConstraintError });
        await assert.rejects(write(a, { systemMode: Auto }), { code: Status.ConstraintError });
        await assert.rejects(write(a, { minHeatSetpointLimit: 700 }), { code: Status.UnsupportedWrite });
        await assert.rejects(write(a, { maxHeatSetpointLimit: 3000 }), { code: Status.UnsupportedWrite });
        // Die Grenzen selbst sind genau die konfigurierten und nicht schreibbar.
        await write(a, { occupiedHeatingSetpoint: 520 });
        await write(a, { occupiedHeatingSetpoint: 3010 });
        await pause(240);
        // Die beiden gueltigen Randwerte wurden entprellt: nur der letzte geht hinaus.
        assert.deepEqual(commands.slice(first), [command(11, 'temporary_setpoint', '30.1')]);
        await write(a, { occupiedHeatingSetpoint: 2250 });
        await until(() => simulator.state(11).setpoint === '22.5' && a.stateOf(thermostat).occupiedHeatingSetpoint === 2250,
            'Zurück auf 22,5 fehlt');
    });

    await t.test('schnelle Radschritte werden je Zone zum letzten Wert zusammengefasst', async () => {
        const first = commands.length;
        await write(a, { occupiedHeatingSetpoint: 2240 });
        await write(a, { occupiedHeatingSetpoint: 2250 });
        await write(a, { occupiedHeatingSetpoint: 2260 });
        await write(b, { occupiedHeatingSetpoint: 1900 });
        await until(() => simulator.state(11).setpoint === '22.6' && simulator.state(22).setpoint === '19',
            'Letzter Schritt oder zweite Zone fehlt');
        await pause(240);
        assert.deepEqual(commands.slice(first).sort((x, y) => x.topic.localeCompare(y.topic)),
            [command(11, 'temporary_setpoint', '22.6'), command(22, 'temporary_setpoint', '19')]);
    });

    await t.test('abgelehnte Writes springen nach 500 ms zurück, spätere MQTT-Zustände gewinnen, kein Echo', async () => {
        const first = commands.length;
        simulator.rejected.add(11);
        await write(a, { occupiedHeatingSetpoint: 2400 });
        assert.equal((await read(a)).occupiedHeatingSetpoint, 2400);
        await until(() => simulator.seen.at(-1)?.input === '24', 'Befehl fehlt beim Simulator');
        assert.equal(simulator.seen.at(-1).accepted, false);
        const sent = performance.now();
        await until(() => a.stateOf(thermostat).occupiedHeatingSetpoint === 2260, '500-ms-Abgleich fehlt');
        assert.ok(performance.now() - sent >= 300, 'Rueckspruengen darf nicht schneller als der Abgleich sein');
        // Ein abgelehnter Moduswechsel springt ebenfalls zurueck.
        await write(a, { systemMode: Off });
        await until(() => simulator.seen.at(-1)?.input === 'off' && simulator.seen.at(-1).accepted === false, 'Off-Befehl fehlt');
        await until(() => a.stateOf(thermostat).systemMode === Heat, 'Abgelehntes Off springt nicht zurück');
        simulator.rejected.delete(11);
        // Spaetere Zustaende gewinnen in der Reihenfolge des Empfangs.
        await simulator.set(11, 'setpoint', '20.8');
        await simulator.set(11, 'setpoint', '20.7');
        await simulator.set(11, 'would_heat', 'false');
        await simulator.set(11, 'override_active', 'false');
        await until(() => a.stateOf(thermostat).occupiedHeatingSetpoint === 2070
            && a.stateOf(thermostat).thermostatRunningState?.heat === false, 'Letzter MQTT-Zustand fehlt');
        await pause(250);
        // Genau die zwei Befehle des Nutzers, kein Echo der empfangenen Zustaende.
        assert.deepEqual(commands.slice(first), [command(11, 'temporary_setpoint', '24'), command(11, 'operating_mode', 'off')]);
    });

    await t.test('hostile Nutzlasten: Sollwert/Modus sperren nur ihre Zone, Istwert/Heizbedarf degradieren', async () => {
        const first = commands.length;
        const warned = warnings.length;
        const bin = Buffer.from([0xff, 0xfe, 0x00, 0x39]);
        const huge = Buffer.alloc(10 * 1024 * 1024, 0x39);
        const hard = [
            ['setpoint', '', '20.7'], ['setpoint', 'NaN', '20.7'], ['setpoint', 'Infinity', '20.7'], ['setpoint', '-Infinity', '20.7'],
            ['setpoint', '21.00', '20.7'], ['setpoint', '0x15', '20.7'], ['setpoint', '21\n', '20.7'], ['setpoint', ' 21', '20.7'],
            ['setpoint', '1e1', '20.7'], ['setpoint', '+21', '20.7'], ['setpoint', '21.', '20.7'], ['setpoint', '.5', '20.7'],
            ['setpoint', '21,5', '20.7'], ['setpoint', '٢١', '20.7'], ['setpoint', '31', '20.7'], ['setpoint', '5.1', '20.7'],
            ['setpoint', '--5', '20.7'], ['setpoint', bin, '20.7'], ['setpoint', huge, '20.7'],
            ['operating_mode', 'cool', 'auto'], ['operating_mode', 'heat', 'auto'], ['operating_mode', 'AUTO', 'auto'],
            ['operating_mode', ' auto', 'auto'], ['operating_mode', 'auto\n', 'auto'], ['operating_mode', '', 'auto'],
            ['operating_mode', bin, 'auto'], ['operating_mode', huge, 'auto'],
        ];
        for (const [key, hostile, restore] of hard) {
            await simulator.set(11, key, hostile);
            await until(async () => (await reachable(a)) === false, `Gefährliche Nutzlast akzeptiert bei ${key}: ${hostile.toString().slice(0, 12)}`);
            await until(() => a.stateOf(thermostat).localTemperature === null, 'Unerreichbare Zone zeigt noch einen Istwert');
            assert.equal(await reachable(b), true);
            assert.equal((await read(b)).occupiedHeatingSetpoint, 1900);
            await assert.rejects(write(a, { systemMode: Heat }), { code: Status.InvalidInState });
            await assert.rejects(write(a, { occupiedHeatingSetpoint: 2100 }), { code: Status.InvalidInState });
            await simulator.set(11, key, restore);
            await until(async () => (await reachable(a)) === true, `Keine Erholung nach ${key}`);
        }
        const soft = [
            ['current_temperature', '', 'null'], ['current_temperature', 'NaN', 'null'], ['current_temperature', 'Infinity', 'null'],
            ['current_temperature', '-273.16', 'null'], ['current_temperature', '0x13', 'null'], ['current_temperature', '1e2', 'null'],
            ['current_temperature', '19.123', 'null'], ['current_temperature', '٣', 'null'], ['current_temperature', bin, 'null'],
            ['current_temperature', huge, 'null'], ['would_heat', '1', 'heat-false'], ['would_heat', 'TRUE', 'heat-false'],
            ['would_heat', 'true\n', 'heat-false'], ['would_heat', '', 'heat-false'], ['would_heat', bin, 'heat-false'],
        ];
        await simulator.set(11, 'would_heat', 'true');
        await until(() => a.stateOf(thermostat).thermostatRunningState?.heat === true, 'Heizbedarf fehlt');
        for (const [key, hostile, expected] of soft) {
            await simulator.set(11, 'current_temperature', '19.50');
            await simulator.set(11, 'would_heat', 'true');
            await until(() => a.stateOf(thermostat).localTemperature === 1950, 'Istwert fehlt');
            await simulator.set(11, key, hostile);
            if (expected === 'null') await until(() => a.stateOf(thermostat).localTemperature === null, `Istwert nicht null bei ${hostile.toString().slice(0, 12)}`);
            else await until(() => a.stateOf(thermostat).thermostatRunningState?.heat === false, 'Heizbedarf bleibt wahr');
            // Die Zone bleibt bedienbar: nur der betroffene Wert fehlt.
            assert.equal(await reachable(a), true);
        }
        // override_active ohne Matter-Gegenstueck: Muell darf nichts veraendern und nichts zerstoeren.
        await simulator.set(11, 'override_active', bin);
        await simulator.set(11, 'override_active', 'true');
        await simulator.set(11, 'current_temperature', '19.50');
        await until(() => a.stateOf(thermostat).localTemperature === 1950, 'Istwert nach Muell fehlt');
        assert.equal(await reachable(a), true);
        // Es wurde nichts gesendet; Warnungen nennen nie den Inhalt.
        assert.equal(commands.length, first);
        assert.ok(warnings.length > warned, 'ungueltige Werte muessen als Warnung auftauchen');
        for (const message of warnings.slice(warned)) assert.match(message, /^Zone \d+: ungültiger Wert für [a-z_]+ \(\d+ Bytes\) verworfen$/);
    });

    await t.test('setpointRaiseLower: relative Schritte laufen über denselben Befehlsweg und dieselben Prüfungen', async () => {
        await simulator.set(11, 'setpoint', '21.0');
        await until(() => a.stateOf(thermostat).occupiedHeatingSetpoint === 2100, 'Ausgangswert 21,0 fehlt');
        const first = commands.length;
        const raise = (endpoint, mode, amount) => endpoint.commandsOf('thermostat').setpointRaiseLower({ mode, amount });
        // amount zaehlt in Zehntelgrad: +5 = +0,5 Grad (21,0 -> 21,5), nicht +5 Grad und nicht +0,05.
        await raise(a, Thermostat.SetpointRaiseLowerMode.Heat, 5);
        await until(() => simulator.state(11).setpoint === '21.5', 'Relativer Schritt +0,5 fehlt');
        await until(() => a.stateOf(thermostat).occupiedHeatingSetpoint === 2150, 'Bestätigter Wert 21,5 fehlt');
        await raise(a, Thermostat.SetpointRaiseLowerMode.Heat, -5);
        await until(() => simulator.state(11).setpoint === '21', 'Relativer Schritt -0,5 fehlt');
        await until(() => a.stateOf(thermostat).occupiedHeatingSetpoint === 2100, 'Bestätigter Wert 21,0 fehlt');
        assert.deepEqual(commands.slice(first), [command(11, 'temporary_setpoint', '21.5'), command(11, 'temporary_setpoint', '21')]);
        // Kuehlen und Beide sind nicht verfuegbar; Werte ausserhalb der Grenzen (max. 30,1) werden abgelehnt.
        const rejected = commands.length;
        await assert.rejects(raise(a, Thermostat.SetpointRaiseLowerMode.Cool, 5));
        await assert.rejects(raise(a, Thermostat.SetpointRaiseLowerMode.Both, 5));
        await assert.rejects(raise(a, Thermostat.SetpointRaiseLowerMode.Heat, 100), { code: Status.ConstraintError });
        await assert.rejects(raise(a, Thermostat.SetpointRaiseLowerMode.Heat, -200), { code: Status.ConstraintError });
        // Bei Matter-Modus Aus wird auch ein relativer Schritt abgelehnt.
        await simulator.set(11, 'operating_mode', 'off');
        await until(() => a.stateOf(thermostat).systemMode === Off, 'Off fehlt');
        await assert.rejects(raise(a, Thermostat.SetpointRaiseLowerMode.Heat, 5), { code: Status.InvalidInState });
        await simulator.set(11, 'operating_mode', 'auto');
        await until(() => a.stateOf(thermostat).systemMode === Heat, 'Heat fehlt');
        await pause(300);
        assert.equal(commands.length, rejected, 'Abgelehnte relative Schritte dürfen nichts senden');
        assert.equal((await read(a)).occupiedHeatingSetpoint, 2100);
    });

    await t.test('Heizen abgelehnt, Rad innerhalb von 500 ms: der Moduswunsch läuft trotzdem ab, danach keine Sollwerte mehr', async () => {
        await simulator.set(22, 'operating_mode', 'off');
        await until(() => b.stateOf(thermostat).systemMode === Off, 'Off der zweiten Zone fehlt');
        const first = commands.length;
        simulator.rejected.add(22);
        await write(b, { systemMode: Heat });
        await pause(80);
        // Der Moduswunsch gilt noch: das Rad wird angenommen und (nach Entprellung) gesendet.
        await write(b, { occupiedHeatingSetpoint: 2000 });
        await until(() => commands.length === first + 2, 'Befehle von Modus und Rad fehlen');
        await until(() => b.stateOf(thermostat).systemMode === Off, 'Abgelehntes Heizen springt nicht zurück', 2500);
        // Nach Ablauf (500 ms nach dem Moduswunsch, unabhängig vom Sollwert-Send) ist der Wunsch vergessen.
        await pause(700);
        await assert.rejects(write(b, { occupiedHeatingSetpoint: 2010 }), { code: Status.InvalidInState });
        await pause(300);
        assert.equal(commands.length, first + 2, 'Nach Ablauf darf kein temporary_setpoint mehr hinausgehen');
        assert.deepEqual(commands.slice(first), [command(22, 'operating_mode', 'auto'), command(22, 'temporary_setpoint', '20')]);
        simulator.rejected.delete(22);
        await simulator.set(22, 'operating_mode', 'auto');
        await simulator.set(22, 'setpoint', '19.0');
        await until(() => b.stateOf(thermostat).systemMode === Heat && b.stateOf(thermostat).occupiedHeatingSetpoint === 1900, 'Zone 22 nicht zurückgesetzt');
    });

    await t.test('Verbindungswechsel verwirft ungequittete und entprellte Befehle sowie Retained-Zustände', async () => {
        const first = commands.length;
        holdAcks(true);
        await write(a, { systemMode: Off });
        await until(() => commands.length === first + 1, 'Unquittierter Befehl fehlt am Broker');
        await write(b, { occupiedHeatingSetpoint: 2310 });
        dropConnection();
        await until(() => a.stateOf(thermostat).localTemperature === null, 'Offline-Meldung fehlt');
        // Die echte healthy()-Logik: ohne Broker nicht gesund, obwohl der Matter-Knoten online ist.
        assert.equal(bridge.node.lifecycle.isOnline, true);
        assert.equal(bridge.healthy(), false, 'Ohne MQTT-Verbindung ist die Bridge nicht gesund');
        assert.equal(bridge.allZonesReachable(), false);
        assert.equal(await reachable(a), false);
        await assert.rejects(write(a, { occupiedHeatingSetpoint: 2070 }), { code: Status.InvalidInState });
        holdAcks(false);
        await until(connected, 'Bridge verbindet sich nicht erneut');
        await until(bridge.healthy, 'Nach Wiederverbindung wieder gesund');
        await pause(250);
        // Retained-Zustaende aus der Zeit vor dem Verbindungsverlust gelten nicht als frisch.
        assert.equal(await reachable(a), false);
        assert.equal(commands.length, first + 1, 'Kein Befehl darf nach der Wiederverbindung nachgereicht werden');
        await simulator.refresh();
        await until(bridge.allZonesReachable, 'Frische Daten nach Reconnect fehlen');
        await write(b, { occupiedHeatingSetpoint: 1910 });
        await until(() => simulator.state(22).setpoint === '19.1', 'Write nach Wiederverbindung fehlt');
        assert.deepEqual(commands.slice(first), [command(11, 'operating_mode', 'off'), command(22, 'temporary_setpoint', '19.1')]);
        assert.ok(infos.includes('MQTT getrennt') && infos.filter(m => m === 'MQTT verbunden').length >= 2);
    });

    await t.test('Neustart behält Fabric, CASE, Passcode, Discriminator und Endpoint-Identitäten; Zonen kommen und gehen', async () => {
        const beforeA = await a.getStateOf(basic, ['uniqueId']);
        const beforeB = await b.getStateOf(basic, ['uniqueId']);
        assert.match(beforeA.uniqueId, /^[a-f0-9]{32}$/);
        assert.match(beforeB.uniqueId, /^[a-f0-9]{32}$/);
        assert.notEqual(beforeA.uniqueId, beforeB.uniqueId);
        const oldA = a.number;
        const oldB = b.number;
        await bridge.close();
        bridge = await startBridge(config(initial.storageDir, url, [
            { id: 33, name: 'Testzone C' }, { id: 11, name: 'Umbenannte Testzone' },
        ], { MATTER_BRIDGE_MAX_AGE_SECONDS: '1' }), options);
        assert.equal(bridge.node.lifecycle.isCommissioned, true);
        assert.equal(bridge.node.state.commissioning.passcode, passcode);
        assert.equal(bridge.node.state.commissioning.discriminator, discriminator);
        assert.equal(codeLines.length, 1, 'Der Kopplungscode wird genau einmal ausgegeben');
        assert.equal(bridge.zones.get(11).endpoint.number, oldA);
        assert.notEqual(bridge.zones.get(33).endpoint.number, oldB, 'Nummer einer entfernten Zone wird nicht wiederverwendet');
        assert.notEqual(bridge.zones.get(33).endpoint.number, oldA);
        assert.equal(bridge.zones.has(22), false);
        await until(connected, 'Neuverbindung fehlt');
        await simulator.refresh();
        await until(bridge.allZonesReachable, 'Frische Zustände nach Neustart fehlen');
        assert.equal((await a.getStateOf(basic)).nodeLabel, 'Umbenannte Testzone');
        assert.equal((await a.getStateOf(basic)).uniqueId, beforeA.uniqueId);
        assert.equal((await read(a)).occupiedHeatingSetpoint, 2100);
        await assert.rejects(read(b), 'Die entfernte Zone darf nicht mehr erreichbar sein');
        assert.deepEqual([...(await [...peer.parts][0].getStateOf('descriptor'))?.partsList ?? []].sort(),
            [oldA, bridge.zones.get(33).endpoint.number].sort());
        // Maximalalter: schweigt thermoctl, wird die Zone unerreichbar und Writes werden abgelehnt.
        await until(async () => (await reachable(a)) === false, 'Maximalalter sperrt alte Zustände nicht', 4000);
        await assert.rejects(write(a, { systemMode: Heat }), { code: Status.InvalidInState });

        await bridge.close();
        bridge = await startBridge(config(initial.storageDir, url, zoneFixtures), options);
        assert.equal(bridge.zones.get(22).endpoint.number, oldB, 'Eine wieder hinzugefügte Zone behält ihre Nummer');
        assert.equal(bridge.zones.get(22).endpoint.state.bridgedDeviceBasicInformation.uniqueId, beforeB.uniqueId);
        assert.equal(bridge.zones.get(11).endpoint.number, oldA);
        assert.equal(codeLines.length, 1);
    });

    // Nichts ausser den zwei erlaubten Befehlen ging ueber die Leitung, abonniert wurde nur state/*.
    broker.assertWireDiscipline();
    assert.equal(warnings.some(message => /passcode|\d{11}/i.test(message)), false);
});
