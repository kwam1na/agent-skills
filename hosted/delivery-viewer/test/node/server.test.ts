// The portable server's own guarantees: durability settings and write order, a fault between the blob and the
// transaction, the startup lock, a crash, the HTTP failures Node meets before the core, and provisioned tokens.
import { connect, type Socket } from 'node:net';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { Deliveries, endpoint } from '../../src/core/deliveries';
import { createService } from '../../src/core/service';
import type { MetadataStore } from '../../src/core/ports';
import { RENDERER, digest } from '../../src/protocol';
import { realFiles, type BlobFiles } from '../../src/node/file-blobs';
import { createNodeServer, type Limits } from '../../src/node/http';
import { cases } from '../fixtures.generated';
import { builtNode, credentials, startNode } from '../launcher.mjs';
import { withStores } from './stores';

const REPO = 'repo-node';
const hex = (n: number) => n.toString(16).padStart(64, '0');
const id = () => crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
const commit = (sequence: number, n: number) => ({ epoch: 1, sequence, digest: hex(n), rendererVersion: RENDERER } as const);
const bytes = (n: number) => new TextEncoder().encode(`{"cut":${n}}`);
const blobName = (publicId: string, sequence: number, n: number) => `${publicId}.1.${sequence}.${hex(n)}`;

describe('portable stores', () => {
  it('opens its database with synchronous=FULL and an exclusive lock, read back from the open database', async () => {
    await withStores(async ({ metadata }) => {
      expect(metadata.db.prepare('PRAGMA synchronous').get()).toEqual({ synchronous: 2 });
      expect(metadata.db.prepare('PRAGMA locking_mode').get()).toEqual({ locking_mode: 'exclusive' });
    });
  });

  it('fsyncs the temp file, links it, fsyncs the directory, and only then runs the metadata transaction', async () => {
    const events: string[] = [];
    const files: BlobFiles = {
      ...realFiles,
      async writeSynced(path, data) { await realFiles.writeSynced(path, data); events.push('temp-fsync'); },
      async link(from, to) { await realFiles.link(from, to); events.push('link'); },
      async syncDirectory(path) { await realFiles.syncDirectory(path); events.push('dir-fsync'); },
    };
    await withStores(async ({ metadata, blobs }) => {
      const recorded: MetadataStore = { transact: (publicId, run) => { events.push('transaction'); return metadata.transact(publicId, run); } };
      const deliveries = new Deliveries(recorded, blobs, { now: () => 1_000 });
      const publicId = id();
      deliveries.session(publicId, REPO, { mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(1) });
      events.length = 0;
      await deliveries.commit(publicId, REPO, commit(1, 7), hex(8), bytes(7));
      // The first transaction checks admissibility before any write; the commit's own comes after the directory fsync.
      expect(events).toEqual(['transaction', 'temp-fsync', 'link', 'dir-fsync', 'transaction']);
    }, files);
  });

  it('leaves the blob and an unmoved pointer when it fails between the blob and the transaction, and a retry commits', async () => {
    await withStores(async ({ dataDir, metadata, blobs }) => {
      let armed = false, failNext = false;
      const faulty: MetadataStore = { transact: (publicId, run) => {
        if (failNext) { failNext = false; throw new Error('injected fault before the metadata transaction'); }
        return metadata.transact(publicId, run);
      } };
      const putThenFail = { put: async (key: string, data: Uint8Array) => { await blobs.put(key, data); failNext = armed; }, get: (key: string) => blobs.get(key) };
      const deliveries = new Deliveries(faulty, putThenFail, { now: () => 2_000 });
      const publicId = id();
      deliveries.session(publicId, REPO, { mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(1) });
      const kept = await deliveries.commit(publicId, REPO, commit(1, 11), hex(12), bytes(11));
      armed = true;
      await expect(deliveries.commit(publicId, REPO, commit(2, 13), hex(14), bytes(13))).rejects.toThrow('injected fault');
      expect(existsSync(join(dataDir, 'blobs', blobName(publicId, 2, 13)))).toBe(true);
      expect(metadata.transact(publicId, tx => tx.row())).toMatchObject({ sequence: 1, receipt: kept });
      const plain = new Deliveries(metadata, blobs, { now: () => 3_000 });
      expect((await plain.commit(publicId, REPO, commit(2, 13), hex(14), bytes(13))).sequence).toBe(2);
      expect(plain.current(publicId).sequence).toBe(2);
    });
  });
});

