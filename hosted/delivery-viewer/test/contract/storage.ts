// The core-level storage contract: the ordering guarantees the core needs of any MetadataStore and BlobStore, run
// through the core's own Deliveries with deterministic interleaving and an injected clock. Each store runs it:
// test/storage-contract.test.ts against the Durable Object store and R2, test/node/storage-contract.test.ts against
// SQLite and the filesystem. `manyDeliveriesContract` adds the rows only a store holding many deliveries can answer.
import { describe, expect, it } from 'vitest';
import { Deliveries, endpoint, type Session } from '../../src/core/deliveries';
import { createService } from '../../src/core/service';
import type { BlobStore, MetadataStore, MetadataTransaction } from '../../src/core/ports';
import { ApiError, LEASE_MS, RENDERER, STALE_MS, digest, type Commit } from '../../src/protocol';
import { cases } from '../fixtures.generated';

export interface Stores {
  metadata: MetadataStore;
  blobs: BlobStore;
  /** The store's raw persisted metadata (its tables, rows or files), read past the MetadataStore interface. */
  persisted(): unknown[];
}
/** Runs one scenario with stores for the delivery `publicId`, inside whatever context the stores need. */
export type Harness = (publicId: string, scenario: (stores: Stores) => Promise<void>) => Promise<void>;

const REPO = 'repo-contract', OTHER_REPO = 'repo-contract-other';
const hex = (seed: number) => seed.toString(16).padStart(64, '0');
let next = 1;
const publicId = () => crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');

/** A distinct cut: bytes, the envelope that names them, and a fingerprint. */
function cut(epoch: number, sequence: number) {
  const n = next++;
  return { commit: { epoch, sequence, digest: hex(n), rendererVersion: RENDERER } as Commit, fingerprint: hex(n + 1_000_000),
    bytes: new TextEncoder().encode(`{"cut":${n}}`) };
}
async function code(run: () => unknown): Promise<string> {
  try { await run(); return 'ok'; } catch (error) { return error instanceof ApiError ? error.code : 'unexpected'; }
}

/** A BlobStore whose first put waits, before writing, until it is released. Later puts pass straight through. */
function holding(blobs: BlobStore) {
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  let first = true;
  const store: BlobStore = {
    async put(key, bytes) {
      if (first) { first = false; entered(); await gate; }
      return blobs.put(key, bytes);
    },
    get: key => blobs.get(key),
  };
  return { store, started, release };
}

/** A MetadataStore that, while armed, fails inside the commit transaction after the receipt is recorded. */
function faulting(metadata: MetadataStore) {
  let armed = false;
  const store: MetadataStore = {
    transact: (id, run) => metadata.transact(id, tx => run(armed ? {
      ...bind(tx), movePointer() { throw new Error('injected fault at the commit boundary'); },
    } : tx)),
  };
  return { store, arm: (value: boolean) => { armed = value; } };
}
function bind(tx: MetadataTransaction): MetadataTransaction {
  return { row: () => tx.row(), create: row => tx.create(row), advance: (o, l) => tx.advance(o, l), accepted: (e, s) => tx.accepted(e, s),
    addReceipt: r => tx.addReceipt(r), movePointer: c => tx.movePointer(c), confirm: (c, l) => tx.confirm(c, l) };
}

