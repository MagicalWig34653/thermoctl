// Ersetzt thermoctl. Beweist NICHT: den Python-Befehlsweg, die echte Domaenenlogik
// (Zeitplan, Uebersteuerung, Frostschutz) und das Verhalten des echten Brokers.
// Der Simulator bildet nur den im README beschriebenen Vertrag nach.

export async function simulate(mqtt, prefix, fixtures) {
    const zones = new Map(fixtures.map(({ id, ...state }) => [id, state]));
    const seen = [];
    // Zonen, deren Befehle der Simulator wie thermoctl ablehnt (z. B. Wert ausserhalb der Domaenengrenzen).
    const rejected = new Set();

    async function set(id, key, value) {
        zones.get(id)[key] = value;
        await mqtt.publishAsync(`${prefix}/zones/${id}/state/${key}`, value, { qos: 1, retain: true });
    }

    async function refresh() {
        for (const [id, state] of zones) {
            for (const [key, value] of Object.entries(state)) await set(id, key, value);
        }
    }

    // Der Broker der Tests zeichnet jeden Befehl auf der Leitung auf. Dieser Teilnehmer
    // simuliert nur die daraus folgenden Aenderungen auf den Zustands-Topics.
    await mqtt.subscribeAsync(`${prefix}/zones/+/command/+`, { qos: 1 });
    mqtt.on('message', (topic, payload) => {
        const [idPart, , key] = topic.slice(prefix.length + '/zones/'.length).split('/');
        const id = Number(idPart);
        const input = payload.toString();
        const state = zones.get(id);
        if (!state) return;
        const number = Number(input);
        const accepted = !rejected.has(id) && (key === 'temporary_setpoint'
            ? state.operating_mode !== 'off' && /^-?\d+(?:\.\d)?$/.test(input) && number >= -20 && number <= 35
            : key === 'operating_mode' && ['auto', 'off'].includes(input));
        seen.push({ id, key, input, accepted });
        if (accepted) {
            const pending = key === 'temporary_setpoint'
                ? set(id, 'setpoint', input).then(() => set(id, 'override_active', 'true'))
                : set(id, 'operating_mode', input);
            pending.catch(error => {
                if (mqtt.connected) throw error;
            });
        }
    });

    return { set, refresh, seen, rejected, state: id => zones.get(id) };
}
