import { manyDeliveriesContract, storageContract, type Harness } from '../contract/storage';
import { withStores } from './stores';

// Each scenario gets its own data directory: one SQLite database holding every delivery, and filesystem blobs.
const harness: Harness = (_publicId, scenario) => withStores(({ metadata, blobs }) => scenario({ metadata, blobs,
  persisted: () => [...metadata.db.prepare('SELECT * FROM deliveries').all(), ...metadata.db.prepare('SELECT * FROM receipts').all()] }));
storageContract('SQLite and the filesystem', harness);
manyDeliveriesContract('SQLite and the filesystem', harness);