// The HTTP failures Node meets before a request reaches the core: each closes the socket with no bytes written.
describe('requests the core never sees', () => {
  const token = 'node-http-token-'.padEnd(43, 'z');
  let close: () => Promise<void>, port: number, release: () => void;
  const limits: Limits = { headersTimeoutMs: 600, requestTimeoutMs: 1_200, keepAliveTimeoutMs: 500, maxHeaderBytes: 4096, maxConnections: 3 };
  beforeAll(async () => {
    const done = new Promise<void>(resolve => { release = resolve; });
    const ready = new Promise<void>(resolve => {
      void withStores(async ({ metadata, blobs }) => {
        const expected = await digest(new TextEncoder().encode(token));
        const deliveries = new Deliveries(metadata, blobs);
        const service = createService({ deliveries: publicId => endpoint(deliveries, publicId), publishers: { expected: async r => r === REPO ? expected : null } });
        const server = createNodeServer(service, limits);
        await new Promise<void>(listening => server.listen(0, '127.0.0.1', listening));
        port = (server.address() as AddressInfo).port;
        close = () => new Promise(closed => { server.closeAllConnections(); server.close(() => closed()); });
        resolve();
        await done;
      });
    });
    await ready;
  });
  afterAll(async () => { await close(); release(); });

  /** Opens a raw connection, writes `parts` with pauses, and reports what came back and how the socket ended. */
  function exchange(parts: (string | number)[]): Promise<{ received: number; ended: 'closed' | 'open'; elapsed: number; socket: Socket }> {
    return new Promise((resolve) => {
      const started = Date.now();
      let received = 0;
      const socket = connect(port, '127.0.0.1');
      socket.on('data', chunk => { received += chunk.length; });
      socket.on('error', () => {});
      socket.on('close', () => resolve({ received, ended: 'closed', elapsed: Date.now() - started, socket }));
      void (async () => {
        for (const part of parts) {
          if (typeof part === 'number') await new Promise(wait => setTimeout(wait, part));
          else if (!socket.destroyed) socket.write(part);
        }
      })();
      setTimeout(() => resolve({ received, ended: 'open', elapsed: Date.now() - started, socket }), 5_000);
    });
  }

  it('closes a connection whose header block is too slow, with no bytes', async () => {
    const result = await exchange(['GET / HTTP/1.1\r\nHost: x\r\n', 2_000]);
    expect([result.ended, result.received]).toEqual(['closed', 0]);
    expect(result.elapsed).toBeGreaterThanOrEqual(limits.headersTimeoutMs);
  });

  it('closes a connection whose body is too slow, with no bytes', async () => {
    const head = `POST /api/v1/repos/${REPO}/deliveries/${hex(5)}/session HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer ${token}\r\n` +
      'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"mode":';
    const result = await exchange([head, 3_000]);
    expect([result.ended, result.received]).toEqual(['closed', 0]);
    expect(result.elapsed).toBeGreaterThanOrEqual(limits.requestTimeoutMs);
  });

  it('closes a connection whose header block is over the limit, with no bytes', async () => {
    const result = await exchange([`GET / HTTP/1.1\r\nHost: x\r\nX-Padding: ${'a'.repeat(limits.maxHeaderBytes * 2)}\r\n\r\n`]);
    expect([result.ended, result.received]).toEqual(['closed', 0]);
  });

  it('closes a connection with a malformed request line, with no bytes', async () => {
    for (const line of ['NOT A REQUEST LINE\r\n\r\n', 'GET / HTTP/9.9\r\nHost: x\r\n\r\n', 'GET /\u0001 HTTP/1.1\r\nHost: x\r\n\r\n']) {
      const result = await exchange([line]);
      expect([line, result.ended, result.received]).toEqual([line, 'closed', 0]);
    }
  });

  it('closes a connection over the cap, with no bytes, and serves again once one closes', async () => {
    const held = await Promise.all(Array.from({ length: limits.maxConnections }, () => new Promise<Socket>(resolve => {
      const socket = connect(port, '127.0.0.1', () => resolve(socket));
      socket.on('error', () => {});
    })));
    const over = await exchange(['GET / HTTP/1.1\r\nHost: x\r\n\r\n']);
    expect([over.ended, over.received]).toEqual(['closed', 0]);
    for (const socket of held) socket.destroy();
    await new Promise(wait => setTimeout(wait, 100));
    const response = await fetch(`http://127.0.0.1:${port}/`);
    expect([response.status, await response.json()]).toEqual([404, { error: 'not_found' }]);
  });
});

