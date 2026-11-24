import { SELF } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import legacyFixture from './fixtures/snapshot-v2.json';
import { cases } from './fixtures.generated';
import { id, session, request, route, sql, upload, current, OTHER_TOKEN, TOKEN, fixture } from './helpers';
import { MAX_BYTES, RENDERER, LEGACY_RENDERER, digest, parse } from '../src/protocol';

describe('ingestion boundary', () => {
  it('authenticates scope before any durable metadata and keeps public unknown reads empty', async () => {
    const publicId = id();
    for (const token of [null, 'wrong'.repeat(16), OTHER_TOKEN]) {
      const response = await request(route(publicId, 'session'), { method: 'POST', body: '{}' }, token);
      expect(response.status).toBe(401);
    }
    for (const path of [`/d/${publicId}/current`, `/d/${publicId}/snapshots/${'a'.repeat(64)}`, '/', '/d', '/api/v1/repos']) {
      expect((await request(path, {}, null)).status).toBe(404);
    }
    expect(await sql(publicId, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%'")).toEqual([]);
    expect((await request(route('%61'.repeat(64), 'session'), { method: 'POST', body: '{}' })).status).toBe(404);
    expect((await session(publicId)).status).toBe(200);
    expect((await session(publicId, {}, 'repo-b', OTHER_TOKEN)).status).toBe(404);
    expect((await session(publicId, { deliveryKey: 'OTHER' })).status).toBe(404);
    expect((await session(publicId, { ownerId: 'd'.repeat(64) })).status).toBe(409);
    expect((await request(route(publicId, 'status', 'repo-b'), {}, OTHER_TOKEN)).status).toBe(404);
    const rows = await sql<{repo: string}>(publicId, 'SELECT repo FROM delivery');
    expect(rows[0].repo).toBe('repo-a');
  });
  it.each(cases)('enforces shared schema and boundary fixture $file', async ({ body, valid }) => {
    const publicId = id();
    expect((await session(publicId)).status).toBe(200);
    const response = await upload(publicId, 1, body);
    expect(response.status).toBe(valid ? 200 : 400);
    expect((await current(publicId)).status).toBe(valid ? 200 : 404);
  });
  // A fixture body to change freely: the generated fixture types are its literal values.
  type Loose = Record<string, any>;
  // The hosted page shows what the local live view shows: prose arrives with the paths and commands the record holds.
  it.each([
    '--output=/Users/operator/work/wait.json', String.raw`--output=C:\work\wait.json`, '--output=~/work/wait.json',
    'python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py lead-wait',
    'Read `docs/plans/approved.md`', '[public](https://example.com/public_docs/file)/Users/operator/private/wait.json',
    'Verified file:///tmp/proof.json', 'https://example.com/docs/file;more',
  ])('accepts the local view\'s prose as it is written: %s', async (text) => {
    const publicId = id(); await session(publicId);
    const body = fixture() as Loose;
    body.document.state.meta.headline = text;
    body.document.journal.push({ id: 99, at: '2026-11-15T00:09:00Z', kind: 'decision.recorded', lane: null, subject: null,
      because: [1], data: { text, why: text } });
    body.needs = { running: true, at: '2026-11-15T00:00:00Z', staleAfter: '2026-11-15T00:02:00Z',
      items: [{ holding: false, source: 'listener', clears: 'listener restored', text }] };
    body.document.story = { chapters: [{ said: text }] };
    expect((await upload(publicId, 1, body)).status).toBe(200);
    expect((await current(publicId)).status).toBe(200);
  });
  it.each(['password', 'token', 'secret', 'apiKey', 'api_key', 'credential', 'Authorization', 'cookie'])(
    'refuses a credential-named field at any depth: %s', async (key) => {
      for (const place of ['journal fact', 'state', 'run', 'needs']) {
        const publicId = id(); await session(publicId);
        const body = fixture() as Loose;
        body.needs = { running: true, at: '2026-11-15T00:00:00Z', items: [{ holding: false, source: 's', clears: 'c', text: 't' }] };
        const holder = place === 'journal fact' ? (body.document.journal[0].data ??= {})
          : place === 'state' ? body.document.state.meta
          : place === 'run' ? Object.values(body.runs as Record<string, Record<string, unknown>>)[0]
          : body.needs.items[0];
        (holder as Record<string, unknown>)[key] = 'a value';
        expect((await upload(publicId, 1, body)).status, place).toBe(400);
        expect((await current(publicId)).status, place).toBe(404);
      }
      // A field whose name only contains the word is ordinary.
      const publicId = id(); await session(publicId);
      const body = fixture() as Loose; (body.document.state.meta as Record<string, unknown>)[`${key}Budget`] = 3;
      expect((await upload(publicId, 1, body)).status).toBe(200);
    });
  it('serves the deployed entrypoint through local Worker, DO and R2 bindings', async () => {
    const publicId=id();
    const auth={'Authorization':`Bearer ${TOKEN}`,'Content-Type':'application/json'};
    const acquired=await SELF.fetch(`https://service.test${route(publicId,'session')}`,{method:'POST',headers:auth,body:JSON.stringify({mode:'acquire',deliveryKey:'APP-1',ownerId:'c'.repeat(64)})});
    expect(acquired.status).toBe(200);
    const body=JSON.stringify(fixture());const hash=await digest(new TextEncoder().encode(body));
    const accepted=await SELF.fetch(`https://service.test${route(publicId,'snapshots')}`,{method:'POST',headers:{...auth,'X-Writer-Epoch':'1','X-Snapshot-Sequence':'1','X-Snapshot-Digest':hash,'X-Renderer-Version':RENDERER},body});
    expect(accepted.status).toBe(200);
    const response=await SELF.fetch(`https://service.test/d/${publicId}/snapshots/${hash}`);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe(body);
  });
  it('hashes exact bytes and accepts data only with supported renderer and bounded headers', async () => {
    const publicId = id(); await session(publicId);
    const headers = { 'Content-Type': 'application/json', 'X-Renderer-Version': RENDERER,
      'X-Writer-Epoch': '1', 'X-Snapshot-Sequence': '1', 'X-Snapshot-Digest': 'a'.repeat(64) };
    expect((await request(route(publicId, 'snapshots'), { method: 'POST', headers, body: '{}' })).status).toBe(422);
    expect((await request(route(publicId, 'snapshots'), { method: 'POST', headers: {...headers, 'Content-Type': 'text/html'}, body: '<html>' })).status).toBe(415);
    expect((await request(route(publicId, 'snapshots'), { method: 'POST', headers: {...headers, 'X-Renderer-Version': 'future'}, body: '{}' })).status).toBe(422);
    // The renderer before this one is refused by name too, with a body that is otherwise accepted: the service and the
    // publisher move together.
    const valid = JSON.stringify(fixture());
    const exact = { ...headers, 'X-Snapshot-Digest': await digest(new TextEncoder().encode(valid)) };
    expect(RENDERER).toBe('delivery-line-map/3');
    const unknown = await request(route(publicId, 'snapshots'), { method: 'POST', headers: {...exact, 'X-Renderer-Version': 'delivery-line-map/1'}, body: valid });
    expect(unknown.status).toBe(422);
    expect(await unknown.json()).toEqual({ error: 'unsupported_renderer' });
    const mismatched = await request(route(publicId, 'snapshots'), { method: 'POST', headers: {...exact, 'X-Renderer-Version': LEGACY_RENDERER}, body: valid });
    expect(mismatched.status).toBe(400);
    // An older body is refused too, under the current renderer.
    const olderBody = JSON.stringify({ ...fixture(), schemaVersion: 'hosted-delivery-snapshot/2' });
    expect((await upload(publicId, 1, olderBody)).status).toBe(400);
    expect((await current(publicId)).status).toBe(404);
    expect((await upload(publicId, 1, '[['.repeat(65))).status).toBe(400);
    expect((await upload(publicId, 1, '{"bad":')).status).toBe(400);
    expect((await current(publicId)).status).toBe(404);
  });
  it('continues a v2 delivery across retries and confirmations while other deliveries can use v3', async () => {
    const publicId = id();
    await session(publicId);
    const body = JSON.stringify(legacyFixture);
    const headers = {
      'X-Renderer-Version': LEGACY_RENDERER, 'X-Writer-Epoch': '1', 'X-Snapshot-Sequence': '1',
      'X-Snapshot-Digest': await digest(new TextEncoder().encode(body)),
    };
    const snapshot = () => request(route(publicId, 'snapshots'), {method: 'POST', headers, body});
    const confirm = (version: string) => request(route(publicId, 'confirm'), {method: 'POST',
      headers: {...headers, 'X-Renderer-Version': version}, body: JSON.stringify({sourceFingerprint: legacyFixture.sourceFingerprint})});
    const first = await snapshot();
    expect(first.status).toBe(200);
    const receipt = await first.json();
    expect(receipt).toMatchObject({rendererVersion: LEGACY_RENDERER, sequence: 1});
    expect(await (await snapshot()).json()).toEqual(receipt);
    expect((await confirm(RENDERER)).status).toBe(409);
    expect((await confirm(LEGACY_RENDERER)).status).toBe(200);
    const resumed = await session(publicId, {mode: 'resume', epoch: 1});
    expect(await resumed.json()).toMatchObject({receipt});
    headers['X-Snapshot-Sequence'] = '2';
    expect(await (await snapshot()).json()).toMatchObject({rendererVersion: LEGACY_RENDERER, sequence: 2});
    expect((await upload(publicId, 3)).status).toBe(422);
    expect(await (await current(publicId)).json()).toMatchObject({rendererVersion: LEGACY_RENDERER, sequence: 2});
    const newer = id();
    await session(newer);
    expect(await (await upload(newer)).json()).toMatchObject({rendererVersion: RENDERER, sequence: 1});
  });
  it('retains the v2 local-reference privacy rule', async () => {
    const publicId = id();
    await session(publicId);
    const body = JSON.stringify(legacyFixture).replace(legacyFixture.document.state.meta.headline, 'Notes under docs/private');
    expect(body).toContain('docs/private');
    const response = await request(route(publicId, 'snapshots'), {method: 'POST', body, headers: {
      'X-Renderer-Version': LEGACY_RENDERER, 'X-Writer-Epoch': '1', 'X-Snapshot-Sequence': '1',
      'X-Snapshot-Digest': await digest(new TextEncoder().encode(body)),
    }});
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({error: 'invalid_projection'});
    expect((await current(publicId)).status).toBe(404);
  });
  it('counts streamed bytes despite absent or dishonest content length and cancels oversized input', async () => {
    const publicId = id(); await session(publicId);
    for (const length of [undefined, '2', String(MAX_BYTES + 1)]) {
      let cancelled = false;
      const stream = new ReadableStream<Uint8Array>({
        pull(controller) { controller.enqueue(new Uint8Array(1024 * 1024)); },
        cancel() { cancelled = true; }
      });
      const headers: Record<string,string> = { 'Content-Type':'application/json', 'X-Renderer-Version':RENDERER, 'X-Writer-Epoch':'1', 'X-Snapshot-Sequence':'1', 'X-Snapshot-Digest':'a'.repeat(64) };
      if (length) headers['Content-Length'] = length;
      const response = await request(route(publicId,'snapshots'), { method:'POST', headers, body:stream });
      expect(response.status).toBe(413);
      if (length !== String(MAX_BYTES + 1)) expect(cancelled).toBe(true);
      await stream.cancel().catch(() => {});
    }
    expect((await current(publicId)).status).toBe(404);
  });
  it('returns safe fixed errors and exposes only a committed object, never bucket paths', async () => {
    const publicId = id(); await session(publicId);
    const receipt = await (await upload(publicId)).json() as {digest:string};
    const response = await request(`/d/${publicId}/snapshots/${receipt.digest}`, {}, null);
    expect(response.status).toBe(200);
    expect(await digest(new Uint8Array(await response.arrayBuffer()))).toBe(receipt.digest);
    for (const path of [`/d/${publicId}/snapshots/${'0'.repeat(64)}`, `/d/${publicId}/history`, `/d/${publicId}/1/1/${receipt.digest}`]) expect((await request(path, {}, null)).status).toBe(404);
    expect((await request(`/d/${publicId}/current`, {method:'POST'}, null)).status).toBe(404);
    expect(response.headers.get('referrer-policy')).toBe('no-referrer');
    expect(response.headers.get('x-robots-tag')).toContain('noindex');
  });
});

describe('independent transport contract guards', () => {
  it('rejects snapshot and confirmation auth bypasses without mutating a committed delivery', async () => {
    const publicId=id(); await session(publicId); const accepted=await upload(publicId); expect(accepted.status).toBe(200);
    const before=await (await current(publicId)).json();
    const bytes=JSON.stringify(fixture()); const hash=await digest(new TextEncoder().encode(bytes));
    const headers={'X-Writer-Epoch':'1','X-Snapshot-Sequence':'2','X-Snapshot-Digest':hash,'X-Renderer-Version':RENDERER};
    for (const token of [null,'wrong'.repeat(16),OTHER_TOKEN]) {
      expect((await request(route(publicId,'snapshots'),{method:'POST',headers,body:bytes},token)).status).toBe(401);
      expect((await request(route(publicId,'confirm'),{method:'POST',headers,body:JSON.stringify({sourceFingerprint:'1'.repeat(64)})},token)).status).toBe(401);
      expect((await request(route(publicId,'status'),{},token)).status).toBe(401);
    }
    for(const action of ['snapshots','confirm']){
      const body=action==='snapshots'?bytes:JSON.stringify({sourceFingerprint:'1'.repeat(64)});
      expect((await request(route(publicId,action,'repo-b'),{method:'POST',headers,body},OTHER_TOKEN)).status).toBe(404);
    }
    expect((await request(route(publicId,'status','repo-b'),{},OTHER_TOKEN)).status).toBe(404);
    expect((await request(route(publicId,'status'))).status).toBe(200);
    expect(await (await current(publicId)).json()).toEqual({...before as object,serverNow:expect.any(Number)});
    expect((await sql<{n:number}>(publicId,'SELECT count(*) AS n FROM receipts'))[0].n).toBe(1);
  });
  it('accepts the digest of noncanonical bytes and rejects a reserialized digest for those bytes', async () => {
    const publicId=id(); await session(publicId);
    const value=fixture(); const body=JSON.stringify(value,null,2)+'\n';
    const canonicalHash=await digest(new TextEncoder().encode(JSON.stringify(value)));
    const headers={'X-Writer-Epoch':'1','X-Snapshot-Sequence':'1','X-Snapshot-Digest':canonicalHash,'X-Renderer-Version':RENDERER};
    expect((await request(route(publicId,'snapshots'),{method:'POST',headers,body})).status).toBe(422);
    const accepted=await upload(publicId,1,body);expect(accepted.status).toBe(200);
    const exactHash=await digest(new TextEncoder().encode(body));
    const stored=await request(`/d/${publicId}/snapshots/${exactHash}`,{},null);
    expect(stored.status).toBe(200);expect(await stored.text()).toBe(body);
    expect((await accepted.json() as {digest:string}).digest).toBe(exactHash);
  });
  it('rejects credential-shaped identifier map keys even when the structural schema permits them', async () => {
    const publicId=id();await session(publicId);
    const value=fixture();
    Object.assign(value.links.issues,{ordinary:'https://linear.app/v26-technologies/issue/APP-1'});
    expect((await upload(publicId,1,value)).status).toBe(200);
    Object.assign(value.links.issues,{token:'https://linear.app/v26-technologies/issue/APP-1'});
    expect((await upload(publicId,2,value)).status).toBe(400);
    expect((await current(publicId)).status).toBe(200);
  });
  it('takes a file link that opens a file in the reviewed tree at a line, and no other', async () => {
    const publicId=id();await session(publicId);
    const sha='a'.repeat(40), blob=`https://github.com/example/app/blob/${sha}`;
    const value=fixture() as {links:Record<string,Record<string,unknown>>};
    value.links.files={[`${sha} src/app/main.py:4-6`]:`${blob}/src/app/main.py#L4`,[`${sha} ./docs/plan.md:2`]:`${blob}/docs/plan.md#L2`};
    expect((await upload(publicId,1,value)).status).toBe(200);
    const refused:[string,string][]=[
      [`${sha} src/app/main.py:4`,`http://github.com/example/app/blob/${sha}/src/app/main.py#L4`],
      [`${sha} src/app/main.py:4`,'javascript:alert(1)'],
      [`${sha} src/app/main.py:4`,`https://user:pass@github.com/example/app/blob/${sha}/src/app/main.py#L4`],
      [`${sha} src/app/main.py:4`,`${blob}/src/app/main.py?plain=1#L4`],
      [`${sha} ../secrets.env:1`,`${blob}/secrets.env#L1`],
      [`${sha} src/../../etc/passwd:1`,`${blob}/etc/passwd#L1`],
      [`${sha} /etc/passwd:1`,`${blob}/etc/passwd#L1`],
    ];
    let sequence=2;
    for(const [key,href] of refused){
      value.links.files={[key]:href};
      expect((await upload(publicId,sequence++,value)).status,`${key} -> ${href}`).toBe(400);
    }
    // The fragment a file link keeps never carries over to an issue link.
    value.links.files={};
    value.links.issues={'APP-1':'https://example.com/issue/APP-1#x'};
    expect((await upload(publicId,sequence++,value)).status).toBe(400);
    expect(await (await current(publicId)).json()).toMatchObject({sequence:1});
  });
  it('enforces exactly sixteen MiB on finite chunked input without a length hint', async () => {
    const publicId=id(); await session(publicId);
    const limit=16*1024*1024;expect(MAX_BYTES).toBe(limit);
    let emitted=0;
    const stream=new ReadableStream<Uint8Array>({pull(controller){
      if(emitted===limit+1){controller.close();return;}
      const size=Math.min(1024*1024,limit+1-emitted);emitted+=size;controller.enqueue(new Uint8Array(size));
    }});
    const headers={'X-Writer-Epoch':'1','X-Snapshot-Sequence':'1','X-Snapshot-Digest':'a'.repeat(64),'X-Renderer-Version':RENDERER};
    expect((await request(route(publicId,'snapshots'),{method:'POST',headers,body:stream})).status).toBe(413);
    expect((await current(publicId)).status).toBe(404);
    const exact=JSON.stringify(fixture()).padEnd(limit,' ');
    expect((await upload(publicId,1,exact)).status).toBe(200);
  });
  it('pins the parser depth with syntactically valid JSON at and beyond the boundary',()=>{
    const encode=(depth:number)=>new TextEncoder().encode('['.repeat(depth)+'0'+']'.repeat(depth));
    expect(parse(encode(64))).toBeInstanceOf(Array);
    expect(()=>parse(encode(65))).toThrow('invalid_json');
  });
  it('keeps successful and error capability responses private and non-executable',async()=>{
    const publicId=id();await session(publicId);const accepted=await upload(publicId);expect(accepted.status).toBe(200);
    const {digest:hash}=await accepted.json() as {digest:string};
    const responses=[await current(publicId),await request(`/d/${publicId}/snapshots/${hash}`,{},null),
      await request('/unknown',{},null),await request(route(publicId,'status'),{},null),
      await request(route(publicId,'session'),{method:'POST',body:'{}'})];
    expect(responses.map(row=>row.status)).toEqual([200,200,404,401,400]);
    for(const response of responses){
      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
      expect(response.headers.get('X-Robots-Tag')).toBe('noindex, nofollow, noarchive');
      expect(response.headers.get('X-Content-Type-Options')).toBe('nosniff');
      expect(response.headers.get('Content-Security-Policy')).toBe("default-src 'none'; frame-ancestors 'none'; base-uri 'none'");
      await response.arrayBuffer();
    }
  });
});

describe('shared numeric projection boundary', () => {
  it('rejects unsafe numbers so journal identities cannot lose precision',async()=>{
    const publicId=id();await session(publicId);
    const body=fixture();body.document.journal[0].id=Number.MAX_SAFE_INTEGER;
    const accepted=await upload(publicId,1,body);expect(accepted.status).toBe(200);
    const {digest:hash}=await accepted.json() as {digest:string};
    const snapshot=await request(`/d/${publicId}/snapshots/${hash}`,{},null);
    expect(snapshot.status).toBe(200);
    const published=await snapshot.json() as {document:{journal:{id:number}[]}};
    expect(published.document.journal[0].id).toBe(Number.MAX_SAFE_INTEGER);
    body.document.journal[0].id=Number.MAX_SAFE_INTEGER+1;
    expect((await upload(publicId,2,body)).status).toBe(400);
    expect(await (await current(publicId)).json()).toMatchObject({sequence:1,digest:hash});
  });
});
