# Hosted deliveries

A repository may publish its delivery to a signed-out capability link, where it
reads as the local live view does. Local delivery state remains authoritative. The service in
`hosted/delivery-viewer/` runs as a Cloudflare Worker or as a portable container, and any service that
conforms to `hosted-delivery-api/1` is a valid origin (see [Choose a host](#choose-a-host)). It is
deployed separately from portable workflow archives.
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

## Choose a host

A publisher needs only an origin that speaks `hosted-delivery-api/1`, specified
in `hosted/protocol/hosted-delivery-api-1.md`. Any service that conforms to it is
a valid origin, whoever runs it and wherever. The publisher's rules for the origin
stay the same everywhere: an explicit HTTPS DNS origin on port 443 with no path.
There are three ways to provide one:

- **Cloudflare.** The Worker in `hosted/delivery-viewer/`, deployed as the
  [reviewed production procedure](#reviewed-production-procedure) describes, with
  publishers managed as in [Manage a publisher](#manage-a-publisher).
- **The portable container.** The same service core over SQLite and a local
  filesystem, one process on one volume, built from the release's service bundle.
- **Your own implementation.** Any server that passes the conformance suite in
  `hosted/conformance/`.

Each path keeps capability links out of logs. A delivery link's capability sits
in its URL path, so whatever terminates TLS in front of the service must have its
access logging off or path-free.

### The portable container

Each release carries `hosted-delivery-service-<version>.zip` and
`hosted-delivery-service-<version>.zip.sha256`: the service's source, lockfile and
Dockerfile, the protocol spec and the conformance suite, with every file at its
repository path. Use the bundle from the same release as the generation you
applied, because the publisher, the snapshot schema and the service move
together. Nothing is pushed to a container registry: build the image yourself
from bytes you have checked.

```sh
gh release download <version> --repo kwam1na/agent-skills --pattern 'hosted-delivery-service-*'
shasum -a 256 -c hosted-delivery-service-<version>.zip.sha256
mkdir hosted-delivery-service && unzip -q hosted-delivery-service-<version>.zip -d hosted-delivery-service
cd hosted-delivery-service
docker build -f hosted/delivery-viewer/Dockerfile -t hosted-delivery-service:<version> .
```

The digest must also equal the one the release notes list under "Service
bundle". Stop if either check fails. The build runs from the root of the
extracted bundle. Its two stages share one Node base pinned by digest. The
runtime image holds only Node and the built server, runs as the non-root `node`
user (uid 1000) on a read-only root filesystem, and keeps its data under `/data`.

**Publishers.** Give each repository its own ID and token.
`provision-token` prints a new token once, 256 random bits as unpadded
base64url, and the `PUBLISHERS` entry that accepts it, the token's SHA-256
digest. Nothing is stored.

```sh
docker run --rm --read-only hosted-delivery-service:<version> provision-token REPOSITORY_ID
```

Put the token in the adopter's secret store under its `tokenEnv` name, and keep
it out of shell history and logs. Add the entry to `publishers.json`, one JSON
object mapping every repository ID to its digest. Point `PUBLISHERS_FILE` at that
file. The server reads the file on every check, so a new entry, a rotation (run
`provision-token` again and replace the entry) or a revocation (remove the entry)
takes effect at the next request with no restart. A file that cannot be read
denies every write. `PUBLISHERS`, the same map inline, is the alternative.
Exactly one of the two is set.

**A VPS recipe.** One host with Docker Compose, the service on a local volume,
and Caddy terminating TLS. Caddy writes no access log unless a `log` directive
asks for one, so this Caddyfile has none:

```text
deliveries.example.com {
	reverse_proxy deliveries:8080
}
```

```yaml
services:
  deliveries:
    image: hosted-delivery-service:<version>
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    environment:
      PUBLISHERS_FILE: /run/secrets/publishers
    secrets: [publishers]
    volumes: ["deliveries:/data"]
    restart: unless-stopped
  proxy:
    image: caddy:2 # pin the image by digest
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy-data:/data"]
    restart: unless-stopped
secrets:
  publishers:
    file: ./publishers.json
volumes:
  deliveries: {}
  caddy-data: {}
```

Only the proxy publishes ports, and the service is reached only through it.
`publishers.json` must be readable by uid 1000. Run exactly one `deliveries`
container. A second one on the same volume stops at startup with
`hosted.data_dir_in_use`, and nothing supports more than one instance. The server
prints one line when it listens and logs nothing per request. For a health check,
read the fixed `{"error":"not_found"}` 404 that `GET /` answers; there is no
health route. SIGTERM stops it gracefully. Back up the volume while the service
is stopped.

**Volumes.** SQLite's exclusive lock and the blob store's `link()` need local
block-device semantics. Give the service a disk that one machine mounts:

- AWS: an EBS volume attached to the EC2 instance;
- GCP: a Persistent Disk or Hyperdisk attached to the Compute Engine VM;
- Azure: a managed disk attached to the VM;
- your own hardware: a local disk or SSD;
- Kubernetes: one replica on a ReadWriteOnce volume backed by one of those disks,
  replaced rather than rolled, so two pods never share it.

**Unsupported filesystems.** Network filesystems are unsupported: AWS EFS, Azure
Files, Cloud Run volume mounts, GCS FUSE and NFS. Nothing detects them, so the
choice of disk is yours to get right.

**Connections.** The server speaks plain HTTP behind the proxy and never reads
`Host` or `X-Forwarded-*`. It bounds headers, requests, keep-alive and the number
of connections. A request that fails before it reaches the service closes the
socket with no bytes written. Like Node's own HTTP server, it closes a half-closed
connection whose response is still pending. A client must read its reply before
it sends FIN.

**What is proven.** `hosted.node-test` and `hosted.browser` run the portable
server built from source. `hosted.conformance-node` builds it from an extracted
service bundle and runs the conformance suite's fast tier against it. That is
source and fixture proof. The container check,
`node test/conformance/container.mjs`, needs Docker, and it reports itself
unavailable where Docker is missing. No local build of the image is recorded: the
image is first built and run in the service CI's container job. A deployment on a
real host, with an installed adopter publishing to it over HTTPS, is live
acceptance.

### Your own implementation

Implement `hosted-delivery-api/1` as the spec states it, the failures it names
before a request reaches the service included, and prove it with the conformance
suite. The suite is a Node package with no dependencies, and it ships in the
service bundle. Point it at an origin whose deliveries are disposable, with two
repository credentials the origin accepts:

```sh
node hosted/conformance/cli.mjs --origin https://deliveries.example.com --credentials credentials.json
node hosted/conformance/cli.mjs --origin https://deliveries.example.com --credentials credentials.json --slow
```

The fast tier covers every route, error code, header rule, size bound,
idempotency rule, fencing rule and the anonymous read surface in seconds. `--slow`
adds lease expiry, resume, takeover and staleness, in real time. An origin that
passes both tiers is a valid origin. The suite pins the API, snapshot and renderer
versions, so run the suite from the same release as the generation your
publishers apply.

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
`hosted.build`, `hosted.test`, `hosted.browser-install`, `hosted.browser`,
`hosted.conformance`, `hosted.node-test` and `hosted.conformance-node` for those
readers, the protocol spec and the conformance suite among them.
`hosted.conformance` runs the fast tier against the Worker, and its planted-failure
copies must each fail it. `hosted.conformance-node` builds the portable server
from an extracted service bundle and runs the fast tier against it. The service
CI adds the slow tier for both servers and the container check, each in its own
job. A local merge runner needs supported Node on PATH and Chromium's
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
  For the portable container: the service bundle's sha256, the built image's
  digest, the host and its block volume, and the proxy's logging setting.
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
