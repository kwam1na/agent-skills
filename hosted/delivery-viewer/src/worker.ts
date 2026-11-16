import {assets,shell} from './generated-assets';
import { timingSafeEqual } from 'node:crypto';
import { unwrap, digest, identifier, integer, object, exact, parse, projection, readBytes, MAX_BYTES, KEY, REPOSITORY, RENDERER, fail, json, errorResponse, headers, type Commit } from './protocol';
import { DeliveryObject, type Session } from './delivery-object';
export { DeliveryObject };

async function authenticate(request: Request, env: Env, repo: string) {
  const header = request.headers.get('Authorization');
  if (!header || !/^Bearer [A-Za-z0-9_-]{43,128}$/.test(header)) fail('unauthorized', 401);
  const computed = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(header.slice(7)));
  let publishers: Record<string, unknown>;
  try { publishers = object(JSON.parse(env.PUBLISHERS)); } catch { fail('temporarily_unavailable', 503); }
  const expected = publishers[repo];
  const valid = typeof expected === 'string' && /^[a-f0-9]{64}$/.test(expected);
  const bytes = Uint8Array.from((valid ? expected : '0'.repeat(64)).match(/../g)!, hex => parseInt(hex, 16));
  if (!timingSafeEqual(new Uint8Array(computed), bytes) || !valid) fail('unauthorized', 401);
}
function envelope(request: Request): Commit {
  if (request.headers.get('X-Renderer-Version') !== RENDERER) fail('unsupported_renderer', 422);
  const numberHeader = (name: string) => {
    const value = request.headers.get(name);
    if (!value || !/^[1-9][0-9]{0,15}$/.test(value)) fail('invalid_sequence');
    return integer(Number(value));
  };
  return { epoch: numberHeader('X-Writer-Epoch'), sequence: numberHeader('X-Snapshot-Sequence'),
    digest: identifier(request.headers.get('X-Snapshot-Digest')), rendererVersion: RENDERER };
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      const url = new URL(request.url);
      if (url.search || url.hash || /[%\\]/.test(url.pathname)) fail('not_found', 404);
      const write = /^\/api\/v1\/repos\/([A-Za-z0-9_-]+)\/deliveries\/([a-f0-9]{64})\/(session|snapshots|confirm|status)$/.exec(url.pathname);
      if (write) {
        const [, repo, publicId, action] = write;
        identifier(repo, REPOSITORY);
        await authenticate(request, env, repo); // Before DO lookup, parsing or storage mutation.
        if ((action === 'status' && request.method !== 'GET') || (action !== 'status' && request.method !== 'POST')) fail('method_not_allowed', 405);
        if (action === 'status') return json(unwrap(await env.DELIVERIES.getByName(publicId).status(repo)));
        if (action === 'session') {
          const body = object(parse(await readBytes(request, 2048)));
          exact(body, ['deliveryKey', 'ownerId', 'mode', 'epoch']);
          if (typeof body.mode !== 'string' || !['acquire', 'resume', 'takeover'].includes(body.mode)) fail('invalid_envelope');
          const session: Session = { deliveryKey: identifier(body.deliveryKey, KEY), ownerId: identifier(body.ownerId), mode: body.mode as Session['mode'] };
          if (session.mode === 'acquire') { if (body.epoch !== undefined) fail('invalid_envelope'); }
          else session.epoch = integer(body.epoch);
          return json(unwrap(await env.DELIVERIES.getByName(publicId).session(repo, session)));
        }
        const commit = envelope(request);
        if (action === 'confirm') {
          const body = object(parse(await readBytes(request, 1024)));
          exact(body, ['sourceFingerprint']);
          return json(unwrap(await env.DELIVERIES.getByName(publicId).confirm(repo, commit, identifier(body.sourceFingerprint))));
        }
        const bytes = await readBytes(request, MAX_BYTES);
        if (await digest(bytes) !== commit.digest) fail('digest_mismatch', 422);
        const { sourceFingerprint } = projection(parse(bytes));
        return json(unwrap(await env.DELIVERIES.getByName(publicId).commit(repo, publicId, commit, sourceFingerprint, bytes)));
      }
      if (request.method === 'GET' && Object.hasOwn(assets,url.pathname)) {
        const asset=assets[url.pathname];
        return new Response(asset.body,{headers:{...headers,'Content-Type':asset.type,'Cache-Control':'public, max-age=31536000, immutable'}});
      }
      const page=/^\/d\/([a-f0-9]{64})$/.exec(url.pathname);
      if(page&&request.method==='GET'){
        unwrap(await env.DELIVERIES.getByName(page[1]).current());
        return new Response(shell,{headers:{...headers,'Content-Type':'text/html; charset=utf-8',
          'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; connect-src 'self'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}});
      }
      const read = /^\/d\/([a-f0-9]{64})\/(current|snapshots\/([a-f0-9]{64}))$/.exec(url.pathname);
      if (read && request.method === 'GET') {
        const [, publicId, action, digest] = read;
        const stub = env.DELIVERIES.getByName(publicId);
        if (action === 'current') return json(unwrap(await stub.current()));
        return new Response(unwrap(await stub.snapshot(digest)), { headers: { ...headers, 'Content-Type': 'application/json' } });
      }
      return json({ error: 'not_found' }, 404);
    } catch (error) { return errorResponse(error); }
  }
} satisfies ExportedHandler<Env>;
