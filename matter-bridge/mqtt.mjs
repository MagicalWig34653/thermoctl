import { connect } from 'mqtt';

export function connectMqtt(config, routes, reset, log) {
    let client;
    let retry;
    let stopped = false;
    let outageReported = false;
    let wasConnected = false;

    function open() {
        const current = connect(config.mqttUrl, {
            username: config.mqttUsername,
            password: config.mqttPassword,
            clean: true,
            reconnectPeriod: 0,
            resubscribe: false,
            queueQoSZero: false,
            manualConnect: true,
            log: () => {},
        });
        client = current;
        current.on('error', error => {
            // Nur Code oder Kurztext: Zugangsdaten stehen in keiner mqtt.js-Fehlermeldung,
            // aber der Text bleibt trotzdem kurz und ohne Verbindungsdetails.
            const reason = String(error?.code ?? error?.message ?? 'unbekannt').slice(0, 80);
            if (!outageReported) log.warning(`MQTT-Verbindung fehlgeschlagen (${reason}). Neuer Versuch jede Sekunde`);
            outageReported = true;
        });
        current.on('message', (topic, payload, packet) => {
            if (client === current && !packet.retain) routes.get(topic)?.(payload);
        });
        current.once('connect', () => {
            outageReported = false;
            wasConnected = true;
            log.info('MQTT verbunden');
            reset();
            current.subscribe([...routes.keys()], { qos: 1 }, (error, grants) => {
                if (error || grants.some(grant => grant.qos >= 128)) {
                    log.warning('MQTT-Abonnement fehlgeschlagen. Broker-ACLs prüfen');
                }
            });
        });
        current.once('close', () => {
            if (client !== current) return;
            client = undefined;
            if (!stopped && wasConnected) log.info('MQTT getrennt');
            wasConnected = false;
            reset();
            // Discard this client's QoS 1 outgoing store. No earlier command may
            // be replayed on a fresh connection after the zone's mode changed.
            current.end(true);
            if (!stopped) retry = setTimeout(open, 1000);
        });
        current.connect();
    }

    open();
    return {
        get connected() {
            return client?.connected === true;
        },
        publish(topic, payload) {
            if (!client?.connected) return;
            client.publish(topic, payload, { qos: 1, retain: false }, error => {
                if (error && client?.connected) log.warning('MQTT-Befehl konnte nicht bestätigt werden');
            });
        },
        async close() {
            stopped = true;
            clearTimeout(retry);
            const current = client;
            client = undefined;
            await current?.endAsync(true);
        },
    };
}
