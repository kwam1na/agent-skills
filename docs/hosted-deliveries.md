# Hosted deliveries

A repository may publish its delivery to a signed-out capability link, where it
reads as the local live view does. Local delivery state remains authoritative. The Worker package in
`hosted/delivery-viewer/` is deployed separately from portable workflow archives.
No live proof is claimed by packaging, source tests, local receivers or service CI.

## Install and configure

Select `delivery-views`, `run-delivery`, `linear` or `jira`; all include the
publisher, public projection and fixed snapshot schema. Check the archive's sha256
against the release notes as described in README.md, and apply through the supported product lifecycle. Do not copy scripts or
edit `.agent-skills/current`.

```sh
python3 -B <archive.zip> --root <repository> --product apply --archive <archive.zip> --metadata <archive.release.json> --maintenance
```

Configure only `.agents/delivery-views.json` in each adopter:

```json
{
  "spec": "delivery-views-config/1",
  "hosted": {
    "origin": "https://deliveries.v26technologies.com",
    "repositoryId": "operator-provisioned-repository-id",
    "tokenEnv": "DELIVERY_PUBLISH_TOKEN"
  }
}
```

Merge that member into the existing view configuration. Each repository gets a
distinct repository ID and publishing token; matching local delivery keys do not
share identities. Store the token only in the named environment variable through
the operator's secret store. The token is scoped publishing authority, never a
Cloudflare account credential. The lifecycle starts its independent publisher;
manual status uses the installed script and needs no token:

```sh
python3 -B .agent-skills/current/skills/delivery-views/scripts/hosted.py status --config .agents/delivery-views.json --binding .agents/orchestration.json --state-dir .orchestration/state --key APP-1
```

The resulting URL grants read access: share deliberately. No index, history,
control or raw artifact route is public. Source confirmation ages independently
of network success; after 120 seconds an active view is stale. A completed,
acknowledged cut remains readable after the publisher stops. A pending final
publication is shown explicitly and can be retried with the same identity.
Archive viewing starts no publisher and requires no credential. Preserve hosted
identity/receipt sidecars with their record for same-link recovery; keep them out
of release archives and public exports. Never copy one repository's sidecars to
another. Consult the installed skill for `retry`, terminal deadline and recovery.

## What a hosted link shows

A hosted link shows what the local live view shows, from the same record: every
chapter, the story, each journal entry with its facts, the integration step, the
plan, Needs you, review evidence with its briefs and results, the files view,
what a closed delivery left open and how far the integration's target moved. Its
prose keeps the paths, commands and names the record holds, as the local page
draws them. Anyone holding the link reads all of it.

What is never published is a credential. A cut that holds a configured secret's
value anywhere, the publishing token among them, or a field named password,
token, secret, API key, credential, authorization or cookie, is refused with its
code, `snapshot.configured_secret` or `snapshot.credential_field`. `status` names
the code and the page keeps its last cut until a clean one is sent. No scanner
claims to recognize every secret someone may type. A sibling delivery's hosted
link grants read access to that delivery, so it is a credential too: the page
names the sibling without it. The git identity in the local page's corner belongs to the reader's checkout,
not to the record, so the hosted page has none.

## Service checks

Use Node 24 via the repository’s `.nvmrc` for development and CI. The service supports Node 22.22.2+ on the 22 line and Node 24.15 or newer. Run the service's own
locked install, typecheck, build, Worker/DOM and Chromium tests from its directory.
The build is dry-run only. Root page-test manifests stay independent. Dedicated
CI triggers for service files, trusted corpus assets/theme source, shared schema
and shared fixtures. The binding selects `hosted.install`, `hosted.typecheck`,
`hosted.build`, `hosted.test`, `hosted.browser-install` and `hosted.browser` for
those readers. A local merge runner needs supported Node on PATH and Chromium's
OS prerequisites; CI installs those with Playwright. No Python suite implicitly
installs the service package. The lead verifies this separate service job at the
exact main-push commit before release/deploy; the publish command reads no CI
result, and the local suite before a tag does not attest service CI.

## Snapshot compatibility

The shared viewer accepts `delivery-line-map/2` and `delivery-line-map/3` publishers. Each upload declares its renderer and must pass that version’s snapshot validation. The receipt records the accepted renderer. Opening a delivery link selects the page and its immutable assets from the stored receipt, so a finished v2 delivery remains readable at its original URL when another repository starts publishing v3.

Keep an active delivery on the publisher generation that created its local identity and terminal records. Start new deliveries with the new generation. When adding a renderer, retain the existing reader, validation rules and assets, and verify a link created before deployment alongside a new delivery link.

