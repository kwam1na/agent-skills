import { fail, object } from '../protocol';

const DIGEST = /^[a-f0-9]{64}$/;

/**
 * Reads a `PUBLISHERS` map: a JSON object of repository IDs to token digests. Every repository in it is honoured.
 * A map that is not a JSON object is unavailable (503); a repository it lacks, or holds no digest for, is unknown.
 */
export function publisherMap(text: string): (repository: string) => string | null {
  let map: Record<string, unknown>;
  try { map = object(JSON.parse(text)); } catch { return () => fail('temporarily_unavailable', 503); }
  return repository => {
    const value = Object.hasOwn(map, repository) ? map[repository] : undefined;
    return typeof value === 'string' && DIGEST.test(value) ? value : null;
  };
}
