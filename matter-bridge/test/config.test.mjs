import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { ConfigurationError, readConfig } from '../config.mjs';
import { temp } from './fixtures.mjs';

const base = {
    MATTER_BRIDGE_MQTT_URL: 'mqtt://broker.example:1883',
    MATTER_BRIDGE_ZONES: '[{"id":1,"name":"Wohnen"}]',
};
const read = extra => readConfig({ ...base, ...extra });
const rejects = (extra, pattern) => assert.throws(() => read(extra), error => {
    assert.ok(error instanceof ConfigurationError, `ConfigurationError erwartet, erhalten: ${error}`);
    assert.match(error.message, pattern);
    return true;
});

test('Standardwerte entsprechen den thermoctl-Grenzen und dem thermoctl-Präfix', () => {
    const config = read({});
    assert.equal(config.topicPrefix, 'thermoctl');
    assert.equal(config.matterPort, 5540);
    // -20 bis 35 Grad wie thermoctl/domain/modes.py (MINIMUM/MAXIMUM_TEMPERATURE_C), in Hundertstelgrad.
    assert.deepEqual([config.minSetpoint, config.maxSetpoint], [-2000, 3500]);
    assert.equal(config.maxAgeMs, 300_000);
    assert.equal(config.logLevel, 'info');
    assert.deepEqual(config.zones, [{ id: 1, name: 'Wohnen' }]);
});

test('Umgebungsvariablen haben Vorrang vor der JSON-Datei', async t => {
    const dir = await temp(t);
    const path = join(dir, 'bridge.json');
    await writeFile(path, JSON.stringify({
        mqttUrl: 'mqtt://aus-datei:1883', topicPrefix: 'aus/datei', matterPort: 5599,
        zones: [{ id: 7, name: 'Aus Datei' }], minSetpoint: 6, maxSetpoint: '28.5',
    }));
    const config = readConfig({ MATTER_BRIDGE_CONFIG: path, MATTER_BRIDGE_TOPIC_PREFIX: '/aus/umgebung/' });
    assert.equal(config.topicPrefix, 'aus/umgebung');
    assert.equal(config.mqttUrl, 'mqtt://aus-datei:1883');
    assert.equal(config.matterPort, 5599);
    assert.deepEqual(config.zones, [{ id: 7, name: 'Aus Datei' }]);
    assert.deepEqual([config.minSetpoint, config.maxSetpoint], [600, 2850]);
});

test('ungültige JSON-Datei, unbekannte Felder und fehlende Datei brechen mit klarer Meldung ab', async t => {
    const dir = await temp(t);
    const broken = join(dir, 'kaputt.json');
    await writeFile(broken, '{nicht json');
    rejects({ MATTER_BRIDGE_CONFIG: broken }, /ungültiges JSON/);
    const unknown = join(dir, 'unbekannt.json');
    await writeFile(unknown, JSON.stringify({ mqttUrl: 'mqtt://x', tippfehler: 1 }));
    rejects({ MATTER_BRIDGE_CONFIG: unknown }, /bekannten Feldern/);
    const list = join(dir, 'liste.json');
    await writeFile(list, '[]');
    rejects({ MATTER_BRIDGE_CONFIG: list }, /bekannten Feldern/);
    rejects({ MATTER_BRIDGE_CONFIG: join(dir, 'fehlt.json') }, /kann nicht gelesen werden/);
});

test('MQTT-URL: Pflicht, nur mqtt/mqtts, keine Zugangsdaten in der URL', () => {
    for (const url of [undefined, '', 'broker', 'http://broker', 'mqtt://', 'mqtt://u:p@broker', 'mqtt://broker/pfad', 'mqtt://broker?x=1',
        'mqtt://broker:0', 'mqtt://bro ker', 'ws://broker']) {
        assert.throws(() => readConfig({ ...base, MATTER_BRIDGE_MQTT_URL: url }), ConfigurationError, String(url));
    }
    assert.equal(read({ MATTER_BRIDGE_MQTT_URL: 'mqtts://broker:8883' }).mqttUrl, 'mqtts://broker:8883');
});

test('Das Passwort steht in keiner Fehlermeldung, auch nicht bei kaputter URL', () => {
    const secret = 'geheimes-passwort-4711';
    for (const extra of [
        { MATTER_BRIDGE_MQTT_URL: `mqtt://user:${secret}@broker` },
        { MATTER_BRIDGE_MQTT_URL: 'kaputt', MATTER_BRIDGE_MQTT_PASSWORD: secret, MATTER_BRIDGE_MQTT_USERNAME: 'u' },
        { MATTER_BRIDGE_MQTT_PASSWORD: secret },
        { MATTER_BRIDGE_MQTT_USERNAME: 'u', MATTER_BRIDGE_MQTT_PASSWORD: secret, MATTER_BRIDGE_ZONES: '[]' },
        { MATTER_BRIDGE_MQTT_USERNAME: 'u', MATTER_BRIDGE_MQTT_PASSWORD: secret, MATTER_BRIDGE_MATTER_PORT: 'x' },
    ]) {
        assert.throws(() => read(extra), error => {
            assert.ok(error instanceof ConfigurationError);
            assert.equal(error.message.includes(secret), false, error.message);
            return true;
        });
    }
    const config = read({ MATTER_BRIDGE_MQTT_USERNAME: 'u', MATTER_BRIDGE_MQTT_PASSWORD: secret });
    assert.equal(config.mqttPassword, secret);
    // Auch ein Objekt-Abbild der Konfiguration darf das Passwort nicht verraten, wenn jemand es loggt.
    assert.equal(JSON.stringify({ ...config, mqttPassword: undefined }).includes(secret), false);
});

