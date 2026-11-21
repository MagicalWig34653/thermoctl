import { Endpoint } from '@matter/main';
import { AggregatorEndpoint } from '@matter/main/endpoints/aggregator';
import { ThermostatDevice } from '@matter/main/devices/thermostat';
import { ThermostatServer } from '@matter/main/behaviors/thermostat';
import { BridgedDeviceBasicInformationServer } from '@matter/main/behaviors/bridged-device-basic-information';
import { Thermostat } from '@matter/main/clusters/thermostat';
import { hasRemoteActor } from '@matter/protocol';
import { Status, StatusResponse, StatusResponseError } from '@matter/types';

const { Off, Heat } = Thermostat.SystemMode;
const thermostatId = Thermostat.Cluster.id;
const modeAttribute = String(Thermostat.Cluster.attributes.systemMode.id);
const targetAttribute = String(Thermostat.Cluster.attributes.occupiedHeatingSetpoint.id);
// Ohne gueltigen, frischen Sollwert und Modus gibt es nichts Verlaessliches anzuzeigen:
// die Zone gilt als nicht erreichbar. Istwert und Heizbedarf duerfen einzeln ausfallen
// (Fuehlerausfall: thermoctl veroeffentlicht dann keinen Istwert) -- die Kachel zeigt
// dann "keine Temperatur" statt "keine Antwort", und Aus/Heizen bleibt bedienbar.
const requiredStates = ['setpoint', 'operating_mode'];
// override_active wird gelesen und geprueft, hat aber kein Matter-Gegenstueck
// (nur Diagnose, siehe README).
const states = ['current_temperature', ...requiredStates, 'would_heat', 'override_active'];
const debounceMs = 200;
const reconcileMs = 500;

const backends = new WeakMap();

export function parseState(key, payload, { minSetpoint, maxSetpoint }) {
    if (!Buffer.isBuffer(payload) || payload.length === 0 || payload.length > 24) return null;
    // latin1 maps every byte directly. Non-ASCII never matches the allowlist.
    const value = payload.toString('latin1');
    switch (key) {
        case 'operating_mode':
            if (value === 'off') return Off;
            return value === 'manual' || value === 'auto' ? Heat : null;
        case 'would_heat':
        case 'override_active':
            return value === 'true' ? true : value === 'false' ? false : null;
        case 'current_temperature': {
            if (!/^-?\d{1,3}(?:\.\d{1,2})?$/.test(value)) return null;
            const measured = Math.round(Number(value) * 100);
            return measured >= -27315 && measured <= 32767 ? measured : null;
        }
        case 'setpoint': {
            if (!/^-?\d{1,2}(?:\.\d)?$/.test(value)) return null;
            const target = Math.round(Number(value) * 100);
            return target >= minSetpoint && target <= maxSetpoint ? target : null;
        }
    }
}

function afterCommit(transaction, backend, attribute, value) {
    // Matter.js can accept an assignment without changing the stored value.
    // An explicit Heat in manual or a repeat of the current target still carries intent.
    transaction.addParticipants({
        toString: () => `MQTT zone ${backend.id} attribute ${attribute}`,
        postCommit: () => backend.command(attribute, value),
    });
}

class Heating extends ThermostatServer.with('Heating') {
    setpointRaiseLower({ mode, amount }) {
        if (mode !== Thermostat.SetpointRaiseLowerMode.Heat) {
            throw new StatusResponse.InvalidCommandError('Nur Heizen ist verfügbar');
        }
        const backend = backends.get(this.endpoint);
        const target = this.state.occupiedHeatingSetpoint + amount * 10;
        backend.validate(targetAttribute, target);
        this.state.occupiedHeatingSetpoint = target;
        afterCommit(this.context.transaction, backend, targetAttribute, target);
    }
}

const thermostat = ThermostatDevice.with(Heating, BridgedDeviceBasicInformationServer);

function guardWrites(node, endpoint, backend) {
    // This protocol hook runs after Matter's ACL checks and before its transactional write.
    // $Changing/$Changed alone omit writes whose value equals the existing attribute.
    const cluster = node.protocol[endpoint.number][thermostatId];
    const open = cluster.openForWrite.bind(cluster);
    cluster.openForWrite = async session => {
        const state = await open(session);
        if (!hasRemoteActor(session)) return state;
        return new Proxy(state, {
            set(target, attribute, value) {
                backend.validate(attribute, value);
                const written = Reflect.set(target, attribute, value);
                if (written) afterCommit(session.transaction, backend, attribute, value);
                return written;
            },
        });
    };
}

