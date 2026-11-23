// The Cloudflare adapter: the neutral service over Durable Object deliveries, R2 snapshots and R2 publisher records.
import { publisherKey, REVOKED } from '../publisher-record.mjs';
import { fail } from '../protocol';
import { createService } from '../core/service';
import { publisherMap } from '../core/publishers';
import type { DeliveryEndpoint } from '../core/deliveries';
import type { Publishers } from '../core/ports';
import { DeliveryObject } from './delivery-object';
export { DeliveryObject };

/**
 * A repository's digest from its R2 record under `_publishers/v1/<repositoryId>`, read on every check: a stored
 * digest activates it and `revoked` disables it. With no record, the `PUBLISHERS` secret's map decides.
 * A corrupt record or a failed read denies access.
 */
function publishers(env: Env): Publishers {
  return {
    async expected(repo) {
      const record = await env.SNAPSHOTS.get(publisherKey(repo));
      if (record === null) return publisherMap(env.PUBLISHERS)(repo);
      if (record.size !== 64 && record.size !== REVOKED.length) fail('temporarily_unavailable', 503);
      const expected = await record.text();
      if (expected === REVOKED) return null;
      if (!/^[a-f0-9]{64}$/.test(expected)) fail('temporarily_unavailable', 503);
      return expected;
    },
  };
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return createService({
      deliveries: publicId => env.DELIVERIES.getByName(publicId) as unknown as DeliveryEndpoint,
      publishers: publishers(env),
    }).fetch(request);
  },
} satisfies ExportedHandler<Env>;
