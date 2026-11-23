// Portable metadata: one SQLite database, rows keyed by public id, opened by exactly one process.
// locking_mode=EXCLUSIVE holds the file lock from the startup transaction until the process exits, so a second
// instance on the same data directory fails at startup, and the OS drops the lock with a crashed process.
import type { DatabaseSync } from 'node:sqlite';
import type { Receipt } from '../protocol';
import type { DeliveryRow, MetadataStore, MetadataTransaction } from '../core/ports';

export class StartupError extends Error {
  constructor(readonly code: string, message: string) { super(`${code}: ${message}`); }
}

type Stored = Omit<DeliveryRow, 'receipt'> & { receipt: string | null };
const SCHEMA = `CREATE TABLE IF NOT EXISTS deliveries (
  publicId TEXT PRIMARY KEY, repo TEXT NOT NULL, deliveryKey TEXT NOT NULL, ownerId TEXT NOT NULL,
  epoch INTEGER NOT NULL, leaseUntil INTEGER NOT NULL, sequence INTEGER NOT NULL,
  pointer TEXT, receipt TEXT, confirmedAt INTEGER
) STRICT;
CREATE TABLE IF NOT EXISTS receipts (
  publicId TEXT NOT NULL, epoch INTEGER NOT NULL, sequence INTEGER NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL,
  PRIMARY KEY (publicId, epoch, sequence)
) STRICT;`;

export class SqliteMetadata implements MetadataStore {
  private constructor(readonly db: DatabaseSync) {}

  /** Opens the database at `path`, takes its exclusive lock and makes its tables. */
  static async open(path: string): Promise<SqliteMetadata> {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(path);
    try {
      db.exec('PRAGMA busy_timeout=0');
      db.exec('PRAGMA locking_mode=EXCLUSIVE');
      db.exec('PRAGMA synchronous=FULL');
      // EXCLUSIVE locking mode only locks on first access: take the lock now, so even an idle instance holds it.
      db.exec('BEGIN EXCLUSIVE');
      db.exec(SCHEMA);
      db.exec('COMMIT');
    } catch (error) {
      db.close();
      // SQLITE_BUSY or SQLITE_LOCKED: another process holds the lock this instance needs.
      if ([5, 6].includes((error as { errcode?: number }).errcode ?? 0))
        throw new StartupError('hosted.data_dir_in_use', 'another instance holds this data directory\'s database');
      throw error;
    }
    return new SqliteMetadata(db);
  }

  close() { if (this.db.isOpen) this.db.close(); }

  transact<T>(publicId: string, run: (tx: MetadataTransaction) => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    let value: T;
    try {
      value = run(this.transaction(publicId));
      if (value && typeof (value as { then?: unknown }).then === 'function') throw new Error('a metadata transaction never awaits');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
    this.db.exec('COMMIT');
    return value;
  }

  private transaction(publicId: string): MetadataTransaction {
    const db = this.db;
    return {
      row() {
        const row = db.prepare('SELECT repo, deliveryKey, ownerId, epoch, leaseUntil, sequence, pointer, receipt, confirmedAt FROM deliveries WHERE publicId=?')
          .get(publicId) as Stored | undefined;
        return row ? { ...row, receipt: row.receipt ? JSON.parse(row.receipt) as Receipt : null } : undefined;
      },
      create(row) {
        db.prepare('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL)')
          .run(publicId, row.repo, row.deliveryKey, row.ownerId, row.epoch, row.leaseUntil, row.sequence);
      },
      advance(ownerId, leaseUntil) {
        db.prepare('UPDATE deliveries SET ownerId=?, epoch=epoch+1, sequence=0, leaseUntil=? WHERE publicId=?').run(ownerId, leaseUntil, publicId);
      },
      accepted(epoch, sequence) {
        const row = db.prepare('SELECT digest, receipt FROM receipts WHERE publicId=? AND epoch=? AND sequence=?')
          .get(publicId, epoch, sequence) as { digest: string; receipt: string } | undefined;
        return row ? { digest: row.digest, receipt: JSON.parse(row.receipt) as Receipt } : undefined;
      },
      addReceipt(receipt) {
        db.prepare('INSERT INTO receipts VALUES (?,?,?,?,?)').run(publicId, receipt.epoch, receipt.sequence, receipt.digest, JSON.stringify(receipt));
      },
      movePointer(change) {
        db.prepare('UPDATE deliveries SET sequence=?, pointer=?, receipt=?, confirmedAt=?, leaseUntil=? WHERE publicId=?')
          .run(change.sequence, change.pointer, JSON.stringify(change.receipt), change.confirmedAt, change.leaseUntil, publicId);
      },
      confirm(confirmedAt, leaseUntil) {
        db.prepare('UPDATE deliveries SET confirmedAt=?, leaseUntil=? WHERE publicId=?').run(confirmedAt, leaseUntil, publicId);
      },
    };
  }
}
