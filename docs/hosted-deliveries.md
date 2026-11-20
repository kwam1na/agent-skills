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

Use supported Node 22.22.2+ or 24.15+; CI pins 22.23.5. Run the service's own
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

## Versions move together

The service and the publishing generation move together. The snapshot body is
`hosted-delivery-snapshot/3`, sent as renderer `delivery-line-map/3`: a service that
reads another version refuses the body as an unsupported renderer, and a publisher
refuses a receipt naming another. Deploy the service and apply the generation in
one step. A delivery hosted as `/2` or earlier stays at its last acknowledged cut and its page
then reads as requiring a compatible snapshot, since nothing republishes a finished
delivery. One still open under the older generation closes there first, as its
journal must: the newer publisher reads that delivery's identity, pending body or
terminal body as corrupt (`identity_corrupt`, `terminal_corrupt`) rather than
half-reading it.

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
hosted-delivery-snapshots`. Provision independent random publishing tokens with at
least 256 bits per repository. Populate the `PUBLISHERS` secret with a JSON object
mapping each repository ID to the SHA-256 lowercase hex digest of its token, not
the token. Use an interactive secret prompt or a protected file outside the
checkout; never put values in command arguments or shell history. Publish that
secret with `npx wrangler secret put PUBLISHERS`, then `npx wrangler deploy` from
the reviewed checkout. On a first deployment, secret provisioning may create the
Worker before code deployment; verify the selected Worker/account before each
command. The deployment applies the declared DO migration and attaches the custom
domain. Wait for certificate readiness and verify actual HTTPS; DNS creation alone
is not success. Runtime rollbacks use a verified prior Worker version, preserve
DO/R2 data and account for migration compatibility; never delete retained storage
to undo a code deployment. Keep the previous configuration/version ID for rollback.

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
