import { SELF } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import { cases } from './fixtures.generated';
import { id, session, request, route, sql, upload, current, OTHER_TOKEN, TOKEN, fixture } from './helpers';
import { MAX_BYTES, RENDERER, digest, parse } from '../src/protocol';

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
  it.each([
    ['--output=/Users/operator/work/wait.json', false], ['--output=/tmp/wait.json', false],
    ['--output="/Users/operator/work/wait.json"', false], ["--output='/tmp/wait.json'", false],
    ['--output /tmp/wait.json', false], [String.raw`--output=C:\work\wait.json`, false],
    ['--output=C:/work/wait.json', false], [String.raw`--output=\\server\share\wait.json`, false],
    ['--output=~/work/wait.json', false], ['--output="~/work/wait.json"', false],
    ['--output=./wait.json', false], ['--output=../wait.json', false],
    [String.raw`--output=.\wait.json`, false], [String.raw`--output=..\wait.json`, false],
    ['--output=.orchestration/wait.json', false], ['--output=docs/wait.json', false],
    [String.raw`--output=docs\wait.json`, false], ['--output=file:///tmp/wait.json', false],
    ['--output=.agent-skills/current/wait.json', false],
    ['python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py', false],
    ['Read `/Users/operator/private/wait.json`', false], ['Inspect `docs/private-plan.md`', false],
    ['--output=`/Users/operator/private/wait.json`', false], ['`--output=/Users/operator/private/wait.json`', false],
    ['Read “/Users/operator/private/wait.json”', false],
    ['Read ‘.agent-skills/current/run.py’', false], ['Output:/Users/operator/private/wait.json', false],
    ['[/Users/operator/private/wait.json]', false], ['[docs/private-plan.md]', false],
    [String.raw`Output:[C:\work\wait.json]`, false], ['Read “~/work/wait.json”', false], ['Read `../wait.json`', false],
    ['{docs/private-plan.md}', false], ['<./wait.json>', false], ['Output;/tmp/wait.json', false], ['Output,/tmp/wait.json', false],
    ['Read **/Users/operator/private/wait.json**', false], ['Inspect **docs/private-plan.md**', false],
    ['Read _/Users/operator/private/wait.json_', false], ['Read ~~/Users/operator/private/wait.json~~', false],
    ['https:///Users/operator/private/wait.json', false], ['https://user:pass@example.com/docs/private.md', false],
    ['Read "https://example.com/public_docs/file"/Users/operator/private/wait.json', false],
    ['Read `https://example.com/public_docs/file`docs/private-plan.md', false],
    ['[public](https://example.com/public_docs/file)/Users/operator/private/wait.json', false],
    ['[public](https://example.com/public_docs/file)docs/private-plan.md', false],
    ['(https://example.com/public_docs/file)/Users/operator/private/wait.json', false],
    ['[public](https://example.com/public_docs/file)[docs/private-plan.md]', false],
    ['[public](https://example.com/public_docs/file){docs/private-plan.md}', false],
    ['[public](https://example.com/public_docs/file)**/Users/operator/private/wait.json**', false],
    ['[public](https://example.com/(nested/(docs/file)))/Users/operator/private/wait.json', false],
    ['[public](https://example.com/public_docs/file)_docs/private-plan.md_', false],
    ['[public](https://example.com/public_docs/file)~~docs/private-plan.md~~', false],
    ['[public](https://example.com/public_docs/file)', true], ['(https://example.com/public_docs/file)', true],
    ['https://example.com/(docs/file)', true], ['https://example.com/(outer/(docs/file))', true],
    ['[public](https://example.com/(outer/(docs/file)))', true], ['https://example.com/docs/file;more', true],
    ['https://example.com/docs/file,more', true], ['(https://example.com/docs/file)(https://example.com/docs/next)', true],
    ['[public](https://example.com/public_docs/file)ready', true], ['https://[::1]/docs/file', true],
    ['--output=https://example.com/public/wait.json', true], ['--timeout=1800.0', true],
    ['--output=summary.json', true], ['--mode=public', true], ['Review https://example.com/docs/wait.json', true],
    ['`https://example.com/docs/wait.json`', true], ['“https://example.com/docs/wait.json”', true],
    ['[https://example.com/docs/wait.json]', true], ['‘https://example.com/docs/wait.json’', true],
    ['<https://example.com/docs/wait.json>', true], ['Output:summary.json', true], ['[ready]', true], ['`--mode=public`', true],
  ])('independently scans option-bearing public prose: %s', async (text, valid) => {
    const publicId = id(); await session(publicId);
    const body = fixture(); body.document.state.meta.headline = text;
    expect((await upload(publicId, 1, body)).status).toBe(valid ? 200 : 400);
    expect((await current(publicId)).status).toBe(valid ? 200 : 404);
  });
  it.each([..."!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~“”‘’"])(
    'scans punctuation wrappers independently: %s', async (prefix) => {
      const local = ['/Users/operator/private/wait.json', 'docs/private-plan.md', String.raw`C:\work\wait.json`,
        '.agent-skills/current/run.py', '~/work/wait.json', './wait.json', '../wait.json'];
      const benign = ['https://public_docs.example.com/part_docs/file', 'https://example.com/public_docs/file',
        'HTTPS://example.com/public_docs/file', 'https://public-docs.example.com/.docs/file',
        '--mode=public', 'ready'];
      for (const text of [...local, ...benign]) {
        // Slash controls that form UNC/absolute paths remain private.
        const privateReference = local.includes(text) || prefix === '\\' || (prefix === '/' && text === 'ready');
        const publicId = id(); await session(publicId);
        const body = fixture(); body.document.state.meta.headline = `Read ${prefix}${prefix}${text}${prefix}${prefix}`;
        expect((await upload(publicId, 1, body)).status).toBe(privateReference ? 400 : 200);
        expect((await current(publicId)).status).toBe(privateReference ? 404 : 200);
      }
    });
  it.each([['(', ')'], ['[', ']'], ['{', '}']].flatMap(([opening, balancedClosing]) =>
    [')', ']', '}'].map(closing => ({ opening, closing, balanced: closing === balancedClosing }))))(
    'distinguishes URL wrapper types $opening $closing', async ({ opening, closing, balanced }) => {
      const publicId = id(); await session(publicId);
      const body = fixture();
      body.needs = { running: true, at: '2026-11-15T00:00:00Z', staleAfter: '2026-11-15T00:02:00Z',
        items: [{ holding: false, source: 'listener', clears: 'listener restored',
          text: `Read [public](https://example.com/${opening}nested${closing}/Users/operator/private/wait.json)` }] };
      expect((await upload(publicId, 1, body)).status).toBe(balanced ? 200 : 400);
      expect((await current(publicId)).status).toBe(balanced ? 200 : 404);
    });
  it.each([
    ['Verified /Users/operator/private/proof.json', false], ['Verified docs/private-proof.md', false],
    ['Verified --output=/tmp/private-proof.json', false], ['[Local reference omitted]', true],
    ['Verified all acceptance criteria without a code change.', true],
    ['Verified https://example.com/public_docs/proof', true],
  ])('validates settled no-change evidence independently: %s', async (evidence, valid) => {
    const publicId = id(); await session(publicId);
    const body = fixture();
    Object.assign(body.document.state, { planned: [{ subject: 'APP-2', title: 'No change needed', lane: null, stage: 'settled',
      noChange: { evidence, baseHead: 'b'.repeat(40), handedAt: '2026-11-15T00:00:00Z', settledAt: '2026-11-15T00:01:00Z' } }] });
    expect((await upload(publicId, 1, body)).status).toBe(valid ? 200 : 400);
    expect((await current(publicId)).status).toBe(valid ? 200 : 404);
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
    expect((await upload(publicId, 1, '[['.repeat(65))).status).toBe(400);
    expect((await upload(publicId, 1, '{"bad":')).status).toBe(400);
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