// The built server as its own process: npm run build:node first.
describe.runIf(existsSync(builtNode))('the built portable server', () => {
  const server = fileURLToPath(builtNode);
  const [first, second] = credentials;
  async function acquire(origin: string, repository: string, token: string, publicId = id()) {
    return fetch(`${origin}/api/v1/repos/${repository}/deliveries/${publicId}/session`, { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(3) }) });
  }

  it('refuses a second instance on the data directory an idle first one holds, and stops cleanly on SIGTERM', async () => {
    // On an existing data directory: a first run made the database and stopped, and the instance that holds it now
    // has done nothing since it started.
    const made = await startNode();
    expect(await made.stop('SIGTERM')).toEqual({ code: 0, signal: null });
    const one = await startNode({ dataDir: made.dataDir });
    try {
      const two = await startNode({ dataDir: one.dataDir }).then(() => 'started', (error: { stderr: string; code: number }) => error);
      expect(two).toMatchObject({ code: 1 });
      expect((two as { stderr: string }).stderr).toContain('hosted.data_dir_in_use');
      expect((await fetch(`${one.origin}/`)).status).toBe(404);
    } finally {
      expect(await one.stop('SIGTERM')).toEqual({ code: 0, signal: null });
      await made.dispose();
    }
  });

  it('restarts after a kill with its second commit as the view', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'hosted-crash-'));
    try {
      const publicId = id();
      const before = await startNode({ dataDir });
      expect((await acquire(before.origin, first.repository, first.token, publicId)).status).toBe(200);
      const valid = cases.filter(row => row.valid).slice(0, 2);
      let last = '';
      for (const [index, row] of valid.entries()) {
        const body = JSON.stringify(row.body);
        last = await digest(new TextEncoder().encode(body));
        const response = await fetch(`${before.origin}/api/v1/repos/${first.repository}/deliveries/${publicId}/snapshots`, { method: 'POST', body,
          headers: { Authorization: `Bearer ${first.token}`, 'Content-Type': 'application/json', 'X-Writer-Epoch': '1',
            'X-Snapshot-Sequence': String(index + 1), 'X-Snapshot-Digest': last, 'X-Renderer-Version': RENDERER } });
        expect(response.status).toBe(200);
      }
      expect(await before.stop('SIGKILL')).toEqual({ code: null, signal: 'SIGKILL' });
      const after = await startNode({ dataDir });
      try {
        expect(await (await fetch(`${after.origin}/d/${publicId}/current`)).json()).toMatchObject({ sequence: 2, digest: last });
        expect((await fetch(`${after.origin}/d/${publicId}/snapshots/${last}`)).status).toBe(200);
      } finally { await after.dispose(); }
    } finally { rmSync(dataDir, { recursive: true, force: true }); }
  });

  it('accepts the tokens provision-token printed, through PUBLISHERS and PUBLISHERS_FILE, for both repositories', async () => {
    const provisioned = ['repo-provisioned-a', 'repo-provisioned-b'].map(repository => {
      const run = spawnSync(process.execPath, [server, 'provision-token', repository], { encoding: 'utf8', env: { PATH: process.env.PATH } as unknown as NodeJS.ProcessEnv });
      expect(run.status).toBe(0);
      const [, token] = /^token: (\S+)$/m.exec(run.stdout)!;
      const [, entry] = /^PUBLISHERS entry: (\{.*\})$/m.exec(run.stdout)!;
      expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
      return { repository, token, entry: JSON.parse(entry) as Record<string, string> };
    });
    const map = JSON.stringify(Object.assign({}, ...provisioned.map(row => row.entry)));
    const folder = mkdtempSync(join(tmpdir(), 'hosted-publishers-'));
    try {
      writeFileSync(join(folder, 'publishers.json'), map, { mode: 0o600 });
      for (const env of [{ PUBLISHERS: map }, { PUBLISHERS: undefined, PUBLISHERS_FILE: join(folder, 'publishers.json') }]) {
        const running = await startNode({ env });
        try {
          for (const row of provisioned) expect([env, row.repository, (await acquire(running.origin, row.repository, row.token)).status]).toEqual([env, row.repository, 200]);
          expect((await acquire(running.origin, provisioned[0].repository, provisioned[1].token)).status).toBe(401);
          expect((await acquire(running.origin, second.repository, second.token)).status).toBe(401);
        } finally { await running.dispose(); }
      }
    } finally { rmSync(folder, { recursive: true, force: true }); }
  });

  it('refuses a missing, ambiguous or malformed configuration at startup with a named error', async () => {
    for (const [env, code] of [[{ PUBLISHERS: undefined }, 'hosted.config_invalid'], [{ PUBLISHERS_FILE: '/nonexistent' }, 'hosted.config_invalid'],
      [{ PUBLISHERS: '{"repo": "not-a-digest"}' }, 'hosted.publishers_invalid'], [{ PUBLISHERS: '[]' }, 'hosted.publishers_invalid'],
      [{ DATA_DIR: 'relative/path' }, 'hosted.config_invalid'], [{ PORT: 'eighty' }, 'hosted.config_invalid']] as const) {
      const outcome = await startNode({ env }).then(async running => { await running.dispose(); return 'started'; }, (error: { stderr: string; code: number }) => error);
      expect([env, outcome]).toMatchObject([env, { code: 1 }]);
      expect((outcome as { stderr: string }).stderr).toContain(code);
    }
  });
});
