import { env } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { DurableObjectMetadata, R2Blobs } from '../src/cloudflare/delivery-object';
import { storageContract } from './contract/storage';
import { stub } from './helpers';

// Each scenario runs inside its delivery's own Durable Object, on its SQLite storage, with snapshot bytes in R2.
storageContract('Durable Object and R2', (publicId, scenario) =>
  runInDurableObject(stub(publicId), (_instance, state) =>
    scenario({ metadata: new DurableObjectMetadata(state.storage), blobs: new R2Blobs(env.SNAPSHOTS),
      persisted: () => state.storage.sql.exec("SELECT name FROM sqlite_master WHERE name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%'").toArray() })));
