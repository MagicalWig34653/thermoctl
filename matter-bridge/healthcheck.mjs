import { createConnection } from 'node:net';
import { readConfig } from './config.mjs';
import { healthSocket } from './health.mjs';

try {
    const socket = createConnection(healthSocket(readConfig().storageDir));
    socket.setTimeout(2000, () => socket.destroy());
    let response = '';
    socket.on('data', chunk => {
        response += chunk;
        if (response.length > 8) socket.destroy();
    });
    socket.on('error', () => { process.exitCode = 1; });
    socket.on('close', () => { if (response !== 'ready\n') process.exitCode = 1; });
} catch {
    process.exitCode = 1;
}
