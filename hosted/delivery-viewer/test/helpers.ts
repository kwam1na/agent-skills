import { env } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { vi } from 'vitest';
import worker from '../src/worker';
import { digest, RENDERER } from '../src/protocol';
import { cases } from './fixtures.generated';
export const TOKEN = 'publisher-test-token-'.padEnd(64, 'x');
export const OTHER_TOKEN = 'other-repository-token-'.padEnd(64, 'x');
export const owner = 'c'.repeat(64);
export const fixture = () => structuredClone(cases.find(row => row.valid)!.body);
export const id = () => crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
export const stub = (publicId: string) => env.DELIVERIES.getByName(publicId);
export async function request(path: string, options: RequestInit = {}, token: string | null = TOKEN) {
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (options.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  return worker.fetch(new Request(`https://service.test${path}`, { ...options, headers }), env);
}
export function route(publicId: string, action: string, repo = 'repo-a') { return `/api/v1/repos/${repo}/deliveries/${publicId}/${action}`; }
export async function session(publicId: string, changes: Record<string, unknown> = {}, repo = 'repo-a', token = TOKEN) {
  return request(route(publicId, 'session', repo), { method: 'POST', body: JSON.stringify({ mode: 'acquire', deliveryKey: 'APP-1', ownerId: owner, ...changes }) }, token);
}
export async function upload(publicId: string, sequence = 1, body: unknown = fixture(), epoch = 1) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  const hash = await digest(new TextEncoder().encode(text));
  return request(route(publicId, 'snapshots'), { method: 'POST', body: text,
    headers: { 'X-Renderer-Version': RENDERER, 'X-Writer-Epoch': String(epoch), 'X-Snapshot-Sequence': String(sequence), 'X-Snapshot-Digest': hash } });
}
export async function sql<T>(publicId: string, query: string, ...values: (string | number)[]) {
  return runInDurableObject(stub(publicId), (_instance, state) => state.storage.sql.exec(query, ...values).toArray() as T[]);
}
export async function expire(publicId: string) { await sql(publicId, 'UPDATE delivery SET leaseUntil=0 WHERE singleton=1'); }
export async function current(publicId: string) { return request(`/d/${publicId}/current`, {}, null); }
export function barrier() {
  let started!: () => void, release!: () => void;
  const entered = new Promise<void>(resolve => { started = resolve; });
  const blocked = new Promise<void>(resolve => { release = resolve; });
  return { entered, release, pause: async () => { started(); await blocked; } };
}
export async function pauseR2(publicId: string) {
  const gate = barrier();
  const restore = await runInDurableObject(stub(publicId), instance => {
    const bucket = Reflect.get(instance, 'env').SNAPSHOTS as R2Bucket;
    const original = bucket.put.bind(bucket);
    const spy = vi.spyOn(bucket, 'put').mockImplementationOnce(async (...args: Parameters<R2Bucket['put']>) => { await gate.pause(); return original(...args); });
    return () => spy.mockRestore();
  });
  return { ...gate, restore };
}
