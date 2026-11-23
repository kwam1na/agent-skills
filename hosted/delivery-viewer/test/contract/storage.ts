// The core-level storage contract: the ordering guarantees the core needs of any MetadataStore and BlobStore, run
// through the core's own Deliveries with deterministic interleaving and an injected clock. Each store runs it:
// test/storage-contract.test.ts against the Durable Object store and R2.
import { describe, expect, it } from 'vitest';
import { Deliveries, type Session } from '../../src/core/deliveries';
import type { BlobStore, MetadataStore, MetadataTransaction } from '../../src/core/ports';
import { ApiError, LEASE_MS, RENDERER, STALE_MS, type Commit } from '../../src/protocol';

export interface Stores { metadata: MetadataStore; blobs: BlobStore }
/** Runs one scenario with stores for the delivery `publicId`, inside whatever context the stores need. */
export type Harness = (publicId: string, scenario: (stores: Stores) => Promise<void>) => Promise<void>;

const REPO = 'repo-contract';
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
        const held = holding(blobs);
        const deliveries = new Deliveries(metadata, held.store, clock);
        deliveries.session(id, REPO, acquire);
        const stale = cut(1, 1);
        const pending = code(() => deliveries.commit(id, REPO, stale.commit, stale.fingerprint, stale.bytes));
        await held.started;
        clock.time += LEASE_MS;
        expect(deliveries.session(id, REPO, { mode: 'takeover', deliveryKey: 'APP-1', ownerId: other, epoch: 1 }).epoch).toBe(2);
        const fresh = cut(2, 1);
        const accepted = await deliveries.commit(id, REPO, fresh.commit, fresh.fingerprint, fresh.bytes);
        held.release();
        expect(await pending).toBe('writer_fenced');
        expect(deliveries.current(id).digest).toBe(accepted.digest);
        expect(metadata.transact(id, tx => tx.accepted(1, 1))).toBeUndefined();
        expect(await code(() => deliveries.snapshot(id, stale.commit.digest))).toBe('not_found');
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
      await harness(id, async ({ metadata, blobs }) => {
        const deliveries = new Deliveries(metadata, blobs, { now: () => 6_000_000 });
        expect(await code(() => deliveries.current(id))).toBe('not_found');
        expect(await code(() => deliveries.snapshot(id, hex(1)))).toBe('not_found');
        expect(await code(() => deliveries.status(id, REPO))).toBe('not_found');
        expect(await code(() => deliveries.session(id, REPO, { ...acquire, mode: 'resume', epoch: 1 }))).toBe('not_found');
        expect(metadata.transact(id, tx => tx.row())).toBeUndefined();
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
      });
    });
  });
}
