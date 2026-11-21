import { ConfigurationError, readConfig } from './config.mjs';

process.umask(0o077);

const warning = message => console.error(message);
// Auf stdout steht ausschliesslich der Kopplungscode (einmalig). Alle Meldungen gehen nach stderr.
const info = message => console.error(message);
let stop;
let stopping = false;

function shutdown() {
    stopping = true;
    if (!stop) return;
    const close = stop;
    stop = undefined;
    void close().catch(() => {
        warning('Matter-Bridge konnte nicht vollständig beendet werden');
        process.exitCode = 1;
    });
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

try {
    const config = readConfig();
    const { Boot, Logger } = await import('@matter/main');
    await Boot.ready;
    // Eigene Meldungen der Bridge folgen LOG_LEVEL (Standard info). Matter.js ist laut genug:
    // dort gilt bei info nur warn. Den Kopplungscode gibt Matter.js nie aus (service.mjs ersetzt
    // initiateCommissioning), die Bridge schreibt ihn selbst genau einmal.
    Logger.level = config.logLevel === 'info' ? 'warn' : config.logLevel;
    const { startBridge } = await import('./service.mjs');
    const { serveHealth } = await import('./health.mjs');
    const bridge = await startBridge(config, {
        warning,
        info: ['error', 'warn'].includes(config.logLevel) ? () => {} : info,
        output: code => new Promise((resolve, reject) => {
            process.stdout.write(`Matter-Kopplungscode: ${code}\n`, error => error ? reject(error) : resolve());
        }),
    });
    try {
        const closeHealth = await serveHealth(config.storageDir, bridge.healthy, warning);
        stop = async () => {
            try {
                await closeHealth();
            } finally {
                await bridge.close();
            }
        };
    } catch {
        await bridge.close();
        throw new Error('Healthcheck-Socket konnte nicht geöffnet werden. Speicherpfad und Rechte prüfen');
    }
    info(`Matter-Bridge gestartet (${config.zones.length} Zone${config.zones.length === 1 ? '' : 'n'}, Port ${config.matterPort})`);
    if (stopping) shutdown();
} catch (error) {
    warning(error.message);
    process.exitCode = 1;
}
