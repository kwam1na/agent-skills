# Hosted delivery ingestion

A neutral service core with a Cloudflare adapter: private Worker + SQLite Durable
Object + private R2 snapshot storage. This package implements ingestion, committed
read APIs and the trusted read-only browser viewer. Local runtime tests are not
production or installed-adopter evidence.

## Layout

- `src/core/` is the service on Web-standard `Request`, `Response`,
  `crypto.subtle`, `TextEncoder` and streams, plus `node:crypto`
  `timingSafeEqual`, which both runtimes provide. `service.ts` routes,
  authenticates, validates and serves the viewer's asset table; `deliveries.ts` is
  the delivery state machine; `ports.ts` states the `MetadataStore` and write-once
  `BlobStore` contracts it is written against; `publishers.ts` reads a
  `PUBLISHERS` map. With `src/protocol.ts`, `src/projection-v2.ts` and the
  generated validators and assets, it typechecks under `tsconfig.core.json`, which
  loads neither Workers nor Node types.
- `src/cloudflare/` is the Cloudflare adapter: `worker.ts`, the Worker entry and
  its R2 publisher records, and `delivery-object.ts`, the Durable Object
  `MetadataStore` and the R2 `BlobStore`. It typechecks under `tsconfig.json`
  with the Workers types.
- `src/node/` is the portable server: the core over one SQLite database and
  write-once filesystem blobs in a data directory, behind `node:http`, with no
  runtime dependencies. `npm run build:node` bundles it to
  `build/node/server.mjs`; it typechecks under `tsconfig.node.json`, and
  `Dockerfile` builds its container image. See [Portable server](#portable-server).
- `src/viewer.ts`, `src/view-store.ts` and `src/viewer.css` are the browser viewer
  the build bundles into the asset table.

Use Node 22.22.2+ or 24.15+ (an even, supported Node release), then `npm ci`.
Run `npm test`, `npm run typecheck`, and `npm run build`. The build is a Wrangler
dry run and deploys nothing. `generate:core` compiles the validators and builds
the asset table, with no Wrangler; `generate` adds `wrangler types` for the
Cloudflare target. `generate` compiles the corpus's fixed JSON Schema
2020-12 with Ajv standalone at build time and generates bindings from
`wrangler.jsonc`; the Worker never compiles schemas or uses eval/new Function.
The package consumes the shared fixture manifest directly at generation time.
Root dependency manifests and product archives are independent of this package.

Cloudflare runtime references: [SQLite transactions](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#transactions),
[Workers testing](https://developers.cloudflare.com/workers/testing/vitest-integration/write-your-first-test/),
and [generated types](https://developers.cloudflare.com/workers/languages/typescript/#generate-types).

## Protocol: hosted-delivery-api/1

The protocol is specified in
[`hosted/protocol/hosted-delivery-api-1.md`](../protocol/hosted-delivery-api-1.md):
the transport contract, the anonymous viewer API, the response headers and the
error table. This Worker is one implementation of it, and
[`hosted/conformance/`](../conformance/) checks a running origin against it.

What is particular to this Worker:

- Each delivery's metadata is one SQLite Durable Object, named by its public ID.
  Snapshot bytes go to the private R2 bucket under write-once puts. Only a
  successful immutable R2 write followed by a synchronous metadata transaction
  advances the pointer, and the transaction rechecks persisted epoch, lease and
  sequence after the R2 await. No bucket lifecycle deletion is configured.
- The receiver looks up the repository's publishing digest in the private R2
  bucket under `_publishers/v1/<repositoryId>`, falling back to the
  `PUBLISHERS` Worker secret, as the spec's authentication section says.
- Worker observability is disabled so request paths are not collected by
  default.

## Portable server

One process, one data directory, single writer. Configure it with:

- `HOST` (default `127.0.0.1`; the image sets `0.0.0.0`) and `PORT` (default `8080`);
- `DATA_DIR`, an absolute path on a local block volume (the image uses `/data`);
- exactly one of `PUBLISHERS`, the JSON map of repository IDs to token digests,
  or `PUBLISHERS_FILE`, a file holding it. The file is read on every check, so a
  rotation takes effect without a restart, and a file that cannot be read denies
  access.

`node build/node/server.mjs provision-token <repositoryId>` prints a new token
once, 256 random bits as unpadded base64url, and the `PUBLISHERS` entry that
accepts it. Nothing is stored. Add the entry to the map and hand the token to the
publisher.

The server answers on one line, `hosted delivery service listening on <origin>`,
and logs nothing per request. A missing, ambiguous or malformed configuration
stops it at startup with a named error (`hosted.config_invalid`,
`hosted.publishers_invalid`). SIGTERM or SIGINT stops it gracefully: it accepts
nothing new, lets requests in flight finish for up to ten seconds, and exits 0.

Storage, per the plan's decisions:

- `DATA_DIR/metadata.sqlite` holds every delivery, keyed by public ID, opened
  with `locking_mode=EXCLUSIVE` and `synchronous=FULL`, with each operation in a
  `BEGIN IMMEDIATE` transaction. Startup takes the lock with `BEGIN EXCLUSIVE`, so
  a second instance on the same data directory fails at startup with
  `hosted.data_dir_in_use`, even against an idle first one. The OS releases the
  lock with a crashed process, so no lock file is left behind.
- A snapshot is written to `DATA_DIR/staging/`, fsynced, `link()`ed to its
  content-addressed name in `DATA_DIR/blobs/` (an existing name with identical
  bytes counts as success), and the directory is fsynced before the metadata
  transaction. Startup clears what a crash left in `staging/`.
- SQLite and `link()` need local block-device semantics. Network filesystems are
  unsupported: AWS EFS, Azure Files, Cloud Run volume mounts, GCS FUSE and NFS.
  Nothing detects them.

The server speaks HTTP; TLS terminates in a reverse proxy or load balancer in
front of it, whose access logging must be off or path-free, because capability
links sit in URL paths. It never reads `Host` or `X-Forwarded-*`. It bounds every
request with header, request and keep-alive timeouts, a header size limit and a
connection cap, and streams bodies with the core's byte counting. A request that
fails before it reaches the core (headers or body too slow, a header block over
the limit, a malformed request line, a connection over the cap) is closed with
no bytes written; Node's own 400, 408 and 431 responses are never sent.
Health checks read the fixed `{"error":"not_found"}` 404 that `GET /` answers.

The image is a two-stage build on one base pinned by digest. The build stage runs
`npm ci --ignore-scripts` and `npm run build:node`; the runtime stage holds only
Node and `server.mjs`, runs as the non-root `node` user, and works on a read-only
root filesystem with its volume at `/data`. Build it from the root of an
extracted service bundle or a checkout:
`docker build -f hosted/delivery-viewer/Dockerfile .`.

Its checks: `npm run test:node` runs the core storage contract against SQLite and
the filesystem, the durability settings and write order, a fault between the blob
and the transaction, the startup lock, a crash restart, the pre-core HTTP
failures and provisioned tokens. `node test/conformance/check-node.mjs [--slow]`
runs the conformance suite against the built server, and
`npm run test:browser:node` runs the Chromium suite against it, with
`test/browser/portable-publisher.spec.ts`: the corpus publisher's own
`Transport`, through its loopback test seam, acquires, commits twice, confirms,
reads status, retries exactly and survives a server restart, and the page
renders the last committed cut. That is source and fixture proof; an installed
adopter publishing over HTTPS stays with live acceptance.

`npm run test:browser` runs the Chromium suite against both servers.
`node test/conformance/bundle.mjs` is `hosted.conformance-node`: it builds the
release's service bundle with `scripts/cut-release.py --service-bundle`, checks
its `.sha256`, extracts it under `TMPDIR` outside any git worktree, and from that
extraction alone installs, builds the server and runs the fast tier against it.
`node test/conformance/container.mjs` builds the image from the same extraction's
Dockerfile, checks that it runs as `node`, provisions two tokens with it, runs it
read-only with its volume at `/data`, and runs the fast tier against it. It removes
the image, container and volume it named. Where Docker is missing it exits 3 and
says the check is unavailable, never that it passed. The service CI runs it as its
own job, beside the fast checks and the two slow tiers.

## Local development and proof boundary

`npm run dev` binds to `127.0.0.1` with real local DO/R2 simulation. Use only
synthetic tokens in ignored `.dev.vars`, with `PUBLISHERS` set to the digest map.
The safe fixture service origin is `http://127.0.0.1:8787`; production uploaders
must permit HTTP only as an explicit loopback test setting, reject redirects and
never infer an origin from payload data. No HTTP exception is implied for hosted
production. Generate local capability/owner IDs independently; never reuse a real
public link or production token in fixture logs. Request inspection must redact
tokens and capability paths.

The test suite uses current Cloudflare Vitest integration and actual local
Worker/SQLite DO/private R2 bindings. It pauses the real R2 await, lets competing
commits run, and injects R2 failure. The core storage contract,
`test/contract/storage.ts`, holds a store's first blob write until a takeover, a
lease expiry on an injected clock, a competing commit or an exact retry has run,
and faults the commit transaction after its receipt insert. It expects
`writer_fenced`, `lease_expired` or `sequence_superseded`, one immutable receipt,
and no receipt row for a rolled-back commit. `test/storage-contract.test.ts` runs it
against the Durable Object store and R2. `test/versions.test.ts` holds the
conformance suite's versions and bounds equal to the core's and the spec's.
Eviction tests use the runtime's actual
eviction helper and retain session/receipt/binding state. Shared schema and
boundary fixtures run through HTTP handling. These checks prove local runtime
behavior only. No hosted CI, deployment, DNS, live browser or installed-archive
qualification is claimed by this package.

`node test/conformance/check.mjs`, after `npm run build`, is the
`hosted.conformance` check. It starts the built Worker through
`test/launcher.mjs`, the same launcher and synthetic credentials the browser
run uses, and runs the conformance suite's fast tier against it; `--slow` adds
the slow tier, which waits out a lease and staleness in real time. It then
builds a copy of `src/` unchanged, which must pass, and one planted-failure copy
per rule in `test/conformance/planted.mjs`, each the core under the Cloudflare
adapter with exactly one rule removed,
which must start and fail exactly the scenarios it names. The Worker's run must
cover exactly the scenario names the check lists, and `hosted/conformance/cli.mjs`
must exit 0 against the Worker and 1 against a planted copy. A planted copy that
does not start, or whose patch text has drifted, fails the check.


## Trusted browser viewer

`GET /d/{id}` serves a fixed shell only after a committed snapshot exists. The
shell embeds no publisher data and loads content-addressed external JS/CSS. The
build consumes `page.js` (the page controller, first), `line-map.js`,
`run-summary.js` and their styles directly from the corpus. Checked narrow store hooks replace the local HTML refresh mechanism with
one coherent projection push; drift in those anchors refuses the build. Inert
seed blocks are updated idempotently for every accepted cut, including optional
block removal, so a delayed first renderer cannot combine revisions. No
corpus asset is modified or copied into an independently maintained renderer.
Plan presence changes update its chapter; native subject selection, review/plan
folds, focus and horizontal line position remain with the existing UI. The host
preserves the viewport across a pushed revision.

The page renders in the local view's type. The build reads the faces the local
view's `FONTS` link asks Google Fonts for in `render.py` and serves each from
this origin instead: Newsreader, Geist and Geist Mono, from the woff2 files under
`fonts/`, one `@font-face` per requested weight and style, as content-addressed,
immutable-cached assets. `fonts/sources.json` records each file's npm package,
version and integrity and its own sha256, and each family's SIL Open Font
License 1.1 sits beside its files and is served too. The build stops with a
named `hosted.font_*` error when a listed file is missing or changed, or when the
local link or the stylesheet's type variables name a face it does not serve. The
files carry the Latin and Latin Extended ranges; other scripts fall back to the
stylesheet's next family.

The hosting status draws no strip. A current publication, or a completed one
saved and retained while its connection holds, shows only as the hero's live line, whose label offers the
source-confirmed, published and revision times on hover or focus. A loading,
stale, unavailable or incompatible publication is also said in that line, in
words, without interaction. A fixed `role="status"` region outside the
renderer's DOM announces every change, and is seen on its own only before the
live line is drawn.

The store polls every five seconds, with one request in flight and a ten-second
request deadline. It checks schema, body bounds, exact digest, fingerprint,
renderer contract and monotonic epoch/sequence. A missing pointer, failed fetch,
incompatible schema/renderer or bad digest keeps the prior projection readable.
Freshness uses server source age plus monotonic elapsed time, including download
time; transport failure never resets it. A confirmed terminal journal cut is
retained rather than labeled stale, while work that reopens it and an unresolved
newer pointer do not receive that exemption. A failed renderer asset remains an
explicit failure across otherwise successful data polls. The page resumes its
observer when restored from the back/forward cache.

All untrusted text enters JSON data blocks through DOM textContent and the
trusted renderer's text nodes. The shell has no inline executable script or
style, arbitrary uploaded asset or raw review artifact. CSP permits only fixed
same-origin scripts, styles, fonts and data connections, with no eval/inline
script, frames, forms or external resource loads. Native external links retain
noopener/noreferrer; presentation removes addresses containing this capability,
including percent-encoded forms. Public asset routes match only exact build
hashes and use immutable caching; shell/current/data/errors retain no-store.
There is no directory, history page, control action or authenticated viewer.

Run `PLAYWRIGHT_SKIP_BROWSER_GC=1 npx playwright install chromium` to install the
locked browser without removing other shared browser versions. Then run
`npm run test:browser` under the supported Node version. This dry-run builds the
actual Worker and starts a local Miniflare5 Worker/SQLite DO/R2 runtime on a free
loopback port with synthetic repository credentials, through the shared
`test/launcher.mjs`. The harness disables request
logging and telemetry and disposes only its own runtime. Desktop and mobile
Chromium tests assert signed-out access, hostile text remaining text, browser
CSP/MIME rejection, the local view's fonts loaded from this origin with no
request leaving it, the hosting status in the live line, external request
Referer absence and null opener, live update
state preservation, pointer/fetch/compatibility failure retention, stale/terminal
truthfulness and explicit asset failure. External navigation and fault responses
are intercepted in the browser for deterministic assertions; no remote account
or production service is involved. Screenshots/traces stay under ignored test
outputs. Node/jsdom tests cover adapter/DOM behavior separately and are not
browser-security proof. This remains local browser evidence, not deployment or
off-machine adopter qualification.

## Packaging and production preparation

The portable publisher is qualified from exact built archives in two isolated
adopters; this service and its package/dependencies are excluded. Its independent
CI runs locked typecheck, dry-run build, Worker/DOM and Chromium checks when the
service or shared schema/assets/theme/fixtures change. Corpus CI does not replace
this job. See [the operator guide](../../docs/hosted-deliveries.md) for the reviewed
non-secret `deliveries.v26technologies.com` custom domain configuration, private
storage and scoped-secret procedure, exact-byte evidence and release/adoption
sequence. These files prepare deployment; no production action or live acceptance
is claimed here.
