// The portable server's own guarantees: durability settings and write order, a fault between the blob and the
// transaction, the startup lock, a crash, the HTTP failures Node meets before the core, and provisioned tokens.
import { connect, type Socket } from 'node:net';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { open } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { Deliveries, endpoint } from '../../src/core/deliveries';
import { createService } from '../../src/core/service';
import type { MetadataStore } from '../../src/core/ports';
import { RENDERER, digest } from '../../src/protocol';
import { FileBlobs, realFiles, type BlobFiles } from '../../src/node/file-blobs';
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
    await withStores(async ({ dataDir, metadata, blobs }) => {
      // The real fsyncs, observed on the file handles themselves rather than on the steps that call them.
      const probe = await open(dataDir, 'r');
      const handles = Object.getPrototypeOf(probe);
      await probe.close();
      const original = handles.sync as () => Promise<void>;
      handles.sync = async function (this: unknown) { events.push('sync'); return original.call(this); };
      try {
        const recorded: MetadataStore = { transact: (publicId, run) => { events.push('transaction'); return metadata.transact(publicId, run); } };
        const deliveries = new Deliveries(recorded, blobs, { now: () => 1_000 });
        const publicId = id();
        deliveries.session(publicId, REPO, { mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(1) });
        events.length = 0;
        await deliveries.commit(publicId, REPO, commit(1, 7), hex(8), bytes(7));
      } finally { handles.sync = original; }
      // The first transaction checks admissibility before any write; the commit's own comes after the directory fsync.
      expect(events).toEqual(['transaction', 'sync', 'temp-fsync', 'link', 'sync', 'dir-fsync', 'transaction']);
      // The temp file is gone once linked.
      expect(readdirSync(join(dataDir, 'staging'))).toEqual([]);
    }, files);
  });

  it('commits to one delivery while another delivery\'s blob write is held inside the filesystem store', async () => {
    let entered!: () => void, release!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    const gate = new Promise<void>(resolve => { release = resolve; });
    let first = true;
    const files: BlobFiles = { ...realFiles, async writeSynced(path, data) {
      if (first) { first = false; entered(); await gate; }
      return realFiles.writeSynced(path, data);
    } };
    await withStores(async ({ metadata, blobs }) => {
      const deliveries = new Deliveries(metadata, blobs, { now: () => 1_500 });
      const slow = id(), quick = id();
      for (const publicId of [slow, quick]) deliveries.session(publicId, REPO, { mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(1) });
      const pending = deliveries.commit(slow, REPO, commit(1, 21), hex(22), bytes(21));
      await started;
      expect((await deliveries.commit(quick, REPO, commit(1, 23), hex(24), bytes(23))).digest).toBe(hex(23));
      release();
      expect((await pending).digest).toBe(hex(21));
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
  function exchange(parts: (string | number)[]): Promise<{ received: number; text: string; ended: 'closed' | 'open'; elapsed: number; socket: Socket }> {
    return new Promise((resolve) => {
      const started = Date.now();
      let received = 0, text = '';
      const socket = connect(port, '127.0.0.1');
      socket.on('data', chunk => { received += chunk.length; text += chunk; });
      socket.on('error', () => {});
      socket.on('close', () => resolve({ received, text, ended: 'closed', elapsed: Date.now() - started, socket }));
      void (async () => {
        for (const part of parts) {
          if (typeof part === 'number') await new Promise(wait => setTimeout(wait, part));
          else if (!socket.destroyed) socket.write(part);
        }
      })();
      setTimeout(() => resolve({ received, text, ended: 'open', elapsed: Date.now() - started, socket }), 5_000);
    });
  }

  it('closes a connection whose header block is too slow, with no bytes', async () => {
    const result = await exchange(['GET / HTTP/1.1\r\nHost: x\r\n', 2_000]);
    expect([result.ended, result.received]).toEqual(['closed', 0]);
    expect(result.elapsed).toBeGreaterThanOrEqual(limits.headersTimeoutMs);
    // The header limit, not the whole-request limit, closed it.
    expect(result.elapsed).toBeLessThan(limits.requestTimeoutMs);
  });

  it('answers methods fetch cannot represent as any method it does not route', async () => {
    const anonymous = await exchange([`TRACE /d/${hex(6)}/current HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n`]);
    const authenticated = await exchange([`TRACE /api/v1/repos/${REPO}/deliveries/${hex(6)}/status HTTP/1.1\r\nHost: x\r\n` +
      `Authorization: Bearer ${token}\r\nConnection: close\r\n\r\n`]);
    expect(anonymous.text).toContain('{"error":"not_found"}');
    expect(anonymous.text.startsWith('HTTP/1.1 404')).toBe(true);
    expect(authenticated.text).toContain('{"error":"method_not_allowed"}');
    expect(authenticated.text.startsWith('HTTP/1.1 405')).toBe(true);
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
      // A temp file a crash left mid-write is cleared at startup.
      writeFileSync(join(dataDir, 'staging', 'left-by-a-crash'), 'partial');
      const after = await startNode({ dataDir });
      expect(readdirSync(join(dataDir, 'staging'))).toEqual([]);
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
    const invalid = spawnSync(process.execPath, [server, 'provision-token', 'bad repo'], { encoding: 'utf8', env: { PATH: process.env.PATH } as unknown as NodeJS.ProcessEnv });
    expect([invalid.status, invalid.stdout]).toEqual([1, '']);
    expect(invalid.stderr).toContain('hosted.config_invalid');
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
          if (env.PUBLISHERS_FILE) {
            // The file is read on every check: a rotation takes effect without a restart, and a missing file denies.
            writeFileSync(env.PUBLISHERS_FILE, JSON.stringify(provisioned[1].entry));
            expect((await acquire(running.origin, provisioned[0].repository, provisioned[0].token)).status).toBe(401);
            expect((await acquire(running.origin, provisioned[1].repository, provisioned[1].token)).status).toBe(200);
            rmSync(env.PUBLISHERS_FILE);
            const denied = await acquire(running.origin, provisioned[1].repository, provisioned[1].token);
            expect([denied.status, await denied.json()]).toEqual([503, { error: 'temporarily_unavailable' }]);
          }
        } finally { await running.dispose(); }
      }
    } finally { rmSync(folder, { recursive: true, force: true }); }
  });

  it('finishes a request in flight on SIGTERM, accepts no new connection, then exits 0', async () => {
    const running = await startNode();
    try {
      const publicId = id();
      expect((await acquire(running.origin, first.repository, first.token, publicId)).status).toBe(200);
      const body = JSON.stringify(cases.find(row => row.valid)!.body);
      const hash = await digest(new TextEncoder().encode(body));
      const { port } = new URL(running.origin);
      const reply = new Promise<string>((resolve) => {
        let text = '';
        const socket = connect(Number(port), '127.0.0.1', () => {
          socket.write(`POST /api/v1/repos/${first.repository}/deliveries/${publicId}/snapshots HTTP/1.1\r\nHost: x\r\n` +
            `Authorization: Bearer ${first.token}\r\nContent-Type: application/json\r\nContent-Length: ${Buffer.byteLength(body)}\r\n` +
            `X-Writer-Epoch: 1\r\nX-Snapshot-Sequence: 1\r\nX-Snapshot-Digest: ${hash}\r\nX-Renderer-Version: ${RENDERER}\r\n\r\n` + body.slice(0, 100));
          socket.on('data', chunk => { text += chunk; if (text.endsWith('\r\n0\r\n\r\n')) socket.end(); });
          socket.on('close', () => resolve(text));
          socket.on('error', () => resolve(text));
          setTimeout(() => {
            process.kill(running.pid!, 'SIGTERM');
            setTimeout(async () => {
              // After the signal, the listener is closed: a new connection is refused.
              const refused = await new Promise<string>(done => { const probe = connect(Number(port), '127.0.0.1');
                probe.on('connect', () => { probe.destroy(); done('connected'); }); probe.on('error', (error: NodeJS.ErrnoException) => done(error.code ?? 'error')); });
              expect(refused).toBe('ECONNREFUSED');
              // The rest of the body, without half-closing: the server closes the connection once it has answered.
              socket.write(body.slice(100));
            }, 300);
          }, 200);
        });
      });
      const text = await reply;
      expect(text.slice(0, 200)).toMatch(/^HTTP\/1.1 200/);
      expect(JSON.parse(/\{.*\}/s.exec(text.slice(text.indexOf('\r\n\r\n')))![0])).toMatchObject({ epoch: 1, sequence: 1, digest: hash });
      expect(await running.exited).toEqual({ code: 0, signal: null });
    } finally { await running.dispose(); }
  });

  it('refuses a missing, ambiguous or malformed configuration at startup with a named error', async () => {
    for (const [env, code] of [[{ PUBLISHERS: undefined }, 'hosted.config_invalid'], [{ PUBLISHERS_FILE: '/nonexistent' }, 'hosted.config_invalid'],
      [{ PUBLISHERS: '{"repo": "not-a-digest"}' }, 'hosted.publishers_invalid'], [{ PUBLISHERS: '[]' }, 'hosted.publishers_invalid'],
      [{ DATA_DIR: 'relative/path' }, 'hosted.config_invalid'], [{ PORT: 'eighty' }, 'hosted.config_invalid'], [{ PORT: '99999' }, 'hosted.config_invalid']] as const) {
      const outcome = await startNode({ env }).then(async running => { await running.dispose(); return 'started'; }, (error: { stderr: string; code: number }) => error);
      expect([env, outcome]).toMatchObject([env, { code: 1 }]);
      expect((outcome as { stderr: string }).stderr).toContain(code);
    }
  });
});

