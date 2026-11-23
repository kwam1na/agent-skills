import { evictDurableObject, runInDurableObject } from 'cloudflare:test';
import { describe, it, expect, vi } from 'vitest';
import { id, session, upload, current, sql, expire, fixture, pauseR2, stub, request, route, owner } from './helpers';
import { RENDERER, type Receipt } from '../src/protocol';

async function receipt(response: Response): Promise<Receipt> { expect(response.status).toBe(200); return response.json() as Promise<Receipt>; }
async function confirm(publicId: string, value: Receipt, fingerprint = value.sourceFingerprint, extra: Record<string,unknown> = {}) {
  return request(route(publicId, 'confirm'), {method:'POST', headers: {'X-Renderer-Version':RENDERER, 'X-Writer-Epoch':String(value.epoch), 'X-Snapshot-Sequence':String(value.sequence), 'X-Snapshot-Digest':value.digest}, body:JSON.stringify({sourceFingerprint:fingerprint,...extra}) });
}

// Fencing after the blob write, lease expiry during it, rollback at the commit boundary and one receipt for exact
// retries are the core storage contract's rows: test/contract/storage.ts, run in test/storage-contract.test.ts.
describe('persistent commit ordering', () => {
  it('commits N+1 before N, persists lost receipts and rejects same-sequence different bytes', async () => {
    const publicId=id(); await session(publicId);
    const pause = await pauseR2(publicId);
    const delayed = upload(publicId, 1);
    const nextBody=fixture(); nextBody.sourceFingerprint='4'.repeat(64);
    try {
      await pause.entered;
      const next = await receipt(await upload(publicId, 2, nextBody));
      pause.release();
      expect((await delayed).status).toBe(409);
      expect(await receipt(await upload(publicId, 2, nextBody))).toEqual(next);
      expect((await upload(publicId, 2)).status).toBe(409);
      expect((await (await current(publicId)).json() as Receipt).sequence).toBe(2);
      const count = await sql<{n:number}>(publicId,'SELECT count(*) AS n FROM receipts');
      expect(count[0].n).toBe(1);
      pause.restore();
      expect(await receipt(await upload(publicId, 2, nextBody))).toEqual(next);
      const status=await (await request(route(publicId,'status'))).json() as {epoch:number;sequence:number;receipt:Receipt};
      expect(status).toMatchObject({epoch:1,sequence:2,receipt:next});
    } finally { pause.release(); await delayed; pause.restore(); }
  });
  it('persists session, binding and lost acknowledgements through actual eviction', async () => {
    const publicId=id(); await session(publicId);
    const accepted=await receipt(await upload(publicId));
    await evictDurableObject(stub(publicId));
    expect(await receipt(await upload(publicId))).toEqual(accepted);
    expect((await session(publicId,{mode:'resume',epoch:1})).status).toBe(200);
    expect((await session(publicId,{deliveryKey:'DIFFERENT'})).status).toBe(404);
    expect((await (await current(publicId)).json() as Receipt).digest).toBe(accepted.digest);
  });
  it('retains the prior snapshot on R2 failure and never makes a staged digest public', async () => {
    const publicId=id(); await session(publicId);
    const initial=await receipt(await upload(publicId));
    const restore=await runInDurableObject(stub(publicId), instance=>{
      const bucket=Reflect.get(instance,'env').SNAPSHOTS as R2Bucket;
      const spy=vi.spyOn(bucket,'put').mockRejectedValueOnce(new Error('private-bucket-canary'));
      return ()=>spy.mockRestore();
    });
    try {
      const next=fixture();next.sourceFingerprint='6'.repeat(64);
      const response=await upload(publicId,2,next);
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({error:'temporarily_unavailable'});
      expect((await (await current(publicId)).json() as Receipt).digest).toBe(initial.digest);
      expect((await request(`/d/${publicId}/snapshots/${initial.digest}`,{},null)).status).toBe(200);
    } finally { restore(); }
  });
  it('renews freshness only through a matching source confirmation and resumes expired sessions explicitly', async () => {
    const publicId=id(); await session(publicId);
    const initial=await receipt(await upload(publicId));
    await sql(publicId,'UPDATE delivery SET confirmedAt=0 WHERE singleton=1');
    expect((await (await current(publicId)).json() as {stale:boolean}).stale).toBe(true);
    expect((await session(publicId,{mode:'resume',epoch:1})).status).toBe(200);
    expect((await (await current(publicId)).json() as {sourceConfirmedAt:number}).sourceConfirmedAt).toBe(0);
    expect((await confirm(publicId,initial,'7'.repeat(64))).status).toBe(409);
    expect((await confirm(publicId,initial)).status).toBe(200);
    expect((await (await current(publicId)).json() as {stale:boolean}).stale).toBe(false);
    await expire(publicId);
    expect((await upload(publicId,2)).status).toBe(409);
    expect((await confirm(publicId,initial)).status).toBe(409);
    expect((await session(publicId,{mode:'takeover',epoch:1,ownerId:'e'.repeat(64)})).status).toBe(200);
    expect((await session(publicId,{mode:'resume',epoch:1})).status).toBe(409);
    expect((await session(publicId,{mode:'resume',epoch:2,ownerId:owner})).status).toBe(409);
    expect((await upload(publicId,1,fixture(),2)).status).toBe(200);
    // Equal local names in separate authorized repositories get independent capabilities.
    const other=id();
    const {OTHER_TOKEN}=await import('./helpers');
    expect((await session(other,{},'repo-b',OTHER_TOKEN)).status).toBe(200);
    expect(other).not.toBe(publicId);
  });
});