export function storageContract(name: string, harness: Harness) {
  describe(`storage contract: ${name}`, () => {
    const owner = hex(0xa1), other = hex(0xb2);
    const acquire: Session = { mode: 'acquire', deliveryKey: 'APP-1', ownerId: owner };

    it('fences a commit held at its blob write once a takeover has run', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const clock = { time: 1_000_000, now() { return this.time; } };
        const plain = new Deliveries(metadata, blobs, clock);
        plain.session(id, REPO, acquire);
        const acknowledged = cut(1, 1);
        const original = await plain.commit(id, REPO, acknowledged.commit, acknowledged.fingerprint, acknowledged.bytes);
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, clock);
        const stale = cut(1, 2);
        const pending = code(() => deliveries.commit(id, REPO, stale.commit, stale.fingerprint, stale.bytes));
        await held.started;
        clock.time += LEASE_MS;
        expect(deliveries.session(id, REPO, { mode: 'takeover', deliveryKey: 'APP-1', ownerId: other, epoch: 1 }).epoch).toBe(2);
        const fresh = cut(2, 1);
        const accepted = await deliveries.commit(id, REPO, fresh.commit, fresh.fingerprint, fresh.bytes);
        held.release();
        expect(await pending).toBe('writer_fenced');
        expect(deliveries.current(id).digest).toBe(accepted.digest);
        expect(metadata.transact(id, tx => tx.accepted(1, 2))).toBeUndefined();
        expect(await code(() => deliveries.snapshot(id, stale.commit.digest))).toBe('not_found');
        // The displaced writer's exact retry of an acknowledged commit still answers its original receipt.
        expect(await deliveries.commit(id, REPO, acknowledged.commit, acknowledged.fingerprint, acknowledged.bytes)).toEqual(original);
        expect(deliveries.current(id).digest).toBe(accepted.digest);
      });
    });

    it('refuses a commit whose lease expired on the clock while its blob write was held', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const clock = { time: 2_000_000, now() { return this.time; } };
        const plain = new Deliveries(metadata, blobs, clock);
        plain.session(id, REPO, acquire);
        const first = cut(1, 1);
        const kept = await plain.commit(id, REPO, first.commit, first.fingerprint, first.bytes);
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, clock);
        const late = cut(1, 2);
        const pending = code(() => deliveries.commit(id, REPO, late.commit, late.fingerprint, late.bytes));
        await held.started;
        clock.time += LEASE_MS;
        held.release();
        expect(await pending).toBe('lease_expired');
        expect(deliveries.current(id).digest).toBe(kept.digest);
        expect(metadata.transact(id, tx => tx.accepted(1, 2))).toBeUndefined();
      });
    });

    it('supersedes a held commit once a competing later commit has run', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, { now: () => 3_000_000 });
        deliveries.session(id, REPO, acquire);
        const earlier = cut(1, 1), later = cut(1, 2);
        const pending = code(() => deliveries.commit(id, REPO, earlier.commit, earlier.fingerprint, earlier.bytes));
        await held.started;
        const accepted = await deliveries.commit(id, REPO, later.commit, later.fingerprint, later.bytes);
        held.release();
        expect(await pending).toBe('sequence_superseded');
        expect(deliveries.status(id, REPO)).toMatchObject({ sequence: 2, receipt: accepted });
        expect(metadata.transact(id, tx => tx.accepted(1, 1))).toBeUndefined();
      });
    });

    it('gives a held commit and its exact retry one immutable receipt', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const clock = { time: 4_000_000, now() { return this.time; } };
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, clock);
        deliveries.session(id, REPO, acquire);
        const same = cut(1, 1);
        const original = deliveries.commit(id, REPO, same.commit, same.fingerprint, same.bytes);
        await held.started;
        const retried = await deliveries.commit(id, REPO, same.commit, same.fingerprint, same.bytes);
        clock.time += 5_000;
        held.release();
        expect(await original).toEqual(retried);
        expect(metadata.transact(id, tx => tx.accepted(1, 1))).toEqual({ digest: same.commit.digest, receipt: retried });
        expect(deliveries.current(id).sourceConfirmedAt).toBe(retried.sourceConfirmedAt);
      });
    });

    it('leaves no receipt and an unmoved pointer when the commit transaction fails, and a retry commits', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const faults = faulting(metadata);
        const deliveries = new Deliveries(faults.store, blobs, { now: () => 5_000_000 });
        deliveries.session(id, REPO, acquire);
        const first = cut(1, 1);
        const kept = await deliveries.commit(id, REPO, first.commit, first.fingerprint, first.bytes);
        const failed = cut(1, 2);
        faults.arm(true);
        expect(await code(() => deliveries.commit(id, REPO, failed.commit, failed.fingerprint, failed.bytes))).toBe('unexpected');
        faults.arm(false);
        expect(metadata.transact(id, tx => tx.accepted(1, 2))).toBeUndefined();
        expect(metadata.transact(id, tx => tx.row())).toMatchObject({ sequence: 1, receipt: kept });
        // The blob was written before the transaction: it remains, unreferenced and not public.
        expect(await blobs.get(`${id}/1/2/${failed.commit.digest}`)).not.toBeNull();
        expect(await code(() => deliveries.snapshot(id, failed.commit.digest))).toBe('not_found');
        const retried = await deliveries.commit(id, REPO, failed.commit, failed.fingerprint, failed.bytes);
        expect(deliveries.current(id).digest).toBe(retried.digest);
      });
    });

    it('creates no state for reads of an unknown public id', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs, persisted }) => {
        const deliveries = new Deliveries(metadata, blobs, { now: () => 6_000_000 });
        expect(await code(() => deliveries.current(id))).toBe('not_found');
        expect(await code(() => deliveries.snapshot(id, hex(1)))).toBe('not_found');
        expect(await code(() => deliveries.status(id, REPO))).toBe('not_found');
        expect(await code(() => deliveries.session(id, REPO, { ...acquire, mode: 'resume', epoch: 1 }))).toBe('not_found');
        expect(metadata.transact(id, tx => tx.row())).toBeUndefined();
        expect(persisted()).toEqual([]);
      });
    });

    it('expires, resumes, takes over and goes stale on the injected clock', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const clock = { time: 7_000_000, now() { return this.time; } };
        const deliveries = new Deliveries(metadata, blobs, clock);
        expect(deliveries.session(id, REPO, acquire).leaseUntil).toBe(7_000_000 + LEASE_MS);
        const first = cut(1, 1);
        const receipt = await deliveries.commit(id, REPO, first.commit, first.fingerprint, first.bytes);
        clock.time += STALE_MS - 1;
        expect(deliveries.current(id).stale).toBe(false);
        clock.time += 1;
        expect(deliveries.current(id).stale).toBe(true);
        expect(deliveries.confirm(id, REPO, first.commit, first.fingerprint).leaseUntil).toBe(clock.time + LEASE_MS);
        // The renewal is stored, not only answered.
        expect(deliveries.status(id, REPO).leaseUntil).toBe(clock.time + LEASE_MS);
        expect(deliveries.current(id).stale).toBe(false);
        clock.time += LEASE_MS;
        const later = cut(1, 2);
        expect(await code(() => deliveries.commit(id, REPO, later.commit, later.fingerprint, later.bytes))).toBe('lease_expired');
        expect(await code(() => deliveries.session(id, REPO, acquire))).toBe('resume_required');
        const resumed = deliveries.session(id, REPO, { mode: 'resume', deliveryKey: 'APP-1', ownerId: owner, epoch: 1 });
        expect(resumed).toMatchObject({ epoch: 2, sequence: 0, receipt });
        clock.time += LEASE_MS;
        expect(deliveries.session(id, REPO, { mode: 'takeover', deliveryKey: 'APP-1', ownerId: other, epoch: 2 })).toMatchObject({ epoch: 3, ownerId: other });
        expect(await code(() => deliveries.session(id, REPO, { mode: 'resume', deliveryKey: 'APP-1', ownerId: owner, epoch: 2 }))).toBe('writer_fenced');
        // Displaced twice, the first writer's exact retry still answers its original receipt.
        expect(await deliveries.commit(id, REPO, first.commit, first.fingerprint, first.bytes)).toEqual(receipt);
      });
    });

    it('serves the protocol through the core service over an in-process endpoint', async () => {
      const id = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const token = 'contract-token-'.padEnd(43, 'x'), otherToken = 'contract-other-'.padEnd(43, 'y');
        const expected = await digest(new TextEncoder().encode(token)), otherExpected = await digest(new TextEncoder().encode(otherToken));
        const deliveries = new Deliveries(metadata, blobs, { now: () => 8_000_000 });
        const service = createService({ deliveries: publicId => endpoint(deliveries, publicId),
          publishers: { expected: async repository => repository === REPO ? expected : repository === OTHER_REPO ? otherExpected : null } });
        const send = (path: string, init: RequestInit = {}, headers: Record<string, string> = {}, as = token) =>
          service.fetch(new Request(`https://service.test${path}`, { ...init,
            headers: { Authorization: `Bearer ${as}`, 'Content-Type': 'application/json', ...headers } }));
        const api = (action: string) => `/api/v1/repos/${REPO}/deliveries/${id}/${action}`;
        expect((await send(api('session'), { method: 'POST', body: JSON.stringify(acquire) })).status).toBe(200);
        const body = JSON.stringify(cases.find(row => row.valid)!.body);
        const hash = await digest(new TextEncoder().encode(body));
        const envelope = { 'X-Writer-Epoch': '1', 'X-Snapshot-Sequence': '1', 'X-Snapshot-Digest': hash, 'X-Renderer-Version': RENDERER };
        const committed = await send(api('snapshots'), { method: 'POST', body }, envelope);
        expect(committed.status).toBe(200);
        const receipt = await committed.json() as { digest: string; sourceFingerprint: string };
        expect(receipt.digest).toBe(hash);
        expect(await (await send(api('status'))).json()).toMatchObject({ epoch: 1, sequence: 1, receipt });
        const confirmed = await send(api('confirm'), { method: 'POST', body: JSON.stringify({ sourceFingerprint: receipt.sourceFingerprint }) }, envelope);
        expect(await confirmed.json()).toMatchObject({ sourceConfirmedAt: 8_000_000, leaseUntil: 8_000_000 + LEASE_MS, receipt });
        expect(await (await send(`/d/${id}/current`)).json()).toMatchObject({ digest: hash, stale: false });
        expect(await (await send(`/d/${id}/snapshots/${hash}`)).text()).toBe(body);
        expect((await send(`/api/v1/repos/other-repo/deliveries/${id}/status`)).status).toBe(401);
        // Another authenticated repository reaches the binding check, which refuses it: the endpoint passes the
        // caller's repository through rather than the bound one.
        const asOther = (action: string) => `/api/v1/repos/${OTHER_REPO}/deliveries/${id}/${action}`;
        for (const [action, init, headers] of [['status', {}, {}], ['snapshots', { method: 'POST', body }, { ...envelope, 'X-Snapshot-Sequence': '2' }],
          ['confirm', { method: 'POST', body: JSON.stringify({ sourceFingerprint: receipt.sourceFingerprint }) }, envelope]] as const) {
          const refused = await send(asOther(action), init, headers, otherToken);
          expect([action, refused.status, await refused.json()]).toEqual([action, 404, { error: 'not_found' }]);
        }
        expect(await (await send(api('status'))).json()).toMatchObject({ sequence: 1, receipt });
      });
    });
  });
}

