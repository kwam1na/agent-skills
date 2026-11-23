// The portable server: the core service over SQLite metadata and filesystem blobs, one process, one data directory.
//   node server.mjs                         serve, configured by HOST, PORT, DATA_DIR and PUBLISHERS or PUBLISHERS_FILE
//   node server.mjs provision-token <repo>  print a new publishing token once, and the PUBLISHERS entry for it
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';
import type { AddressInfo } from 'node:net';
import { REPOSITORY, ApiError } from '../protocol';
import { createService } from '../core/service';
import { Deliveries, endpoint } from '../core/deliveries';
import { publisherMap } from '../core/publishers';
import type { Publishers } from '../core/ports';
import { SqliteMetadata, StartupError } from './sqlite-store';
import { FileBlobs } from './file-blobs';
import { createNodeServer, DEFAULT_LIMITS, type Limits } from './http';

export interface Config {
  host: string;
  port: number;
  dataDir: string;
  publishers: Publishers;
}

function fail(code: string, message: string): never { throw new StartupError(code, message); }

/** Reads the configuration from `env`, refusing anything missing, ambiguous or malformed with a named error. */
export function readConfig(env: Record<string, string | undefined>): Config {
  const host = env.HOST ?? '127.0.0.1';
  const port = Number(env.PORT ?? '8080');
  if (!/^\d{1,5}$/.test(env.PORT ?? '8080') || port > 65535) fail('hosted.config_invalid', 'PORT must be a port number');
  const dataDir = env.DATA_DIR;
  if (!dataDir || !isAbsolute(dataDir)) fail('hosted.config_invalid', 'DATA_DIR must be an absolute path');
  const inline = env.PUBLISHERS, file = env.PUBLISHERS_FILE;
  if ((inline === undefined) === (file === undefined)) fail('hosted.config_invalid', 'set exactly one of PUBLISHERS and PUBLISHERS_FILE');
  const read = () => file === undefined ? inline! : readFileSync(file, 'utf8');
  // Checked once at startup, so a bad map fails here and not at the first request.
  try {
    const map = JSON.parse(read());
    if (!map || typeof map !== 'object' || Array.isArray(map) || !Object.keys(map).length) throw new Error();
    for (const [repository, digest] of Object.entries(map))
      if (!REPOSITORY.test(repository) || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) throw new Error();
  } catch {
    fail('hosted.publishers_invalid', 'the publishers map must be a JSON object of repository IDs to lowercase SHA-256 digests');
  }
  // A PUBLISHERS_FILE is read on every check, so a rotation takes effect without a restart. A file that cannot be
  // read, or no longer parses, denies access.
  const publishers: Publishers = {
    async expected(repository) {
      let text: string;
      try { text = read(); } catch { throw new ApiError('temporarily_unavailable', 503); }
      return publisherMap(text)(repository);
    },
  };
  return { host, port, dataDir, publishers };
}

export interface Running {
  origin: string;
  close(): Promise<void>;
}

/** Opens the data directory and serves on it. A second instance on the same directory fails here. */
export async function serve(config: Config, limits: Limits = DEFAULT_LIMITS): Promise<Running> {
  const metadata = await SqliteMetadata.open(join(config.dataDir, 'metadata.sqlite'));
  let blobs: FileBlobs;
  try { blobs = await FileBlobs.open(config.dataDir); } catch (error) { metadata.close(); throw error; }
  const deliveries = new Deliveries(metadata, blobs);
  const service = createService({ deliveries: publicId => endpoint(deliveries, publicId), publishers: config.publishers });
  const server = createNodeServer(service, limits);
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(config.port, config.host, () => { server.off('error', reject); resolve(); });
  }).catch((error) => { metadata.close(); throw error; });
  const address = server.address() as AddressInfo;
  const host = address.family === 'IPv6' ? `[${address.address}]` : address.address;
  let closing: Promise<void> | undefined;
  return {
    origin: `http://${host}:${address.port}`,
    close() {
      closing ??= new Promise<void>((resolve) => {
        // Stop accepting, let requests in flight finish, then drop what is left.
        const force = setTimeout(() => server.closeAllConnections(), 10_000);
        server.close(() => { clearTimeout(force); metadata.close(); resolve(); });
        server.closeIdleConnections();
      });
      return closing;
    },
  };
}

/** A new token of 256 random bits, unpadded base64url, and the PUBLISHERS entry that accepts it. */
export function provisionToken(repository: string) {
  if (!REPOSITORY.test(repository)) fail('hosted.config_invalid', 'the repository ID must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}');
  const token = randomBytes(32).toString('base64url');
  return { token, entry: { [repository]: createHash('sha256').update(token).digest('hex') } };
}

export async function main(argv: string[], env: Record<string, string | undefined>): Promise<number> {
  try {
    if (argv[0] === 'provision-token') {
      if (argv.length !== 2) fail('hosted.usage', 'usage: provision-token <repository>');
      const { token, entry } = provisionToken(argv[1]);
      process.stdout.write(`token: ${token}\nPUBLISHERS entry: ${JSON.stringify(entry)}\n`);
      return 0;
    }
    if (argv.length) fail('hosted.usage', 'usage: server.mjs [provision-token <repository>]');
    const running = await serve(readConfig(env));
    process.stdout.write(`hosted delivery service listening on ${running.origin}\n`);
    await new Promise<void>((resolve) => {
      const stop = () => { void running.close().then(resolve); };
      process.once('SIGTERM', stop);
      process.once('SIGINT', stop);
    });
    return 0;
  } catch (error) {
    const detail = (error as NodeJS.ErrnoException)?.code ?? 'unexpected';
    process.stderr.write(`${error instanceof StartupError ? error.message : `hosted.startup_failed: the server did not start (${detail})`}\n`);
    return error instanceof StartupError && error.code === 'hosted.usage' ? 2 : 1;
  }
}
