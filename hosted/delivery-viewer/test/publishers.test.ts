import { env } from 'cloudflare:workers';
import { describe, expect, it, vi } from 'vitest';
import { digest } from '../src/protocol';
import { publisherKey, REVOKED } from '../src/publisher-record.mjs';
import { id, session, TOKEN, OTHER_TOKEN, request } from './helpers';

const newToken = 'new-repository-token-'.padEnd(64, 'x');
const hash = (value: string) => digest(new TextEncoder().encode(value));
const open = (repo: string, token: string) => session(id(), {}, repo, token);

describe('per-repository publisher records', () => {
  it('provisions an independent publisher and keeps both legacy publishers active', async () => {
    const repo = 'new-' + id().slice(0, 24);
    await env.SNAPSHOTS.put(publisherKey(repo), await hash(newToken));
    expect((await open(repo, newToken)).status).toBe(200);
    expect((await open(repo, TOKEN)).status).toBe(401);
    expect((await open('repo-a', newToken)).status).toBe(401);
    expect((await open('repo-a', TOKEN)).status).toBe(200);
    expect((await open('repo-b', OTHER_TOKEN)).status).toBe(200);
    expect((await request('/' + publisherKey(repo), {}, null)).status).toBe(404);
  });

  it('applies rotation and revocation immediately, including to a legacy publisher', async () => {
    const key = publisherKey('repo-a');
    await env.SNAPSHOTS.put(key, await hash(newToken));
    expect((await open('repo-a', TOKEN)).status).toBe(401);
    expect((await open('repo-a', newToken)).status).toBe(200);
    await env.SNAPSHOTS.put(key, REVOKED);
    expect((await open('repo-a', newToken)).status).toBe(401);
    expect((await open('repo-a', TOKEN)).status).toBe(401);
    expect((await open('repo-b', OTHER_TOKEN)).status).toBe(200);
  });

  it('fails closed on corrupt or unreadable records, even for a legacy token', async () => {
    const key = publisherKey('repo-a');
    for (const bad of ['', 'x'.repeat(64)]) {
      await env.SNAPSHOTS.put(key, bad);
      expect((await open('repo-a', TOKEN)).status).toBe(503);
    }
    const unavailable = vi.spyOn(env.SNAPSHOTS, 'get').mockRejectedValueOnce(new Error('unavailable'));
    try {
      expect((await open('repo-b', OTHER_TOKEN)).status).toBe(503);
    } finally {
      unavailable.mockRestore();
    }
  });
});
