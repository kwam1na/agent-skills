---
title: The hosted service refuses a delivery's cuts after a release changes the snapshot
date: 2026-11-26
category: integration-issues
module: delivery-views
problem_type: integration_issue
severity: medium
tags: [hosted, snapshot, deploy, invalid_projection, release]
symptoms: "A delivery's hosted page stays 404 although the publish token is set; the record's hosted-status.json reads state failed with code invalid_projection"
root_cause: "A release added a member to the hosted snapshot schema, and the deployed service predated it, so it refused every cut that carried the new member"
resolution_type: environment_setup
---

# The hosted service refuses a delivery's cuts after a release changes the snapshot

## Problem

V26-3993 published nothing to its hosted page. linear-v4.5.0 had added a
`repository` member to `hosted-snapshot.schema.json`, and the Worker serving
`deliveries.v26technologies.com` had been deployed from an earlier main. The
service refused each cut as `invalid_projection`, and the page stayed 404.

## Symptoms

- `hosted: failed <url>` at `lead-init`, and the hosted page answers
  `{"error":"not_found"}`.
- `.orchestration/state/hosted-status.json` holds
  `{"code": "invalid_projection", "state": "failed"}`. The code is the service's
  answer, not a local refusal; a token problem reads `unauthorized` instead.

## What Didn't Work

- Exporting the publish token into the shell: a running supervisor can't pick up
  a later export.
- `hosted-retry` alone: the service still refused the cut.
- Restarting the supervisor with the token: publishing started, and was refused
  for the same reason.

## Solution

1. Compare the deployed Worker with the snapshot change:
   `npx wrangler deployments list` in `hosted/delivery-viewer` names the main
   commit each version came from.
2. Redeploy the service from main, following `docs/hosted-deliveries.md`'s
   reviewed production procedure: `npm run typecheck`, `npm test`,
   `npm run build`, `npx wrangler whoami`, `npx wrangler deploy`. Keep the
   previous version id for a rollback.
3. Retry the delivery's publication with `hosted-retry --resume`, with the token
   in that command's environment only. The writer lease had expired while the
   service was refusing it.

## Why This Works

The service validates each cut against its own copy of the snapshot schema. A
cut carrying a member the deployed schema doesn't know fails validation until the
service is rebuilt from the release that added it.

## Prevention

`docs/own-deliveries.md` already says the service and the publishing generation
move together: applying a generation that changes the snapshot also deploys the
service. Treat `invalid_projection` on a fresh delivery as the sign that this
step was missed, and check the deployed version before anything else. A red
"Hosted delivery viewer" job on main can also hold the deploy back; it was red
here from a stale viewer test, fixed in PR #538.

## Related Issues

- V26-3993's repo gap 13, which recorded the refusal and its evidence.
- `docs/solutions/integration-issues/hosted-viewer-hooks-the-line-map-by-exact-text-2026-11-20.md`
