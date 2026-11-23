// The delivery state machine: sessions, commits, confirmations and the committed pointer, written against the
// MetadataStore and BlobStore ports. It throws ApiError for every refusal; `endpoint` turns that into Results.
import { ApiError, LEASE_MS, STALE_MS, fail, result, type Commit, type Receipt, type Result } from '../protocol';
import type { BlobStore, SnapshotBody, Clock, DeliveryRow, MetadataStore, MetadataTransaction } from './ports';

export type Session = { mode: 'acquire' | 'resume' | 'takeover'; deliveryKey: string; ownerId: string; epoch?: number };
export interface SessionView {
  protocolVersion: 'hosted-delivery-api/1';
  deliveryKey: string;
  ownerId: string;
  epoch: number;
  sequence: number;
  leaseUntil: number;
  receipt: Receipt | null;
}
export interface Confirmation {
  protocolVersion: 'hosted-delivery-api/1';
  sourceConfirmedAt: number;
  leaseUntil: number;
  receipt: Receipt;
}
export interface Current extends Receipt {
  serverNow: number;
  staleAfterMs: number;
  stale: boolean;
}

const realTime: Clock = { now: () => Date.now() };

function view(row: DeliveryRow): SessionView {
  return { protocolVersion: 'hosted-delivery-api/1', deliveryKey: row.deliveryKey, ownerId: row.ownerId, epoch: row.epoch,
    sequence: row.sequence, leaseUntil: row.leaseUntil, receipt: row.receipt };
}

export class Deliveries {
  constructor(private readonly metadata: MetadataStore, private readonly blobs: BlobStore, private readonly clock: Clock = realTime) {}

  private bound(tx: MetadataTransaction, repo: string): DeliveryRow {
    const row = tx.row();
    if (!row || row.repo !== repo) fail('not_found', 404);
    return row;
  }
  private writer(row: DeliveryRow, epoch: number, now: number) {
    if (row.epoch !== epoch) fail('writer_fenced', 409);
    if (row.leaseUntil <= now) fail('lease_expired', 409);
  }
  /** A retry's original receipt, or undefined when this commit may proceed; throws when it may not. */
  private admissible(tx: MetadataTransaction, repo: string, commit: Commit): Receipt | undefined {
    const row = this.bound(tx, repo);
    if (row.receipt && row.receipt.rendererVersion !== commit.rendererVersion) fail('unsupported_renderer', 422);
    const accepted = tx.accepted(commit.epoch, commit.sequence);
    if (accepted) {
      if (accepted.digest !== commit.digest) fail('sequence_conflict', 409);
      return accepted.receipt;
    }
    this.writer(row, commit.epoch, this.clock.now());
    if (commit.sequence <= row.sequence) fail('sequence_superseded', 409);
    return undefined;
  }

  session(publicId: string, repo: string, session: Session): SessionView {
    return this.metadata.transact(publicId, tx => {
      const existing = tx.row();
      const now = this.clock.now();
      if (!existing) {
        if (session.mode !== 'acquire') fail('not_found', 404);
        tx.create({ repo, deliveryKey: session.deliveryKey, ownerId: session.ownerId, epoch: 1, leaseUntil: now + LEASE_MS, sequence: 0 });
      } else {
        if (existing.repo !== repo || existing.deliveryKey !== session.deliveryKey) fail('not_found', 404);
        if (session.mode === 'acquire') {
          if (existing.ownerId !== session.ownerId) fail('lease_held', 409);
          // A lost acquisition response may be retried, but never renews a lease.
          if (existing.epoch !== 1 || existing.leaseUntil <= now) fail('resume_required', 409);
        } else {
          if (session.epoch !== existing.epoch) fail('writer_fenced', 409);
          if (session.mode === 'resume' && existing.ownerId !== session.ownerId) fail('writer_fenced', 409);
          if (session.mode === 'takeover' && existing.leaseUntil > now) fail('lease_held', 409);
          // Explicit resume/takeover only, compare-and-swap the caller's persisted epoch.
          if (existing.leaseUntil <= now) tx.advance(session.ownerId, now + LEASE_MS);
          // An active resume is inspection, not source confirmation.
        }
      }
      return view(this.bound(tx, repo));
    });
  }

