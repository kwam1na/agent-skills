// Fresh portable stores in a temporary data directory, for the node tests.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteMetadata } from '../../src/node/sqlite-store';
import { FileBlobs, realFiles, type BlobFiles } from '../../src/node/file-blobs';

export async function withStores<T>(run: (stores: { dataDir: string; metadata: SqliteMetadata; blobs: FileBlobs }) => Promise<T>,
  files: BlobFiles = realFiles): Promise<T> {
  const dataDir = mkdtempSync(join(tmpdir(), 'hosted-store-'));
  const metadata = await SqliteMetadata.open(join(dataDir, 'metadata.sqlite'));
  try {
    return await run({ dataDir, metadata, blobs: await FileBlobs.open(dataDir, files) });
  } finally {
    metadata.close();
    rmSync(dataDir, { recursive: true, force: true });
  }
}