test('Zonenliste: Pflicht, eindeutige positive IDs, Namen bis 32 Bytes, nur id und name', () => {
    for (const zones of [undefined, 'kein json', '{}', '[]', '[null]', '[{"id":1}]', '[{"name":"x"}]', '[{"id":0,"name":"x"}]',
        '[{"id":-1,"name":"x"}]', '[{"id":1.5,"name":"x"}]', '[{"id":"1","name":"x"}]', '[{"id":1,"name":""}]', '[{"id":1,"name":"  "}]',
        '[{"id":1,"name":"x"},{"id":1,"name":"y"}]', '[{"id":1,"name":"x","extra":1}]', `[{"id":1,"name":"${'ä'.repeat(17)}"}]`,
        '[{"id":1,"name":"a\\nb"}]', '[{"id":1,"name":"a\\u0000b"}]', '[{"id":9007199254740993,"name":"x"}]']) {
        assert.throws(() => readConfig({ ...base, MATTER_BRIDGE_ZONES: zones }), ConfigurationError, String(zones));
    }
    const config = read({ MATTER_BRIDGE_ZONES: `[{"id":2,"name":"${'ä'.repeat(16)}"},{"id":1,"name":"Zwei"}]` });
    assert.deepEqual(config.zones.map(({ id }) => id), [2, 1]);
});

test('Sollwertgrenzen: Zehntelgrad, innerhalb -20 bis 35, Minimum nicht über Maximum', () => {
    const limits = extra => { const c = read(extra); return [c.minSetpoint, c.maxSetpoint]; };
    assert.deepEqual(limits({ MATTER_BRIDGE_MIN_SETPOINT: '5.2', MATTER_BRIDGE_MAX_SETPOINT: '30.1' }), [520, 3010]);
    assert.deepEqual(limits({ MATTER_BRIDGE_MIN_SETPOINT: '-20', MATTER_BRIDGE_MAX_SETPOINT: '35' }), [-2000, 3500]);
    assert.deepEqual(limits({ MATTER_BRIDGE_MIN_SETPOINT: '16', MATTER_BRIDGE_MAX_SETPOINT: '16' }), [1600, 1600]);
    for (const [key, value] of [['MIN', '-20.1'], ['MIN', '35.1'], ['MAX', '35.1'], ['MAX', '-21'], ['MIN', '5.25'], ['MIN', '5.'],
        ['MIN', 'x'], ['MAX', '0x10'], ['MAX', '1e1'], ['MAX', ' 20'], ['MIN', 'NaN'], ['MAX', 'Infinity'], ['MIN', '']]) {
        assert.throws(() => read({ [`MATTER_BRIDGE_${key}_SETPOINT`]: value }), ConfigurationError, `${key}=${value}`);
    }
    rejects({ MATTER_BRIDGE_MIN_SETPOINT: '20', MATTER_BRIDGE_MAX_SETPOINT: '19.9' }, /nicht überschreiten/);
});

test('Port, Maximalalter, Präfix und Speicherverzeichnis werden geprüft', () => {
    for (const port of ['0', '65536', '-1', '5540.5', 'abc', '', '0x15a4', '1e3']) {
        assert.throws(() => read({ MATTER_BRIDGE_MATTER_PORT: port }), ConfigurationError, port);
    }
    assert.equal(read({ MATTER_BRIDGE_MATTER_PORT: '65535' }).matterPort, 65535);
    for (const age of ['0', '86401', '1.5', '-5', 'x']) {
        assert.throws(() => read({ MATTER_BRIDGE_MAX_AGE_SECONDS: age }), ConfigurationError, age);
    }
    assert.equal(read({ MATTER_BRIDGE_MAX_AGE_SECONDS: '90' }).maxAgeMs, 90_000);
    for (const prefix of ['', '/', 'a/+/b', 'a/#', 'mit leerzeichen', 'a\nb', 'a\u0000b']) {
        assert.throws(() => read({ MATTER_BRIDGE_TOPIC_PREFIX: prefix }), ConfigurationError, JSON.stringify(prefix));
    }
    assert.equal(read({ MATTER_BRIDGE_TOPIC_PREFIX: 'haus/heizung' }).topicPrefix, 'haus/heizung');
    assert.throws(() => read({ MATTER_BRIDGE_STORAGE_DIR: '   ' }), ConfigurationError);
    assert.throws(() => read({ MATTER_BRIDGE_LOG_LEVEL: 'laut' }), ConfigurationError);
});
