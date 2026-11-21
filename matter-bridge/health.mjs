import { once } from 'node:events';
import { lstat, unlink } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';

export const healthSocket = storageDir => join(storageDir, 'health.sock');

export async function serveHealth(storageDir, healthy, warning) {
    // Called under the instance storage lock, so a socket left by a previous
    // unclean shutdown can be removed without replacing a live listener.
    const path = healthSocket(storageDir);
    // sockaddr_un.sun_path fasst auf macOS 104, auf Linux 108 Bytes inklusive Abschluss.
    if (Buffer.byteLength(path) > 100) throw new Error('Pfad des Healthcheck-Sockets zu lang');
    try {
        if (!(await lstat(path)).isSocket()) throw new Error('Kein Healthcheck-Socket');
        await unlink(path);
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }

    const server = createServer(socket => {
        socket.on('error', () => socket.destroy());
        socket.end(healthy() ? 'ready\n' : 'unready\n');
    });
    server.listen(path);
    await once(server, 'listening');
    server.on('error', () => warning('Healthcheck-Socket ist ausgefallen'));
    return () => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
}
