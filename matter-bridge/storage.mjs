import { constants } from 'node:fs';
import { access, lstat, mkdir, open, readdir, readFile, rename, rm } from 'node:fs/promises';
import { createHash, createHmac, randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fromJson } from '@matter/main';
import { NodeJsFilesystem } from '@matter/nodejs';

export const nodeId = 'thermoctl-bridge';
export class StorageError extends Error {}

const manifestName = 'instance.json';

function identity(instanceKey, id) {
    return createHmac('sha256', instanceKey).update(`zone:${id}`).digest('hex').slice(0, 32);
}

export function pairingFingerprint({ passcode, discriminator }) {
    return createHash('sha256').update(`${passcode}:${discriminator}`).digest('hex');
}

async function replaceManifest(dir, manifest) {
    const temp = join(dir, `.instance-${randomBytes(8).toString('hex')}.tmp`);
    const file = await open(temp, 'wx', 0o600);
    try {
        try {
            await file.writeFile(JSON.stringify(manifest) + '\n');
            await file.sync();
        } finally {
            await file.close();
        }
        await rename(temp, join(dir, manifestName));
        const directory = await open(dir, 'r');
        try {
            await directory.sync();
        } finally {
            await directory.close();
        }
    } finally {
        await rm(temp, { force: true });
    }
}

function validateManifest(value) {
    if (!value || value.version !== 1 || typeof value.key !== 'string' || !/^[a-f0-9]{64}$/.test(value.key)
        || typeof value.pairingFingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(value.pairingFingerprint)
        || typeof value.codeIssued !== 'boolean' || !Array.isArray(value.allocations)) {
        throw new Error('Ungültiges Identitätsregister');
    }
    const ids = new Set();
    for (const [index, id] of value.allocations.entries()) {
        if (!Number.isSafeInteger(id) || id < 1 || ids.has(id) || index + 2 > 65534) {
            throw new Error('Ungültige Endpoint-Zuordnung');
        }
        ids.add(id);
    }
}

async function inspectDatabase(dir, manifest) {
    const path = join(dir, 'storage.db');
    if (!(await lstat(path)).isFile()) throw new Error('Matter-Datenbank fehlt');
    await access(dir, constants.W_OK);
    await access(path, constants.W_OK);
    const descriptor = JSON.parse(await readFile(join(dir, 'driver.json'), 'utf8'));
    if (descriptor.kind !== 'sqlite' || descriptor.type !== 'kv') throw new Error('Falscher Matter-Speichertreiber');

    const db = new DatabaseSync(path, { readOnly: true });
    try {
        if (db.prepare('PRAGMA quick_check').get().quick_check !== 'ok') throw new Error('SQLite-Integritätsfehler');
        const commissioning = {};
        for (const row of db.prepare(
            "SELECT context, key, value_json FROM kvstore WHERE value_type = 'json'",
        ).iterate()) {
            // Matter.js fills missing credentials with fresh random values if started.
            // Check their presence and validity before any Matter.js write.
            const value = fromJson(row.value_json);
            if (row.context === 'root.commissioning' && ['passcode', 'discriminator'].includes(row.key)) {
                commissioning[row.key] = value;
            }
        }
        if (!Number.isInteger(commissioning.passcode) || !Number.isInteger(commissioning.discriminator)
            || pairingFingerprint(commissioning) !== manifest.pairingFingerprint) {
            throw new StorageError('Matter-Speicher enthält keine gültige Kopplungsidentität');
        }
    } finally {
        db.close();
    }
}

function classify(error, dir) {
    if (error instanceof StorageError) return error;
    if (['EACCES', 'EPERM', 'EROFS'].includes(error?.code)) {
        return new StorageError(`Speicherverzeichnis ${dir} ist nicht beschreibbar (${error.code}). Rechte und Volume prüfen`);
    }
    if (error?.code === 'ENOSPC') return new StorageError(`Speicherverzeichnis ${dir}: Datenträger voll`);
    return new StorageError(
        'Matter-Speicher ist beschädigt oder unvollständig. Die Bridge ändert nichts daran, damit keine Kopplung '
        + 'verloren geht. Datensicherung zurückspielen oder, wenn nie gekoppelt wurde, das Verzeichnis leeren');
}

export async function openStorage(config) {
    const dir = config.storageDir;
    const filesystem = new NodeJsFilesystem(dir);
    let release;
    try {
        await mkdir(dir, { recursive: true, mode: 0o700 });
        // Frueh und mit klarer Meldung: Spaeter wuerde erst das Schreiben des Registers scheitern.
        await access(dir, constants.W_OK);
        try {
            release = await filesystem.directory('instance-lock').lock();
        } catch (error) {
            if (['EACCES', 'EPERM', 'EROFS', 'ENOENT'].includes(error?.code)) throw error;
            throw new StorageError('Speicherverzeichnis ist von einer anderen Instanz belegt. Läuft die Bridge doppelt?');
        }
        const contents = await readdir(dir);
        let manifest;
        if (contents.includes(manifestName)) {
            const path = join(dir, manifestName);
            if (!(await lstat(path)).isFile()) throw new Error('Ungültiges Identitätsregister');
            await access(path, constants.W_OK);
            manifest = JSON.parse(await readFile(path, 'utf8'));
            validateManifest(manifest);
            await inspectDatabase(join(dir, nodeId), manifest);
        } else {
            if (contents.some(name => name !== 'instance-lock')) {
                throw new StorageError(
                    `Speicherverzeichnis ist nicht leer, aber ${manifestName} fehlt. Die Bridge überschreibt keine vorhandene `
                    + 'Kopplung. Datensicherung zurückspielen oder, wenn nie gekoppelt wurde, das Verzeichnis leeren');
            }
            manifest = { version: 1, key: randomBytes(32).toString('hex'), allocations: [], codeIssued: false };
        }

        const allocations = new Map(manifest.allocations.map((id, index) => [id, index + 2]));
        const entries = new Map();
        for (const { id } of config.zones) {
            if (!allocations.has(id)) {
                const number = manifest.allocations.length + 2;
                if (number > 65534) throw new StorageError('Matter-Speicher hat keine freien Endpoint-Nummern');
                allocations.set(id, number);
                manifest.allocations.push(id);
            }
            entries.set(id, { key: identity(manifest.key, id), number: allocations.get(id) });
        }
        return {
            filesystem,
            entries,
            async save(node) {
                const fingerprint = pairingFingerprint(node.state.commissioning);
                if (manifest.pairingFingerprint !== undefined && fingerprint !== manifest.pairingFingerprint) {
                    throw new StorageError('Matter-Speicher enthält eine andere Kopplungsidentität');
                }
                manifest.pairingFingerprint = fingerprint;
                await replaceManifest(dir, manifest).catch(error => { throw classify(error, dir); });
            },
            async issueCode(node, output) {
                if (manifest.codeIssued || node.lifecycle.isCommissioned) return;
                const code = node.state.commissioning.pairingCodes.manualPairingCode;
                manifest.codeIssued = true;
                await replaceManifest(dir, manifest).catch(error => { throw classify(error, dir); });
                await output(code);
            },
            release,
        };
    } catch (error) {
        await release?.();
        throw classify(error, dir);
    }
}
