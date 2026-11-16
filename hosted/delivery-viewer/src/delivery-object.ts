import { DurableObject } from 'cloudflare:workers';
import { ApiError, LEASE_MS, STALE_MS, RENDERER, fail, type Receipt, type Commit, result } from './protocol';

type Row = { repo: string; deliveryKey: string; ownerId: string; epoch: number; leaseUntil: number; sequence: number; pointer: string | null; receipt: string | null; confirmedAt: number | null };
export type Session = { mode: 'acquire' | 'resume' | 'takeover'; deliveryKey: string; ownerId: string; epoch?: number };

export class DeliveryObject extends DurableObject<Env> {
  session(repo: string, session: Session) { return result(() => this.sessionImpl(repo, session)); }
  status(repo: string) { return result(() => this.statusImpl(repo)); }
  commit(repo: string, publicId: string, commit: Commit, fingerprint: string, bytes: Uint8Array) {
    return result(() => this.commitImpl(repo, publicId, commit, fingerprint, bytes));
  }
  confirm(repo: string, commit: Commit, fingerprint: string) { return result(() => this.confirmImpl(repo, commit, fingerprint)); }
  current() { return result(() => this.currentImpl()); }
  snapshot(digest: string) { return result(() => this.snapshotImpl(digest)); }
  // No constructor writes: unknown public capabilities must not initialize SQL metadata.
  private initialize() {
    this.ctx.storage.sql.exec(`CREATE TABLE IF NOT EXISTS delivery (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), repo TEXT NOT NULL, deliveryKey TEXT NOT NULL,
      ownerId TEXT NOT NULL, epoch INTEGER NOT NULL, leaseUntil INTEGER NOT NULL, sequence INTEGER NOT NULL,
      pointer TEXT, receipt TEXT, confirmedAt INTEGER
    ); CREATE TABLE IF NOT EXISTS receipts (epoch INTEGER, sequence INTEGER, digest TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(epoch, sequence));`);
  }
  private row(): Row | undefined {
    const exists = this.ctx.storage.sql.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='delivery'").toArray();
    if (!exists.length) return undefined;
    return this.ctx.storage.sql.exec<Row>('SELECT * FROM delivery WHERE singleton=1').toArray()[0];
  }
  private bound(repo: string): Row {
    const row = this.row();
    if (!row || row.repo !== repo) fail('not_found', 404);
    return row;
  }
  private writer(row: Row, epoch: number) {
    if (row.epoch !== epoch) fail('writer_fenced', 409);
    if (row.leaseUntil <= Date.now()) fail('lease_expired', 409);
  }
  private async sessionImpl(repo: string, session: Session) {
    return this.ctx.storage.transactionSync(() => {
      const existing = this.row();
      const now = Date.now();
      if (!existing) {
        if (session.mode !== 'acquire') fail('not_found', 404);
        this.initialize();
        this.ctx.storage.sql.exec('INSERT INTO delivery VALUES (1,?,?,?,?,?,?,NULL,NULL,NULL)', repo, session.deliveryKey, session.ownerId, 1, now + LEASE_MS, 0);
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
          if (existing.leaseUntil <= now) {
            // Explicit resume/takeover only, compare-and-swap the caller's persisted epoch.
            this.ctx.storage.sql.exec('UPDATE delivery SET ownerId=?, epoch=epoch+1, sequence=0, leaseUntil=? WHERE singleton=1', session.ownerId, now + LEASE_MS);
          }
          // An active resume is inspection, not source confirmation.
        }
      }
      const row = this.bound(repo);
      return { protocolVersion: 'hosted-delivery-api/1', deliveryKey: row.deliveryKey, ownerId: row.ownerId, epoch: row.epoch, sequence: row.sequence, leaseUntil: row.leaseUntil, receipt: row.receipt ? JSON.parse(row.receipt) as Receipt : null };
    });
  }
  private async statusImpl(repo: string) {
    const row = this.bound(repo);
    return { protocolVersion: 'hosted-delivery-api/1', deliveryKey: row.deliveryKey, ownerId: row.ownerId, epoch: row.epoch, sequence: row.sequence, leaseUntil: row.leaseUntil, receipt: row.receipt ? JSON.parse(row.receipt) as Receipt : null };
  }
  private accepted(epoch: number, sequence: number, digest: string): Receipt | undefined {
    const rows = this.ctx.storage.sql.exec<{ digest: string; receipt: string }>('SELECT digest, receipt FROM receipts WHERE epoch=? AND sequence=?', epoch, sequence).toArray();
    if (!rows.length) return undefined;
    if (rows[0].digest !== digest) fail('sequence_conflict', 409);
    return JSON.parse(rows[0].receipt) as Receipt;
  }
  private admissible(repo: string, commit: Commit): Receipt | undefined {
    const row = this.bound(repo);
    const accepted = this.accepted(commit.epoch, commit.sequence, commit.digest);
    if (accepted) return accepted;
    this.writer(row, commit.epoch);
    if (commit.sequence <= row.sequence) fail('sequence_superseded', 409);
  }
  private async commitImpl(repo: string, publicId: string, commit: Commit, fingerprint: string, bytes: Uint8Array) {
    const retry = this.admissible(repo, commit);
    if (retry) return retry;
    const key = `${publicId}/${commit.epoch}/${commit.sequence}/${commit.digest}`;
    // Content addressed and conditional: an exact concurrent retry never overwrites bytes.
    await this.env.SNAPSHOTS.put(key, bytes, { onlyIf: { etagDoesNotMatch: '*' }, httpMetadata: { contentType: 'application/json' } });
    return this.ctx.storage.transactionSync(() => {
      // External I/O permits interleaving. Re-read persisted fencing and receipt here.
      const accepted = this.admissible(repo, commit);
      if (accepted) return accepted;
      const now = Date.now();
      const receipt: Receipt = { protocolVersion: 'hosted-delivery-api/1', rendererVersion: RENDERER,
        epoch: commit.epoch, sequence: commit.sequence, digest: commit.digest, sourceFingerprint: fingerprint,
        publishedAt: now, sourceConfirmedAt: now };
      const encoded = JSON.stringify(receipt);
      this.ctx.storage.sql.exec('INSERT INTO receipts VALUES (?,?,?,?)', commit.epoch, commit.sequence, commit.digest, encoded);
      this.ctx.storage.sql.exec('UPDATE delivery SET sequence=?, pointer=?, receipt=?, confirmedAt=?, leaseUntil=? WHERE singleton=1', commit.sequence, key, encoded, now, now + LEASE_MS);
      return receipt;
    });
  }
  private async confirmImpl(repo: string, commit: Commit, fingerprint: string) {
    return this.ctx.storage.transactionSync(() => {
      const row = this.bound(repo);
      this.writer(row, commit.epoch);
      const receipt = row.receipt ? JSON.parse(row.receipt) as Receipt : null;
      if (!receipt || receipt.epoch !== commit.epoch || receipt.sequence !== commit.sequence || receipt.digest !== commit.digest || receipt.sourceFingerprint !== fingerprint) fail('confirmation_conflict', 409);
      const now = Date.now();
      this.ctx.storage.sql.exec('UPDATE delivery SET confirmedAt=?, leaseUntil=? WHERE singleton=1', now, now + LEASE_MS);
      return { protocolVersion: 'hosted-delivery-api/1', sourceConfirmedAt: now, leaseUntil: now + LEASE_MS, receipt };
    });
  }
  private async currentImpl() {
    const row = this.row();
    if (!row?.receipt) fail('not_found', 404);
    const receipt = JSON.parse(row.receipt) as Receipt;
    const now=Date.now();
    return { ...receipt, serverNow: now, sourceConfirmedAt: row.confirmedAt, staleAfterMs: STALE_MS, stale: now - (row.confirmedAt || 0) >= STALE_MS };
  }
  private async snapshotImpl(digest: string) {
    const row = this.row();
    if (!row?.receipt || !row.pointer || (JSON.parse(row.receipt) as Receipt).digest !== digest) fail('not_found', 404);
    const object = await this.env.SNAPSHOTS.get(row.pointer);
    if (!object) throw new ApiError('temporarily_unavailable', 503);
    return object.body;
  }
}