// The image is built and run in CI; locally Docker may be absent. These are the Dockerfile's own claims, read as text.
describe('the container image definition', () => {
  const dockerfile = readFileSync(fileURLToPath(new URL('../../Dockerfile', import.meta.url)), 'utf8');
  const stages = dockerfile.split(/^FROM /m).slice(1);
  it('builds on one base pinned by digest, installs without scripts, and runs only the built server as a non-root user', () => {
    expect(dockerfile).toMatch(/^ARG NODE_IMAGE=node:[\w.-]+@sha256:[a-f0-9]{64}$/m);
    expect(stages.map(stage => stage.split('\n')[0])).toEqual(['${NODE_IMAGE} AS build', '${NODE_IMAGE}']);
    expect(stages[0]).toContain('npm ci --ignore-scripts');
    const runtime = stages[1];
    expect(runtime.match(/^COPY .*$/gm)).toEqual(['COPY --from=build /src/hosted/delivery-viewer/build/node/server.mjs /app/server.mjs']);
    expect(runtime).toMatch(/^USER node$/m);
    expect(runtime).toMatch(/^VOLUME \["\/data"\]$/m);
    expect(runtime).toMatch(/DATA_DIR=\/data/);
    expect(runtime).not.toMatch(/npm (ci|install)/);
    expect(runtime).toMatch(/^ENTRYPOINT \["node", "\/app\/server.mjs"\]$/m);
  });
});
