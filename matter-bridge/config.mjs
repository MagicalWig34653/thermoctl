import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export class ConfigurationError extends Error {}

const fields = {
    mqttUrl: 'MQTT_URL',
    mqttUsername: 'MQTT_USERNAME',
    mqttPassword: 'MQTT_PASSWORD',
    topicPrefix: 'TOPIC_PREFIX',
    storageDir: 'STORAGE_DIR',
    matterPort: 'MATTER_PORT',
    zones: 'ZONES',
    minSetpoint: 'MIN_SETPOINT',
    maxSetpoint: 'MAX_SETPOINT',
    maxAgeSeconds: 'MAX_AGE_SECONDS',
    logLevel: 'LOG_LEVEL',
};

function invalid(key, rule) {
    throw new ConfigurationError(`Konfiguration ${key} (MATTER_BRIDGE_${fields[key]}) ${rule}`);
}

function numeric(key, input, pattern, min, max) {
    const value = typeof input === 'number' || typeof input === 'string' ? String(input) : '';
    if (!pattern.test(value) || Number(value) < min || Number(value) > max) {
        invalid(key, `erwartet eine Zahl von ${min} bis ${max} im angegebenen Format`);
    }
    return Number(value);
}

function decodeJson(text, source) {
    try {
        return JSON.parse(text);
    } catch {
        throw new ConfigurationError(`${source} enthält ungültiges JSON`);
    }
}

export function readConfig(env = process.env) {
    const path = env.MATTER_BRIDGE_CONFIG;
    let file = {};
    if (path !== undefined) {
        let text;
        try {
            text = readFileSync(path, 'utf8');
        } catch {
            throw new ConfigurationError('Datei aus MATTER_BRIDGE_CONFIG kann nicht gelesen werden');
        }
        file = decodeJson(text, 'Datei aus MATTER_BRIDGE_CONFIG');
        if (!file || typeof file !== 'object' || Array.isArray(file)
            || Object.keys(file).some(key => !Object.hasOwn(fields, key))) {
            throw new ConfigurationError('Datei aus MATTER_BRIDGE_CONFIG erwartet ein Objekt mit bekannten Feldern');
        }
    }
    const raw = Object.fromEntries(Object.entries(fields).map(([key, suffix]) => {
        const value = env[`MATTER_BRIDGE_${suffix}`];
        return [key, value === undefined ? file[key] : key === 'zones' ? decodeJson(value, suffix) : value];
    }));

    let mqttUrl;
    try {
        mqttUrl = new URL(raw.mqttUrl);
    } catch {
        invalid('mqttUrl', 'erwartet eine MQTT-URL');
    }
    if (typeof raw.mqttUrl !== 'string' || !['mqtt:', 'mqtts:'].includes(mqttUrl.protocol)
        || !mqttUrl.hostname || mqttUrl.username || mqttUrl.password
        || !['', '/'].includes(mqttUrl.pathname) || mqttUrl.search || mqttUrl.hash
        || /[\s\x00-\x1f\x7f]/.test(raw.mqttUrl) || mqttUrl.port === '0') {
        invalid('mqttUrl', 'erwartet mqtt:// oder mqtts:// mit Host und optionalem Port. Zugangsdaten haben eigene Felder');
    }
    for (const key of ['mqttUsername', 'mqttPassword']) {
        if (raw[key] !== undefined && (typeof raw[key] !== 'string' || raw[key].includes('\0')
            || Buffer.byteLength(raw[key]) > 65535)) {
            invalid(key, 'erwartet eine gültige MQTT-Zeichenkette');
        }
    }
    if (raw.mqttPassword !== undefined && !raw.mqttUsername) invalid('mqttPassword', 'erfordert mqttUsername');

    const topicPrefix = raw.topicPrefix === undefined ? 'thermoctl'
        : typeof raw.topicPrefix === 'string' ? raw.topicPrefix.replace(/^\/+|\/+$/g, '') : '';
    // Strenger als thermoctl (dort nur: nicht leer, keine Wildcards, kein NUL): keine leeren Ebenen und kein
    // fuehrendes $ (reserviert fuer Broker-Topics wie $SYS, dort veroeffentlicht thermoctl nie).
    if (!topicPrefix || topicPrefix.split('/').includes('') || topicPrefix.startsWith('$')
        || /[+#\x00-\x20\x7f]/.test(topicPrefix) || Buffer.byteLength(topicPrefix) > 1024
        || Buffer.from(topicPrefix).toString() !== topicPrefix) {
        invalid('topicPrefix', 'erwartet ein gültiges MQTT-Präfix ohne Wildcards');
    }

    const storageDir = raw.storageDir === undefined ? './matter-storage' : raw.storageDir;
    if (typeof storageDir !== 'string' || !storageDir.trim() || storageDir.includes('\0')) {
        invalid('storageDir', 'erwartet einen Verzeichnispfad');
    }
    if (!Array.isArray(raw.zones) || raw.zones.length === 0 || raw.zones.length > 65533) {
        invalid('zones', 'erwartet eine Liste mit 1 bis 65533 Zonen');
    }
    const ids = new Set();
    const zones = raw.zones.map(zone => {
        if (!zone || typeof zone !== 'object' || Object.keys(zone).length !== 2
            || !Number.isSafeInteger(zone.id) || zone.id < 1 || ids.has(zone.id)
            || typeof zone.name !== 'string' || !zone.name.trim() || /[\x00-\x1f\x7f]/.test(zone.name)
            || Buffer.from(zone.name).toString() !== zone.name || Buffer.byteLength(zone.name) > 32) {
            invalid('zones', 'erwartet je Zone eine eindeutige positive Integer-ID und einen Namen mit 1 bis 32 UTF-8-Bytes');
        }
        ids.add(zone.id);
        return { id: zone.id, name: zone.name };
    });
    const decimal = /^-?\d+(?:\.\d)?$/;
    const integer = /^\d+$/;
    const minSetpoint = numeric('minSetpoint', raw.minSetpoint === undefined ? -20 : raw.minSetpoint, decimal, -20, 35) * 100;
    const maxSetpoint = numeric('maxSetpoint', raw.maxSetpoint === undefined ? 35 : raw.maxSetpoint, decimal, -20, 35) * 100;
    if (minSetpoint > maxSetpoint) invalid('minSetpoint', 'darf maxSetpoint nicht überschreiten');

    const logLevel = raw.logLevel === undefined ? 'info' : raw.logLevel;
    if (!['error', 'warn', 'info', 'debug'].includes(logLevel)) {
        invalid('logLevel', 'erwartet error, warn, info oder debug');
    }

    return {
        logLevel,
        mqttUrl: mqttUrl.href,
        mqttUsername: raw.mqttUsername,
        mqttPassword: raw.mqttPassword,
        topicPrefix,
        storageDir: resolve(storageDir),
        matterPort: numeric('matterPort', raw.matterPort === undefined ? 5540 : raw.matterPort, integer, 1, 65535),
        maxAgeMs: numeric('maxAgeSeconds', raw.maxAgeSeconds === undefined ? 300 : raw.maxAgeSeconds, integer, 1, 86400) * 1000,
        minSetpoint: Math.round(minSetpoint),
        maxSetpoint: Math.round(maxSetpoint),
        zones,
    };
}
