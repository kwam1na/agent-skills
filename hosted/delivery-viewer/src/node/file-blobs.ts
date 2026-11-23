// Write-once snapshot bytes on a local filesystem. A put writes a temp file in the data directory and fsyncs it,
// link()s it to its content-addressed name, which fails if that name exists, and fsyncs the directory, all before
// the core's metadata transaction runs. Existing identical bytes count as success.
import { mkdir, open, link, unlink, readFile, readdir, rm } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import type { BlobStore, SnapshotBody } from '../core/ports';

/** The filesystem steps a put takes, injectable so a test can record their order. */
export interface BlobFiles {
  /** Creates `path` exclusively, writes `bytes`, fsyncs and closes it. */
  writeSynced(path: string, bytes: Uint8Array): Promise<void>;
  link(from: string, to: string): Promise<void>;
  syncDirectory(path: string): Promise<void>;
  unlink(path: string): Promise<void>;
  read(path: string): Promise<Uint8Array<ArrayBuffer> | null>;
}

export const realFiles: BlobFiles = {
  async writeSynced(path, bytes) {
    const handle = await open(path, 'wx', 0o600);
    try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
  },
  link: (from, to) => link(from, to),
  async syncDirectory(path) {
    const handle = await open(path, 'r');
    try { await handle.sync(); } finally { await handle.close(); }
  },
  unlink: path => unlink(path),
  async read(path) {
    try {
      const buffer = await readFile(path);
      return new Uint8Array(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)) as Uint8Array<ArrayBuffer>;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw error;
    }
  },
};

const KEY = /^([a-f0-9]{64})\/([1-9][0-9]*)\/([1-9][0-9]*)\/([a-f0-9]{64})$/;
const same = (a: Uint8Array, b: Uint8Array) => a.byteLength === b.byteLength && a.every((value, i) => value === b[i]);

export class FileBlobs implements BlobStore {
  private constructor(private readonly blobs: string, private readonly staging: string, private readonly files: BlobFiles) {}

  /** Makes `blobs/` and `staging/` under `dataDir`, clearing temp files a crashed process left in staging. */
  static async open(dataDir: string, files: BlobFiles = realFiles): Promise<FileBlobs> {
    const blobs = join(dataDir, 'blobs'), staging = join(dataDir, 'staging');
    await mkdir(blobs, { recursive: true, mode: 0o700 });
    await mkdir(staging, { recursive: true, mode: 0o700 });
    for (const name of await readdir(staging)) await rm(join(staging, name), { force: true });
    return new FileBlobs(blobs, staging, files);
  }

  private path(key: string): string {
    const match = KEY.exec(key);
    if (!match) throw new Error('not a snapshot key');
    return join(this.blobs, match.slice(1).join('.'));
  }

  async put(key: string, bytes: Uint8Array): Promise<void> {
    const target = this.path(key);
    const temp = join(this.staging, randomBytes(16).toString('hex'));
    await this.files.writeSynced(temp, bytes);
    try {
      try {
        await this.files.link(temp, target);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        const existing = await this.files.read(target);
        if (!existing || !same(existing, bytes)) throw new Error('a snapshot key already holds other bytes');
      }
      await this.files.syncDirectory(this.blobs);
    } finally {
      await this.files.unlink(temp).catch(() => {});
    }
  }

  async get(key: string): Promise<SnapshotBody | null> {
    return this.files.read(this.path(key));
  }
}
