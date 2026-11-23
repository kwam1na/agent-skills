// The Node HTTP adapter: node:http in front of the core service. It never reads Host or X-Forwarded-* for routing or
// identity, logs nothing per request, and writes no response of its own: every failure before a request reaches the
// core closes the socket with no bytes, as the spec's "Failures before a request reaches the service" names.
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { Readable } from 'node:stream';

export interface Limits {
  /** Time for a request's header block to arrive. */
  headersTimeoutMs: number;
  /** Time for the whole request, its body included, to arrive. */
  requestTimeoutMs: number;
  keepAliveTimeoutMs: number;
  maxHeaderBytes: number;
  maxConnections: number;
}
export const DEFAULT_LIMITS: Limits = Object.freeze({
  headersTimeoutMs: 10_000, requestTimeoutMs: 120_000, keepAliveTimeoutMs: 5_000, maxHeaderBytes: 16 * 1024, maxConnections: 256,
});

export interface Service { fetch(request: Request): Promise<Response> }

// Paths are read from the request target only; this origin is a placeholder that no route or check reads.
const BASE = 'http://service.invalid';
// The fetch standard refuses these methods in a Request; the core treats any method it does not route alike.
const UNREPRESENTABLE = new Set(['CONNECT', 'TRACE', 'TRACK']);

/** The request body as a web stream. Cancelling it discards the rest of the body rather than destroying the socket,
 * so the core's 413 still reaches the client. */
function body(request: IncomingMessage): ReadableStream<Uint8Array> {
  let finished = false;
  return new ReadableStream<Uint8Array>({
    start(controller) {
      request.on('data', (chunk: Buffer) => {
        controller.enqueue(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength));
        if ((controller.desiredSize ?? 0) <= 0) request.pause();
      });
      request.on('end', () => { if (!finished) { finished = true; controller.close(); } });
      request.on('error', (error) => { if (!finished) { finished = true; controller.error(error); } });
      request.on('close', () => { if (!finished) { finished = true; controller.error(new Error('the request was aborted')); } });
    },
    pull() { request.resume(); },
    cancel() { finished = true; request.removeAllListeners('data'); request.resume(); },
  }, { highWaterMark: 4 });
}

function toRequest(incoming: IncomingMessage): Request {
  // An absolute-form or empty target is never a route: it is read as a path no route matches.
  const target = incoming.url?.startsWith('/') ? incoming.url : '/\u0000';
  const headers = new Headers();
  const raw = incoming.rawHeaders;
  for (let i = 0; i < raw.length; i += 2) headers.append(raw[i], raw[i + 1]);
  const method = UNREPRESENTABLE.has(incoming.method ?? '') ? 'OPTIONS' : incoming.method ?? 'GET';
  const carries = method !== 'GET' && method !== 'HEAD';
  return new Request(BASE + target, { method, headers, body: carries ? body(incoming) : undefined, ...(carries ? { duplex: 'half' } : {}) } as RequestInit);
}

async function respond(service: Service, incoming: IncomingMessage, outgoing: ServerResponse) {
  let response: Response;
  try {
    response = await service.fetch(toRequest(incoming));
  } catch {
    outgoing.destroy();
    return;
  }
  outgoing.statusCode = response.status;
  response.headers.forEach((value, name) => outgoing.setHeader(name, value));
  // A request whose body was not read to its end cannot carry another request on the same connection.
  if (!incoming.complete) outgoing.setHeader('Connection', 'close');
  if (!response.body || incoming.method === 'HEAD') {
    await response.body?.cancel().catch(() => {});
    outgoing.end();
    return;
  }
  Readable.fromWeb(response.body as import('node:stream/web').ReadableStream<Uint8Array>)
    .on('error', () => outgoing.destroy())
    .pipe(outgoing);
}

export function createNodeServer(service: Service, limits: Limits = DEFAULT_LIMITS): Server {
  const server = createServer({
    headersTimeout: limits.headersTimeoutMs,
    requestTimeout: limits.requestTimeoutMs,
    keepAliveTimeout: limits.keepAliveTimeoutMs,
    maxHeaderSize: limits.maxHeaderBytes,
    connectionsCheckingInterval: Math.max(50, Math.min(1_000, Math.floor(limits.headersTimeoutMs / 4))),
  }, (incoming, outgoing) => {
    outgoing.on('error', () => {});
    void respond(service, incoming, outgoing);
  });
  server.maxConnections = limits.maxConnections;
  // Never Node's own 400, 408 or 431: a malformed request line, an oversized header block and a timeout close the
  // socket with no bytes written.
  server.on('clientError', (_error, socket) => socket.destroy());
  return server;
}