describe('session and freshness contract guards', () => {
  it('rejects coerced session modes before they can replace an expired writer', async () => {
    const publicId=id();await session(publicId);await expire(publicId);
    for(const mode of [['resume'],['takeover'],null,1,{}]){
      expect((await session(publicId,{mode,epoch:1,ownerId:'f'.repeat(64)})).status).toBe(400);
      expect((await sql<{epoch:number;ownerId:string}>(publicId,'SELECT epoch,ownerId FROM delivery'))[0]).toEqual({epoch:1,ownerId:owner});
    }
  });
  it('refuses takeover of an active lease and pins the 180s lease and 120s stale API contracts', async () => {
    const publicId=id();await session(publicId);
    const accepted=await receipt(await upload(publicId));
    const before=await sql(publicId,'SELECT * FROM delivery');
    const refused=await session(publicId,{mode:'takeover',epoch:1,ownerId:'f'.repeat(64)});
    expect(refused.status).toBe(409);expect(await refused.json()).toEqual({error:'lease_held'});
    expect(await sql(publicId,'SELECT * FROM delivery')).toEqual(before);
    const status=await (await request(route(publicId,'status'))).json() as {epoch:number;ownerId:string;leaseUntil:number};
    expect(status.epoch).toBe(1);expect(status.ownerId).toBe(owner);
    expect(status.leaseUntil-accepted.sourceConfirmedAt).toBe(180000);
    const meta=await (await current(publicId)).json() as {staleAfterMs:number};
    expect(meta.staleAfterMs).toBe(120000);
    expect((await upload(publicId,2)).status).toBe(200);
  });
});


describe('durable confirmation and strict envelopes',()=>{
  it('never renews metadata for exact or historical receipt retries',async()=>{
    const publicId=id();await session(publicId);const first=await receipt(await upload(publicId));
    await sql(publicId,'UPDATE delivery SET confirmedAt=1000,leaseUntil=? WHERE singleton=1',Date.now()+111000);
    const before=await sql(publicId,'SELECT * FROM delivery');
    expect(await receipt(await upload(publicId))).toEqual(first);
    expect(await sql(publicId,'SELECT * FROM delivery')).toEqual(before);
    const next=fixture();next.sourceFingerprint='8'.repeat(64);await receipt(await upload(publicId,2,next));
    await sql(publicId,'UPDATE delivery SET confirmedAt=2000,leaseUntil=? WHERE singleton=1',Date.now()+112000);
    const newer=await sql(publicId,'SELECT * FROM delivery');
    expect(await receipt(await upload(publicId))).toEqual(first);
    expect(await sql(publicId,'SELECT * FROM delivery')).toEqual(newer);
  });
  it('uses the precise lease and stale windows with controlled source-confirmation time',async()=>{
    const clock=vi.spyOn(Date,'now').mockReturnValue(1000000);
    try{
      const publicId=id();const acquired=await (await session(publicId)).json() as {leaseUntil:number};
      expect(acquired.leaseUntil).toBe(1180000);
      const accepted=await receipt(await upload(publicId));
      clock.mockReturnValue(1119999);
      expect((await (await current(publicId)).json() as {stale:boolean}).stale).toBe(false);
      clock.mockReturnValue(1120000);
      expect((await (await current(publicId)).json() as {stale:boolean}).stale).toBe(true);
      const confirmed=await confirm(publicId,accepted);expect(confirmed.status).toBe(200);
      expect(await confirmed.json()).toMatchObject({sourceConfirmedAt:1120000,leaseUntil:1300000});
      expect((await (await current(publicId)).json() as {stale:boolean}).stale).toBe(false);
    }finally{clock.mockRestore();}
  });
  it('rejects unknown session and confirmation keys before changing state',async()=>{
    const publicId=id();const bad=await session(publicId,{extra:true});
    expect(bad.status).toBe(400);expect(await bad.json()).toEqual({error:'invalid_envelope'});
    expect((await current(publicId)).status).toBe(404);
    expect((await session(publicId)).status).toBe(200);
    const accepted=await receipt(await upload(publicId));const before=await sql(publicId,'SELECT * FROM delivery');
    const refused=await confirm(publicId,accepted,accepted.sourceFingerprint,{extra:true});
    expect(refused.status).toBe(400);expect(await refused.json()).toEqual({error:'invalid_envelope'});
    expect(await sql(publicId,'SELECT * FROM delivery')).toEqual(before);
    expect((await confirm(publicId,accepted)).status).toBe(200);
  });
});
