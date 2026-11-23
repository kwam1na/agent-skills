// The two storage ports the neutral core is written against, and the contract each adapter keeps.
// The Durable Object kept parts of this contract implicitly; here it is stated, and the storage contract suite
// (test/contract/) holds every store to it.
import type { Receipt } from '../protocol';

/** One delivery's metadata, as the core reads it. */
export interface DeliveryRow {
  repo: string;
  deliveryKey: string;
  ownerId: string;
  epoch: number;
  leaseUntil: number;
  sequence: number;
  /** The blob key of the committed snapshot, or null before the first commit. */
  pointer: string | null;
  receipt: Receipt | null;
  confirmedAt: number | null;
}

/** The operations on one delivery's metadata inside one transaction. */
export interface MetadataTransaction {
  /** The delivery's row, or undefined when the public id is unbound. Reading creates no state. */
  row(): DeliveryRow | undefined;
  /** Binds an unbound public id. */
  create(row: Pick<DeliveryRow, 'repo' | 'deliveryKey' | 'ownerId' | 'epoch' | 'leaseUntil' | 'sequence'>): void;
  /** Advances the epoch by one for `ownerId`, resetting the sequence to 0. */
  advance(ownerId: string, leaseUntil: number): void;
  /** The receipt accepted for this epoch and sequence, with its digest, if any. */
  accepted(epoch: number, sequence: number): { digest: string; receipt: Receipt } | undefined;
  /** Records the one durable receipt for its epoch and sequence. */
  addReceipt(receipt: Receipt): void;
  /** Moves the pointer to a committed snapshot. */
  movePointer(change: { sequence: number; pointer: string; receipt: Receipt; confirmedAt: number; leaseUntil: number }): void;
  /** Records a source confirmation. */
  confirm(confirmedAt: number, leaseUntil: number): void;
}

/**
 * Per-delivery metadata. The contract:
 * - each `transact` call is one synchronous, atomic transaction on that delivery's metadata: `run` never awaits,
 *   and when it throws, nothing it did persists;
 * - nothing is assumed to hold across an `await` between two calls: the core rechecks epoch, lease and sequence
 *   inside the commit transaction after the blob write;
 * - a read of an unknown public id creates no state.
 */
export interface MetadataStore {
  transact<T>(publicId: string, run: (tx: MetadataTransaction) => T): T;
}

/** Snapshot bytes are a body a Response can carry. */
export type SnapshotBody = ReadableStream<Uint8Array> | Uint8Array<ArrayBuffer>;

/**
 * Write-once snapshot bytes, content addressed: a key names exactly one byte sequence. `put` resolves once the key
 * holds these bytes; a key that already exists is left as it is and counts as success. It rejects on failure.
 * `get` returns null for a key never written.
 */
export interface BlobStore {
  put(key: string, bytes: Uint8Array): Promise<void>;
  get(key: string): Promise<SnapshotBody | null>;
}

/** Server time in Unix milliseconds. Injected in tests; real time otherwise. */
export interface Clock {
  now(): number;
}

/**
 * Where publishing digests live. `expected` returns the repository's lowercase SHA-256 token digest, or null when
 * the repository is unknown or disabled. It throws when its record cannot be read or is corrupt, which denies access.
 */
export interface Publishers {
  expected(repository: string): Promise<string | null>;
}