## Reviewed production procedure

The tracked Wrangler configuration names `hosted-delivery-viewer`, private R2
bucket `hosted-delivery-snapshots`, SQLite `DeliveryObject` migration `v1`, disabled
workers.dev/preview URLs and observability, and a single custom domain:
`deliveries.v26technologies.com`. No account ID or secret value is committed.
[Cloudflare Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
use `routes: [{pattern: hostname, custom_domain: true}]`; attaching the domain
creates its DNS record and certificate. Before any mutation the operator verifies
the selected account and active zone and confirms the hostname has no conflicting
service. Stop on a collision; do not replace existing DNS. Keep the R2 bucket
private with no public domain/r2.dev access and no lifecycle deletion of retained
snapshots. Do not enable request/path logging or analytics containing capabilities.

Only the authorized deployment operator runs the following in the reviewed
service checkout after checking exact main-push CI and account/zone permissions:

```sh
npm ci --ignore-scripts --no-audit --no-fund --engine-strict
npm run typecheck
npm test
npm run build
npx wrangler whoami
npx wrangler r2 bucket info hosted-delivery-snapshots
```

If absent, create that private bucket with `npx wrangler r2 bucket create
hosted-delivery-snapshots`. Retain the current `PUBLISHERS` secret when upgrading.
For a new Worker, initialize this legacy fallback with the empty JSON object
`{}` through `npx wrangler secret put PUBLISHERS`. Run `npx wrangler deploy`
from the reviewed checkout. On a first deployment, initializing the secret may
create the Worker. Verify the selected Worker and account before each command.
The deployment applies the declared DO migration and attaches the custom
domain. Wait for certificate readiness and verify actual HTTPS; DNS creation alone
is not success. Runtime rollbacks use a verified prior Worker version, preserve
DO/R2 data and account for migration compatibility; never delete retained storage
to undo a code deployment. Keep the previous configuration/version ID for rollback.

### Manage a publisher

From the viewer package, verify the Wrangler account and the bucket bound as
`SNAPSHOTS`. Give each repository a random ID and a distinct token generated from
at least 32 random bytes, encoded as unpadded base64url. Export the token from
the operator's credential store. Only the variable name appears in the command.

```sh
node scripts/publisher.mjs set hosted-delivery-snapshots REPOSITORY_ID DELIVERY_PUBLISH_TOKEN
node scripts/publisher.mjs revoke hosted-delivery-snapshots REPOSITORY_ID
```

Use `set` for both initial provisioning and rotation. It writes the token’s
SHA-256 digest to one private object and verifies the result by reading it back.
The receiver checks this object on each write request. Coordinate changes to
the same repository ID because the latest write wins. Other repository IDs
can be managed independently. A token change takes effect for the next request,
so restart active publishers with the new environment after rotation.

Revocation stores a permanent marker. Keep the marker and all publisher
records outside bucket expiration rules. Deleting a record restores the
legacy fallback for that repository. Before rolling the Worker back to code
that only reads `PUBLISHERS`, reconcile legacy credentials with current
revocations and rotations.

## Acceptance evidence

Record separate identities and results, without secret values:

- Source/fixture proof: focused tests and deterministic local receiver scenarios;
  these prove code/protocol boundaries, not Cloudflare or remote operation.
- Installed bytes: exact locally built archive SHA-256, verification result,
  installed receipt and unchanged generation bytes in two clean repository
  bindings. Socket substitution in a fixture remains a fixture seam.
- Published candidate: exact main-push SHA, the local macOS Python 3.11 suite's
  pass at it and service CI, annotated candidate tag, archive and metadata digests, applied active receipt.
- Exact deployed bytes: reviewed source SHA, Wrangler version, dry-run Worker and
  asset digests, actual Worker version/deployment ID, domain and storage bindings.
- Live acceptance: two repositories with matching delivery names, an unrelated
  signed-out device, healthy update cadence, disconnect and retained stale view,
  reconnect/catch-up at the same URL, interrupted upload/restart without regression,
  wrong-repository credential rejection and completed-link survival after shutdown.
  Check no public listing/history/control or secret-bearing response/asset exists.

Keep the live story open until all actual scenarios have evidence. A runtime or
record-shape change first ships as a candidate from an exact successful main-push
commit. Apply it in the adopter before testing its installed publisher. Promote
final on the same commit after candidate acceptance; verify/apply the final's own
archive because its release ID changes the digest. Provisioning, candidate release,
real deployment and live acceptance are operator work; this document records the
reviewed procedure without claiming those actions occurred.