  status(publicId: string, repo: string): SessionView {
    return this.metadata.transact(publicId, tx => view(this.bound(tx, repo)));
  }

  async commit(publicId: string, repo: string, commit: Commit, fingerprint: string, bytes: Uint8Array): Promise<Receipt> {
    const retry = this.metadata.transact(publicId, tx => this.admissible(tx, repo, commit));
    if (retry) return retry;
    const key = `${publicId}/${commit.epoch}/${commit.sequence}/${commit.digest}`;
    // Content addressed and write-once: an exact concurrent retry never overwrites bytes.
    await this.blobs.put(key, bytes);
    return this.metadata.transact(publicId, tx => {
      // The blob write permits interleaving. Re-read persisted fencing and receipt here.
      const accepted = this.admissible(tx, repo, commit);
      if (accepted) return accepted;
      const now = this.clock.now();
      const receipt: Receipt = { protocolVersion: 'hosted-delivery-api/1', rendererVersion: commit.rendererVersion,
        epoch: commit.epoch, sequence: commit.sequence, digest: commit.digest, sourceFingerprint: fingerprint,
        publishedAt: now, sourceConfirmedAt: now };
      tx.addReceipt(receipt);
      tx.movePointer({ sequence: commit.sequence, pointer: key, receipt, confirmedAt: now, leaseUntil: now + LEASE_MS });
      return receipt;
    });
  }

  confirm(publicId: string, repo: string, commit: Commit, fingerprint: string): Confirmation {
    return this.metadata.transact(publicId, tx => {
      const row = this.bound(tx, repo);
      const now = this.clock.now();
      this.writer(row, commit.epoch, now);
      const receipt = row.receipt;
      if (!receipt || receipt.rendererVersion !== commit.rendererVersion || receipt.epoch !== commit.epoch ||
        receipt.sequence !== commit.sequence || receipt.digest !== commit.digest || receipt.sourceFingerprint !== fingerprint)
        fail('confirmation_conflict', 409);
      tx.confirm(now, now + LEASE_MS);
      return { protocolVersion: 'hosted-delivery-api/1', sourceConfirmedAt: now, leaseUntil: now + LEASE_MS, receipt };
    });
  }

  current(publicId: string): Current {
    const row = this.metadata.transact(publicId, tx => tx.row());
    if (!row?.receipt) fail('not_found', 404);
    const now = this.clock.now();
    return { ...row.receipt, serverNow: now, sourceConfirmedAt: row.confirmedAt ?? 0, staleAfterMs: STALE_MS,
      stale: now - (row.confirmedAt || 0) >= STALE_MS };
  }

  async snapshot(publicId: string, digest: string): Promise<SnapshotBody> {
    const row = this.metadata.transact(publicId, tx => tx.row());
    if (!row?.receipt || !row.pointer || row.receipt.digest !== digest) fail('not_found', 404);
    const body = await this.blobs.get(row.pointer);
    if (!body) throw new ApiError('temporarily_unavailable', 503);
    return body;
  }
}

/** The calls the request handler makes on one delivery, each answering a Result as the Durable Object's RPC does. */
export interface DeliveryEndpoint {
  session(repo: string, session: Session): Promise<Result<SessionView>>;
  status(repo: string): Promise<Result<SessionView>>;
  commit(repo: string, publicId: string, commit: Commit, fingerprint: string, bytes: Uint8Array): Promise<Result<Receipt>>;
  confirm(repo: string, commit: Commit, fingerprint: string): Promise<Result<Confirmation>>;
  current(): Promise<Result<Current>>;
  snapshot(digest: string): Promise<Result<SnapshotBody>>;
}

/** An in-process endpoint on one delivery of `deliveries`. */
export function endpoint(deliveries: Deliveries, publicId: string): DeliveryEndpoint {
  return {
    session: (repo, value) => result(() => deliveries.session(publicId, repo, value)),
    status: repo => result(() => deliveries.status(publicId, repo)),
    commit: (repo, id, commit, fingerprint, bytes) => result(() => deliveries.commit(id, repo, commit, fingerprint, bytes)),
    confirm: (repo, commit, fingerprint) => result(() => deliveries.confirm(publicId, repo, commit, fingerprint)),
    current: () => result(() => deliveries.current(publicId)),
    snapshot: digest => result(() => deliveries.snapshot(publicId, digest)),
  };
}
