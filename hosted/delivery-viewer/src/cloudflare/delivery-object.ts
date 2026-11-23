// The Cloudflare adapter's storage: each delivery's metadata in its own SQLite Durable Object, whose
// transactionSync gives the MetadataStore its one synchronous, atomic transaction, and snapshot bytes in private R2.
import { DurableObject } from 'cloudflare:workers';
import { result, type Commit, type Receipt } from '../protocol';
import { Deliveries, type Session } from '../core/deliveries';
import type { BlobStore, DeliveryRow, MetadataStore, MetadataTransaction } from '../core/ports';

type Stored = Omit<DeliveryRow, 'receipt'> & { receipt: string | null };

/** The one delivery this Durable Object holds; the public id `transact` is given is the object's own. */
export class DurableObjectMetadata implements MetadataStore {
  constructor(private readonly storage: DurableObjectStorage) {}
  transact<T>(_publicId: string, run: (tx: MetadataTransaction) => T): T {
    return this.storage.transactionSync(() => run(this.transaction()));
  }
  private transaction(): MetadataTransaction {
    const sql = this.storage.sql;
    return {
      row() {
        const exists = sql.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='delivery'").toArray();
        if (!exists.length) return undefined;
        const row = sql.exec<Stored>('SELECT * FROM delivery WHERE singleton=1').toArray()[0];
        if (!row) return undefined;
        const { singleton: _singleton, ...rest } = row as Stored & { singleton?: number };
        return { ...rest, receipt: row.receipt ? JSON.parse(row.receipt) as Receipt : null };
      },
      create(row) {
        // Tables are made by the first binding only: unknown public capabilities never initialize SQL metadata.
        sql.exec(`CREATE TABLE IF NOT EXISTS delivery (
          singleton INTEGER PRIMARY KEY CHECK(singleton=1), repo TEXT NOT NULL, deliveryKey TEXT NOT NULL,
          ownerId TEXT NOT NULL, epoch INTEGER NOT NULL, leaseUntil INTEGER NOT NULL, sequence INTEGER NOT NULL,
          pointer TEXT, receipt TEXT, confirmedAt INTEGER
        ); CREATE TABLE IF NOT EXISTS receipts (epoch INTEGER, sequence INTEGER, digest TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(epoch, sequence));`);
        sql.exec('INSERT INTO delivery VALUES (1,?,?,?,?,?,?,NULL,NULL,NULL)', row.repo, row.deliveryKey, row.ownerId, row.epoch, row.leaseUntil, row.sequence);
      },
      advance(ownerId, leaseUntil) {
        sql.exec('UPDATE delivery SET ownerId=?, epoch=epoch+1, sequence=0, leaseUntil=? WHERE singleton=1', ownerId, leaseUntil);
      },
      accepted(epoch, sequence) {
        const rows = sql.exec<{ digest: string; receipt: string }>('SELECT digest, receipt FROM receipts WHERE epoch=? AND sequence=?', epoch, sequence).toArray();
        return rows.length ? { digest: rows[0].digest, receipt: JSON.parse(rows[0].receipt) as Receipt } : undefined;
      },
      addReceipt(receipt) {
        sql.exec('INSERT INTO receipts VALUES (?,?,?,?)', receipt.epoch, receipt.sequence, receipt.digest, JSON.stringify(receipt));
      },
      movePointer(change) {
        sql.exec('UPDATE delivery SET sequence=?, pointer=?, receipt=?, confirmedAt=?, leaseUntil=? WHERE singleton=1',
          change.sequence, change.pointer, JSON.stringify(change.receipt), change.confirmedAt, change.leaseUntil);
      },
      confirm(confirmedAt, leaseUntil) {
        sql.exec('UPDATE delivery SET confirmedAt=?, leaseUntil=? WHERE singleton=1', confirmedAt, leaseUntil);
      },
    };
  }
}

/** Private R2, written once per key: a put on an existing key is refused by its precondition and leaves the bytes. */
export class R2Blobs implements BlobStore {
  constructor(private readonly bucket: R2Bucket) {}
  async put(key: string, bytes: Uint8Array) {
    await this.bucket.put(key, bytes, { onlyIf: { etagDoesNotMatch: '*' }, httpMetadata: { contentType: 'application/json' } });
  }
  async get(key: string) {
    const object = await this.bucket.get(key);
    return object ? object.body : null;
  }
}

// RPC does not preserve custom Error properties: each call answers a safe tagged Result.
export class DeliveryObject extends DurableObject<Env> {
  // No constructor writes: unknown public capabilities must not initialize SQL metadata.
  private readonly deliveries = new Deliveries(new DurableObjectMetadata(this.ctx.storage), new R2Blobs(this.env.SNAPSHOTS));
  private static readonly self = 'self';
  session(repo: string, session: Session) { return result(() => this.deliveries.session(DeliveryObject.self, repo, session)); }
  status(repo: string) { return result(() => this.deliveries.status(DeliveryObject.self, repo)); }
  commit(repo: string, publicId: string, commit: Commit, fingerprint: string, bytes: Uint8Array) {
    return result(() => this.deliveries.commit(publicId, repo, commit, fingerprint, bytes));
  }
  confirm(repo: string, commit: Commit, fingerprint: string) { return result(() => this.deliveries.confirm(DeliveryObject.self, repo, commit, fingerprint)); }
  current() { return result(() => this.deliveries.current(DeliveryObject.self)); }
  snapshot(digest: string) { return result(() => this.deliveries.snapshot(DeliveryObject.self, digest)); }
}