class ZoneBackend {
    #endpoint;
    #config;
    #publish;
    #log;
    #cache = new Map();
    #expiry;
    #debounce;
    #fallback;
    #modeTimer;
    #syncing;
    #dirty = false;
    #closed = false;
    #modeRequested;
    #announced;

    constructor(endpoint, id, config, publish, log) {
        this.#endpoint = endpoint;
        this.id = id;
        this.#config = config;
        this.#publish = publish;
        this.#log = log;
    }

    #value(key, now = performance.now()) {
        const entry = this.#cache.get(key);
        return entry && now - entry.at < this.#config.maxAgeMs ? entry.value : null;
    }

    get reachable() {
        return requiredStates.every(key => this.#value(key) !== null);
    }

    #canSetpoint() {
        // Ein noch unbestaetigter Matter-Moduswunsch geht vor dem zuletzt gemeldeten Modus,
        // damit "Heizen" und gleich danach ein Sollwert funktioniert und "Aus" sofort sperrt.
        const mode = this.#modeRequested ?? this.#value('operating_mode');
        return this.reachable && mode === Heat;
    }

    validate(attribute, value) {
        if (attribute !== modeAttribute && attribute !== targetAttribute) {
            throw new StatusResponse.UnsupportedWriteError('Dieses Thermostat-Attribut ist fest vorgegeben');
        }
        if (!this.reachable) throw new StatusResponse.InvalidInStateError('Aktueller MQTT-Zustand fehlt');
        if (attribute === modeAttribute && value !== Off && value !== Heat) {
            throw StatusResponseError.create(Status.ConstraintError, 'Nur Aus und Heizen sind zulässig');
        }
        if (attribute === targetAttribute) {
            if (!this.#canSetpoint()) throw new StatusResponse.InvalidInStateError('Zone steht auf Aus');
            if (!Number.isInteger(value) || value % 10 || value < this.#config.minSetpoint
                || value > this.#config.maxSetpoint) {
                throw StatusResponseError.create(Status.ConstraintError, 'Sollwert muss auf einem erlaubten Zehntelgrad liegen');
            }
        }
    }

    receive(key, payload) {
        if (key === 'operating_mode') {
            this.#modeRequested = undefined;
            clearTimeout(this.#modeTimer);
        }
        const value = parseState(key, payload, this.#config);
        const previous = this.#cache.get(key);
        if (value === null && previous?.value !== null) {
            // Nie den Inhalt loggen: er ist beliebig gross und beliebig geformt.
            this.#log.warning(`Zone ${this.id}: ungültiger Wert für ${key} (${Buffer.byteLength(payload)} Bytes) verworfen`);
        }
        this.#cache.set(key, { value, at: performance.now() });
        if (!this.#canSetpoint()) {
            clearTimeout(this.#debounce);
            this.#debounce = undefined;
        }
        if (key !== 'override_active') this.#sync();
    }

    reset() {
        this.#cache.clear();
        this.#modeRequested = undefined;
        clearTimeout(this.#debounce);
        clearTimeout(this.#fallback);
        clearTimeout(this.#modeTimer);
        this.#debounce = undefined;
        this.#sync();
    }

    command(attribute, value) {
        if (this.#closed) return;
        if (attribute === modeAttribute) {
            this.#sendMode(value);
            return;
        }
        // Apple Home schreibt je Rad-Schritt einen Wert. Ohne Entprellung wuerde jeder Zwischenwert
        // eine eigene befristete Uebersteuerung in thermoctl erzeugen (Audit, Schaltlogik).
        // Letzter Wert gewinnt; 200 ms sind laenger als ein Radschritt, kuerzer als eine Rueckmeldung.
        clearTimeout(this.#debounce);
        this.#debounce = setTimeout(() => {
            this.#debounce = undefined;
            if (this.#canSetpoint()) this.#send('temporary_setpoint', String(value / 100));
            else this.#sync();
        }, debounceMs);
    }

    #sendMode(value) {
        if (value === Off) {
            clearTimeout(this.#debounce);
            this.#debounce = undefined;
        }
        if (!this.reachable) {
            this.#sync();
            return;
        }
        this.#modeRequested = value;
        this.#send('operating_mode', value === Off ? 'off' : 'auto');
        // Eigener Timer: Ein Sollwert-Send ersetzt den Abgleich-Timer (#fallback). Haengt das Zuruecksetzen
        // des Moduswunsches daran, bleibt er bei "Heizen" und Radschritt innerhalb von 500 ms ewig stehen,
        // und die Bridge nimmt weiter Sollwerte an, obwohl thermoctl "off" meldet.
        clearTimeout(this.#modeTimer);
        this.#modeTimer = setTimeout(() => {
            this.#modeRequested = undefined;
            this.#sync();
        }, reconcileMs);
    }

    #send(command, payload) {
        this.#publish(command, payload);
        clearTimeout(this.#fallback);
        // Es gibt keine fachliche Quittung. Nach reconcileMs gilt wieder der zuletzt empfangene
        // Zustand -- ein abgelehntes Rad springt so zurueck, ein angenommenes ist laengst bestaetigt.
        this.#fallback = setTimeout(() => {
            this.#sync();
        }, reconcileMs);
    }

    #sync() {
        if (this.#closed) return;
        this.#dirty = true;
        if (this.#syncing) return;
        this.#syncing = Promise.resolve().then(async () => {
            while (this.#dirty && !this.#closed) {
                this.#dirty = false;
                const now = performance.now();
                clearTimeout(this.#expiry);
                const next = Math.min(...states.map(key => this.#cache.get(key))
                    .filter(entry => entry && entry.value !== null)
                    .map(entry => entry.at + this.#config.maxAgeMs).filter(at => at > now));
                if (Number.isFinite(next)) this.#expiry = setTimeout(() => this.#sync(), Math.max(1, Math.ceil(next - now)));
                const reachable = requiredStates.every(key => this.#value(key, now) !== null);
                if (reachable !== this.#announced) {
                    this.#announced = reachable;
                    this.#log.info(`Zone ${this.id}: ${reachable ? 'erreichbar' : 'nicht erreichbar'}`);
                }
                await this.#endpoint.set({
                    bridgedDeviceBasicInformation: { reachable },
                    thermostat: {
                        localTemperature: reachable ? this.#value('current_temperature', now) : null,
                        thermostatRunningState: { heat: reachable && this.#value('would_heat', now) === true },
                        ...(reachable ? {
                            // Waehrend ein Rad gedreht wird, gehoert der Wert dem Nutzer.
                            ...(this.#debounce ? {} : { occupiedHeatingSetpoint: this.#value('setpoint', now) }),
                            systemMode: this.#value('operating_mode', now),
                        } : {}),
                    },
                });
            }
        }).catch(() => {
            this.#log.warning(`Zustandsabgleich für Zone ${this.id} fehlgeschlagen`);
        }).finally(() => {
            this.#syncing = undefined;
            if (this.#dirty) this.#sync();
        });
    }

    async close() {
        this.#closed = true;
        clearTimeout(this.#debounce);
        clearTimeout(this.#expiry);
        clearTimeout(this.#fallback);
        clearTimeout(this.#modeTimer);
        await this.#syncing;
    }
}

export async function attachZones(node, config, entries, publish, log) {
    const aggregator = await node.add(new Endpoint(AggregatorEndpoint, { id: 'zones', number: 1 }));
    const zones = new Map();
    const routes = new Map();
    for (const { id, name } of config.zones) {
        const { key, number } = entries.get(id);
        const base = `${config.topicPrefix}/zones/${id}`;
        const endpoint = await aggregator.add(new Endpoint(thermostat, {
            id: key,
            number,
            bridgedDeviceBasicInformation: { nodeLabel: name, uniqueId: key, reachable: false },
            thermostat: {
                controlSequenceOfOperation: Thermostat.ControlSequenceOfOperation.HeatingOnly,
                absMinHeatSetpointLimit: config.minSetpoint,
                absMaxHeatSetpointLimit: config.maxSetpoint,
                minHeatSetpointLimit: config.minSetpoint,
                maxHeatSetpointLimit: config.maxSetpoint,
                localTemperature: null,
                systemMode: Off,
                occupiedHeatingSetpoint: Math.max(config.minSetpoint, Math.min(2100, config.maxSetpoint)),
                thermostatRunningState: { heat: false },
            },
        }));
        await endpoint.set({ bridgedDeviceBasicInformation: { nodeLabel: name, reachable: false } });
        const backend = new ZoneBackend(endpoint, id, config,
            (command, payload) => publish(`${base}/command/${command}`, payload), log);
        backends.set(endpoint, backend);
        zones.set(id, { endpoint, backend });
        guardWrites(node, endpoint, backend);
        for (const key of states) {
            routes.set(`${base}/state/${key}`, payload => backend.receive(key, payload));
        }
    }
    return { aggregator, zones, routes };
}
