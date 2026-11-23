// The request handler for hosted-delivery-api/1: routing, authentication, validation and the viewer's asset table,
// on Web-standard Request and Response only. Each adapter supplies the deliveries and the publishers.
import { assets, shell, retainedShells } from '../generated-assets';
import { timingSafeEqual } from 'node:crypto';
import { unwrap, digest, identifier, integer, object, exact, parse, projection, readBytes, MAX_BYTES, MAX_SESSION_BYTES, MAX_CONFIRM_BYTES, KEY, REPOSITORY,
  LEGACY_RENDERER, supportedRenderer, fail, json, errorResponse, headers, type Commit } from '../protocol';
import { projectionV2 } from '../projection-v2';
import type { DeliveryEndpoint, Session } from './deliveries';
import type { Publishers } from './ports';

export interface ServiceOptions {
  /** The delivery a public id names. Called for every routed request; it must create no state by itself. */
  deliveries(publicId: string): DeliveryEndpoint;
  publishers: Publishers;
}

const SHELL_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; connect-src 'self'; img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
// Binary assets (the fonts) are generated as base64; each is decoded once, when the service module loads.
const bodies = new Map<string, Uint8Array<ArrayBuffer>>();
for (const [path, asset] of Object.entries(assets))
  if (asset.encoding === 'base64') bodies.set(path, Uint8Array.from(atob(asset.body), (c) => c.charCodeAt(0)));

async function authenticate(request: Request, publishers: Publishers, repo: string) {
  const header = request.headers.get('Authorization');
  if (!header || !/^Bearer [A-Za-z0-9_-]{43,128}$/.test(header)) fail('unauthorized', 401);
  const computed = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(header.slice(7)));
  const expected = await publishers.expected(repo);
  const expectedDigest = typeof expected === 'string' && /^[a-f0-9]{64}$/.test(expected) ? expected : null;
  const bytes = Uint8Array.from((expectedDigest ?? '0'.repeat(64)).match(/../g)!, hex => parseInt(hex, 16));
  if (!timingSafeEqual(new Uint8Array(computed), bytes) || expectedDigest === null) fail('unauthorized', 401);
}
function envelope(request: Request): Commit {
  const rendererVersion = supportedRenderer(request.headers.get('X-Renderer-Version'));
  const numberHeader = (name: string) => {
    const value = request.headers.get(name);
    if (!value || !/^[1-9][0-9]{0,15}$/.test(value)) fail('invalid_sequence');
    return integer(Number(value));
  };
  return { epoch: numberHeader('X-Writer-Epoch'), sequence: numberHeader('X-Snapshot-Sequence'),
    digest: identifier(request.headers.get('X-Snapshot-Digest')), rendererVersion };
}

export function createService({ deliveries, publishers }: ServiceOptions) {
  async function fetch(request: Request): Promise<Response> {
    try {
      const url = new URL(request.url);
      if (url.search || url.hash || /[%\\]/.test(url.pathname)) fail('not_found', 404);
      const write = /^\/api\/v1\/repos\/([A-Za-z0-9_-]+)\/deliveries\/([a-f0-9]{64})\/(session|snapshots|confirm|status)$/.exec(url.pathname);
      if (write) {
        const [, repo, publicId, action] = write;
        identifier(repo, REPOSITORY);
        await authenticate(request, publishers, repo); // Before delivery lookup, parsing or storage mutation.
        if ((action === 'status' && request.method !== 'GET') || (action !== 'status' && request.method !== 'POST')) fail('method_not_allowed', 405);
        if (action === 'status') return json(unwrap(await deliveries(publicId).status(repo)));
        if (action === 'session') {
          const body = object(parse(await readBytes(request, MAX_SESSION_BYTES)));
          exact(body, ['deliveryKey', 'ownerId', 'mode', 'epoch']);
          if (typeof body.mode !== 'string' || !['acquire', 'resume', 'takeover'].includes(body.mode)) fail('invalid_envelope');
          const session: Session = { deliveryKey: identifier(body.deliveryKey, KEY), ownerId: identifier(body.ownerId), mode: body.mode as Session['mode'] };
          if (session.mode === 'acquire') { if (body.epoch !== undefined) fail('invalid_envelope'); }
          else session.epoch = integer(body.epoch);
          return json(unwrap(await deliveries(publicId).session(repo, session)));
        }
        const commit = envelope(request);
        if (action === 'confirm') {
          const body = object(parse(await readBytes(request, MAX_CONFIRM_BYTES)));
          exact(body, ['sourceFingerprint']);
          return json(unwrap(await deliveries(publicId).confirm(repo, commit, identifier(body.sourceFingerprint))));
        }
        const bytes = await readBytes(request, MAX_BYTES);
        if (await digest(bytes) !== commit.digest) fail('digest_mismatch', 422);
        const validate = commit.rendererVersion === LEGACY_RENDERER ? projectionV2 : projection;
        const { sourceFingerprint } = validate(parse(bytes));
        return json(unwrap(await deliveries(publicId).commit(repo, publicId, commit, sourceFingerprint, bytes)));
      }
      if (request.method === 'GET' && Object.hasOwn(assets, url.pathname)) {
        const asset = assets[url.pathname];
        return new Response(bodies.get(url.pathname) ?? asset.body, { headers: { ...headers, 'Content-Type': asset.type, 'Cache-Control': 'public, max-age=31536000, immutable' } });
      }
      const page = /^\/d\/([a-f0-9]{64})$/.exec(url.pathname);
      if (page && request.method === 'GET') {
        const pointer = unwrap(await deliveries(page[1]).current());
        const selected = Object.hasOwn(retainedShells, pointer.rendererVersion) ? retainedShells[pointer.rendererVersion] : shell;
        return new Response(selected, { headers: { ...headers, 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': SHELL_CSP } });
      }
      const read = /^\/d\/([a-f0-9]{64})\/(current|snapshots\/([a-f0-9]{64}))$/.exec(url.pathname);
      if (read && request.method === 'GET') {
        const [, publicId, action, digest] = read;
        const delivery = deliveries(publicId);
        if (action === 'current') return json(unwrap(await delivery.current()));
        return new Response(unwrap(await delivery.snapshot(digest)), { headers: { ...headers, 'Content-Type': 'application/json' } });
      }
      return json({ error: 'not_found' }, 404);
    } catch (error) { return errorResponse(error); }
  }
  return { fetch };
}
