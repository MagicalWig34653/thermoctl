import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseState } from '../bridge.mjs';
import { Thermostat } from '@matter/main/clusters/thermostat';

const limits = { minSetpoint: 520, maxSetpoint: 3010 };
const parse = (key, text) => parseState(key, Buffer.from(text, 'latin1'), limits);

test('Istwert: höchstens zwei Nachkommastellen, endlich, über dem absoluten Nullpunkt', () => {
    assert.equal(parse('current_temperature', '19.5'), 1950);
    assert.equal(parse('current_temperature', '19.50'), 1950);
    assert.equal(parse('current_temperature', '-5.25'), -525);
    assert.equal(parse('current_temperature', '0'), 0);
    assert.equal(parse('current_temperature', '-273.15'), -27315);
    for (const bad of ['', 'NaN', 'Infinity', '-Infinity', '-273.16', '-1000', '1000', '1e2', '0x13', '19.123', '19,5', ' 19', '19 ', '19\n',
        '+19', '.5', '19.', '--1', 'null', 'true']) {
        assert.equal(parse('current_temperature', bad), null, JSON.stringify(bad));
    }
});

test('Sollwert: Zehntelgrad, innerhalb der Grenzen, keine Schreibvarianten', () => {
    assert.equal(parse('setpoint', '21'), 2100);
    assert.equal(parse('setpoint', '21.0'), 2100);
    assert.equal(parse('setpoint', '5.2'), 520);
    assert.equal(parse('setpoint', '30.1'), 3010);
    for (const bad of ['21.00', '21.55', '5.1', '30.2', '31', '-20', '0x15', '1e1', 'NaN', 'Infinity', '+21', '21,5', ' 21', '21\n', '']) {
        assert.equal(parse('setpoint', bad), null, JSON.stringify(bad));
    }
    // Kein Fantasiewert durch Rundung: 21.04 ist kein Zehntelgrad.
    assert.equal(parse('setpoint', '21.04'), null);
});

test('Betriebsart: off, manual und auto; alles andere ist ungültig, auch Kühlen', () => {
    assert.equal(parse('operating_mode', 'off'), Thermostat.SystemMode.Off);
    assert.equal(parse('operating_mode', 'manual'), Thermostat.SystemMode.Heat);
    assert.equal(parse('operating_mode', 'auto'), Thermostat.SystemMode.Heat);
    for (const bad of ['heat', 'cool', 'Auto', 'OFF', ' off', 'off\n', '', 'auto\0']) {
        assert.equal(parse('operating_mode', bad), null, JSON.stringify(bad));
    }
});

test('Heizbedarf: nur true und false; Nutzlast als Nicht-Buffer, leer oder riesig ist ungültig', () => {
    assert.equal(parse('would_heat', 'true'), true);
    assert.equal(parse('would_heat', 'false'), false);
    for (const bad of ['', '1', '0', 'True', 'TRUE', 'yes', 'true\n']) assert.equal(parse('would_heat', bad), null, JSON.stringify(bad));
    assert.equal(parseState('would_heat', 'true', limits), null);
    assert.equal(parseState('would_heat', undefined, limits), null);
    assert.equal(parseState('current_temperature', Buffer.alloc(10 * 1024 * 1024, 0x39), limits), null);
    assert.equal(parseState('setpoint', Buffer.from([0xff, 0xfe, 0x00]), limits), null);
    // Nicht-ASCII-Ziffern gelten nicht als Ziffern.
    assert.equal(parseState('setpoint', Buffer.from('٢١', 'utf8'), limits), null);
});

test('Rundreise Matter-Hundertstel -> MQTT-Zeichenkette -> Parser ergibt für alle 551 Zehntelwerte von -20,0 bis 35,0 denselben Wert', () => {
    const wide = { minSetpoint: -2000, maxSetpoint: 3500 };
    for (let tenth = -200; tenth <= 350; tenth++) {
        const hundredths = tenth * 10;
        // So bildet die Bridge die Nutzlast (bridge.mjs, command()): String(value / 100).
        const sent = String(hundredths / 100);
        // Ganze Grad gehen als "23" hinaus, nicht als "23.0"; thermoctl liest beides (Decimal("23")).
        assert.match(sent, /^-?\d+(?:\.\d)?$/, `Format von ${hundredths}: ${sent}`);
        assert.equal(parseState('setpoint', Buffer.from(sent), wide), hundredths, `Rundreise ${sent}`);
    }
    // Der Fall, an dem Math.trunc scheitert: -19.9 * 100 ist -1989.9999999999998.
    assert.equal(parseState('setpoint', Buffer.from('-19.9'), { minSetpoint: -2000, maxSetpoint: 3500 }), -1990);
});