/** Rows for a store that holds many deliveries, as the portable SQLite store does and a Durable Object does not. */
export function manyDeliveriesContract(name: string, harness: Harness) {
  describe(`storage contract, many deliveries: ${name}`, () => {
    const acquire: Session = { mode: 'acquire', deliveryKey: 'APP-1', ownerId: hex(0xc3) };

    it('keeps each public id to its own delivery through the in-process endpoint', async () => {
      const id = publicId(), other = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const deliveries = new Deliveries(metadata, blobs, { now: () => 9_000_000 });
        deliveries.session(id, REPO, acquire);
        const first = cut(1, 1);
        const receipt = await deliveries.commit(id, REPO, first.commit, first.fingerprint, first.bytes);
        const at = (publicId: string) => endpoint(deliveries, publicId);
        expect(await at(id).current()).toMatchObject({ ok: true, value: { digest: receipt.digest } });
        expect(await at(other).current()).toEqual({ ok: false, error: 'not_found', status: 404 });
        expect(await at(other).status(REPO)).toEqual({ ok: false, error: 'not_found', status: 404 });
        expect(await at(other).snapshot(receipt.digest)).toEqual({ ok: false, error: 'not_found', status: 404 });
        expect(await at(other).session(REPO, { ...acquire, mode: 'resume', epoch: 1 })).toEqual({ ok: false, error: 'not_found', status: 404 });
        expect(await at(other).confirm(REPO, first.commit, first.fingerprint)).toEqual({ ok: false, error: 'not_found', status: 404 });
      });
    });

    it('commits to one delivery while another delivery\'s blob write is held', async () => {
      const id = publicId(), other = publicId();
      await harness(id, async ({ metadata, blobs }) => {
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, { now: () => 9_500_000 });
        deliveries.session(id, REPO, acquire);
        deliveries.session(other, REPO, acquire);
        const slow = cut(1, 1), quick = cut(1, 1);
        const pending = deliveries.commit(id, REPO, slow.commit, slow.fingerprint, slow.bytes);
        await held.started;
        const accepted = await deliveries.commit(other, REPO, quick.commit, quick.fingerprint, quick.bytes);
        expect(deliveries.current(other).digest).toBe(accepted.digest);
        expect(await code(() => deliveries.current(id))).toBe('not_found');
        held.release();
        expect((await pending).digest).toBe(slow.commit.digest);
        expect(deliveries.current(id).digest).toBe(slow.commit.digest);
      });
    });
  });
}
