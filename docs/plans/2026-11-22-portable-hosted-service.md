---
title: Portable hosted delivery service - Plan
type: feat
date: 2026-11-22
topic: portable-hosted-service
reviewSlug: portable-hosted-service
execution: code
---

# Portable hosted delivery service - Plan

## Goal

Let an adopter host the hosted delivery viewer wherever they choose (a VPS, AWS,
GCP, Azure or their own hardware) instead of only on our Cloudflare Worker, with
the same links, protocol and guarantees the Worker gives today.

Operator decisions, 2026-11-22:

- **Deliverable:** a normative protocol spec, a black-box conformance suite any
  origin can be run against, and one portable reference server. The Cloudflare
  Worker becomes one adapter over a shared core. No per-cloud templates.
- **Portable storage:** SQLite plus a local filesystem, one process, one mounted
  volume, single writer.
- **Distribution:** each GitHub release carries a verified service bundle (source,
  lockfile, Dockerfile, spec and conformance suite) with its digest. Adopters build
  the image from bytes they can check. Nothing is pushed to a container registry.

## Current state (evidence)

- The publisher is already host-agnostic. `skills/delivery-views/scripts/views_common.py`
  accepts any explicit HTTPS DNS origin on port 443 with no path, and
  `skills/delivery-views/scripts/hosted.py` speaks only `hosted-delivery-api/1` over
  `http.client`. Plain HTTP is reachable only through `Transport(...,
  allow_loopback=True)`, a constructor argument the tests use. No command-line flag
  or configuration enables it.
- The service in `hosted/delivery-viewer/` (about 950 lines of TypeScript) is
  Cloudflare-only:
  - `src/delivery-object.ts` keeps per-delivery state in a SQLite Durable Object,
    relying on its per-object serialization and `transactionSync`.
  - Snapshot bytes go to R2 through write-once puts (`etagDoesNotMatch: '*'`).
  - `src/worker.ts` mixes routing, authentication and validation with `env.DELIVERIES`
    and `env.SNAPSHOTS`, and uses `node:crypto` `timingSafeEqual`.
  - `generate` and `build` call `wrangler types` and `wrangler deploy --dry-run`.
- The service build reads files outside `hosted/`:
  - `scripts/build-assets.mjs` reads `skills/delivery-views/assets/`;
  - `scripts/fonts.mjs` reads the `FONTS` link in `skills/delivery-views/scripts/render.py`;
  - `scripts/generate-validator.mjs` reads
    `skills/delivery-views/references/hosted-snapshot.schema.json` and
    `tests/fixtures/delivery-views/hosted/`.

  `.github/workflows/hosted-delivery-viewer.yml` triggers on exactly these, plus
  `hosted/delivery-viewer/**` and the workflow file itself. The hosted check readers
  in `.agents/orchestration.json` list the same paths. The service job's timeout is
  20 minutes.
- The browser harness, `test/browser/hosted-viewer.spec.ts`, already starts the built
  Worker in Miniflare on a free loopback port with synthetic credentials.
- The protocol exists only as prose in `hosted/delivery-viewer/README.md`, and its
  tests run inside the Cloudflare Vitest pool.
- Release archives exclude the service. `scripts/cut-release.py` produces exactly the
  named outputs, and `tests/test_cut_release.py::_named_outputs` pins that set.
  `EARLIER_OUTPUT` in `scripts/cut-release.py` names which earlier files a run may
  replace. The tests for `release_channel.py` (publish, dry run, `render_notes`) are
  in `tests/test_release_channel.py`.
- `hosted/delivery-viewer/test/ordering.test.ts` pins four ordering guarantees, against
  the Durable Object only:
  - fencing after the R2 await;
  - lease expiry during the await;
  - rollback at the commit boundary;
  - one receipt for simultaneous exact retries.
- Tests read the hosted docs and checks:
  - `tests/test_own_deliveries.py` pins `SERVICE_IDS` and the check prerequisites;
  - `tests/test_own_deliveries.py`, `tests/test_delivery_views_hosted_projection.py`
    and `tests/test_declared_mechanics_corpus.py` read `docs/hosted-deliveries.md`
    or the hosted workflow.
- `docs/solutions/integration-issues/hosted-viewer-hooks-the-line-map-by-exact-text-2026-11-20.md`:
  the viewer build patches `line-map.js` by exact text, so any rework of
  `build-assets.mjs` keeps those hooks and their `hosted.asset_hook_drift` refusal.
- Knowledge library: not searched. No knowledge tool is loaded in this session.

## Scope

In scope:

1. **Spec.** A normative `hosted-delivery-api/1` spec, moved out of the README with
   its meaning unchanged. The protocol itself does not change.
2. **Conformance suite.** A black-box suite that runs against an origin and two sets
   of repository credentials. It covers every route, error code, header rule, size
   bound, idempotency rule, fencing rule, and the anonymous read surface.
3. **Neutral core.** Routing, authentication, validation, the delivery state machine
   and the viewer's asset table, written against two ports: a `MetadataStore` and a
   write-once `BlobStore`.
4. **Cloudflare adapter.** Rebuilt on the core, with no behaviour change.
5. **Portable Node server.** Built on the core with no runtime dependencies:
   - `node:http`, `node:sqlite` and the filesystem;
   - a non-root container image on a base pinned by digest;
   - a `provision-token` subcommand that prints a new token once and the matching
     `PUBLISHERS` entry.
6. **Service bundle.** Added to the release output set, built deterministically in
   pure Python, with no Node in `cut-release`.
7. **Docs.** Operator docs for three paths (Cloudflare, the portable container, your
   own implementation), and one sentence in the `delivery-views` skill saying any
   conforming service is a valid origin.

Out of scope:

- Per-cloud templates, Terraform or managed-service adapters (S3, GCS, Azure Blob,
  DynamoDB, Postgres).
- Pushing images to a registry. Running more than one instance of the portable
  server, or high availability.
- Any change to `hosted-delivery-api/1`, `hosted-delivery-snapshot/3`,
  `delivery-line-map/3`, the publisher's transport or its origin rules. No CLI or
  configuration path to the loopback seam is added.
- Moving our own production deployment off Cloudflare.
- Windows validation.

## Decisions recorded here

- **Conformance does not change the protocol.** It adds no test-only route, clock
  hook or header. Lease expiry (180 s) and staleness (120 s) are proven black-box in
  an opt-in `slow` tier that waits real time. Each server's unit tests cover expiry
  with an injected clock. The fast tier runs as a local check; the slow tier runs in
  the service CI job.
- **The Worker under conformance is the existing harness.** The Miniflare runtime the
  browser harness builds is factored into a shared fixture launcher, so the
  conformance run, the browser run and their credentials come from one place.
- **No health route.** The public surface stays exactly as specified. Health checks
  read the fixed `{"error":"not_found"}` 404 that `GET /` returns.
- **The core uses only portable APIs.** It uses Web-standard APIs (`Request`,
  `Response`, `crypto.subtle`, `TextEncoder`, streams) plus `node:crypto`
  `timingSafeEqual`, which both runtimes provide. It typechecks under a tsconfig with
  neither Workers nor Node ambient types. Each adapter has its own tsconfig.
- **The `MetadataStore` contract states what the Durable Object gave implicitly.**
  - Each delivery's metadata operation is one synchronous, atomic transaction.
  - Nothing is assumed to hold across an `await`; commit rechecks epoch, lease and
    sequence inside its transaction after the blob write, as the Worker does today.
  - A read of an unknown public id creates no state.
- **The conformance suite is proven to fail.** A set of planted-failure servers must
  each fail the suite, and the failure must name the scenario that server is
  expected to trip. A planted server that fails to start counts as a failed proof,
  not a pass. How they are built:
  - **In V26-3748:** each is a copy of today's Worker source with exactly one rule
    removed, built at test time under `hosted/delivery-viewer/test/`.
  - **In V26-3749:** the same change that extracts the core re-derives them onto it, as
    the core with one rule removed, and each must still fail the suite.

  The rules removed:
  - `no-store` or the security headers on error responses;
  - an exact retry that refreshes `sourceConfirmedAt`;
  - the writer-fencing check;
  - query strings accepted;
  - only the first `PUBLISHERS` entry honoured;
  - a 413 replaced by a closed connection;
  - routing by `Host`.

  `hosted.conformance` runs these planted servers too, so a weakened suite turns the
  check red.
- **One storage contract suite for both stores.** The ordering guarantees become a
  core-level `MetadataStore`/`BlobStore` contract suite that runs against both the
  Durable Object store and the SQLite store. A `BlobStore` wrapper holds the first
  put until one of these has run:
  - a takeover;
  - a lease expiry, on an injected clock;
  - a competing commit;
  - an exact retry.

  A fault is also injected inside the commit transaction. The suite asserts the
  expected `writer_fenced`, `lease_expired` or `sequence_superseded`, a single
  immutable receipt, and no receipt row for a rolled-back commit.
- **Portable metadata lives in one SQLite database**, with rows keyed by public id,
  opened with `locking_mode=EXCLUSIVE` and `synchronous=FULL`, using `BEGIN IMMEDIATE`
  transactions.
  - **The lock is taken at startup.** Startup runs `BEGIN EXCLUSIVE` and commits it.
    The exclusive mode only locks on first access, so without this an idle instance
    would not hold the lock.
  - **The lock is the single-instance guard.** A second instance on the same data
    directory fails at startup with a named error. The OS releases the lock on a
    crash, so no stale lock file is left behind.
- **Write-once blobs on a filesystem.**
  - Bytes go to a temp file in the data directory, which is fsynced.
  - The temp file is then `link()`ed to the content-addressed name, which fails if
    the name exists. Identical bytes count as success.
  - The directory is fsynced before the metadata transaction.
- **Bounded requests.** The Node adapter streams bodies with the same byte counting
  as the Worker. It sets header, request and keep-alive timeouts and a connection
  cap, logs nothing per request, and returns only fixed error codes. It never reads
  `X-Forwarded-*` or `Host` for routing or identity.
- **Responses Node writes itself.** Some failures never reach the core: header and
  request timeouts, oversized headers, a malformed request line, and a connection
  over the cap.
  - **Spec:** it names each of these as either close-only (the socket closes with
    no bytes) or a fixed-code response with the full security header set and
    `no-store`.
  - **Server:** a `clientError` handler enforces what the spec names. Node's own
    default 400, 408 and 431 responses are never sent.
- **TLS terminates in front.** The portable server speaks HTTP behind a reverse proxy
  or load balancer. Because capability links sit in URL paths, the docs require the
  proxy's access logging to be off or path-free, as the Cloudflare docs require for
  the Worker.
- **Volume requirement.** SQLite and `link()` need local block-device semantics.
  Network filesystems are documented as unsupported: AWS EFS, Azure Files, Cloud Run
  volume mounts, GCS FUSE and NFS. Nothing claims to detect them.
- **The bundle's contents are an explicit list.** It is not derived from the
  workflow's trigger paths. The bundle holds these roots at their
  repository-relative locations, with ignored and untracked files excluded:
  - `hosted/delivery-viewer/`;
  - `hosted/protocol/`;
  - `hosted/conformance/`;
  - `skills/delivery-views/assets/`;
  - `skills/delivery-views/scripts/render.py`;
  - `skills/delivery-views/references/hosted-snapshot.schema.json`;
  - `tests/fixtures/delivery-views/hosted/`.

  **One root list, three places.** V26-3748 adds `hosted/protocol/**` and
  `hosted/conformance/**` to the workflow's `pull_request` and `push` paths, and to
  the hosted check readers, in the same change that creates them. From then on, the
  roots equal the workflow's trigger paths minus the workflow file, and a Python
  test pins that equality.

  **Proof that the bundle is enough:** a service check extracts the bundle under
  `TMPDIR`, outside any git worktree, so no checkout's `node_modules/` or repository
  root can supply a missing input. From that extraction alone it builds and runs the
  server, then runs the conformance suite.
- **Verification of the bundle.** `cut-release` writes
  `hosted-delivery-service-<version>.zip` and `hosted-delivery-service-<version>.zip.sha256`,
  and rechecks the digest against the written bytes before the output set is
  renamed into place. A bundle that changes between write and recheck is refused
  with a named code. `EARLIER_OUTPUT` gains both names. The release notes list the
  digest. The docs tell adopters to use the bundle from the same release as their
  applied generation, because versions move together.
- **Versions still move together.** The conformance suite pins the API, snapshot and
  renderer versions. A change to any of them moves the spec, the suite and both
  adapters in the same change. A service test asserts that the suite's version
  constants equal the core's and the spec's.
- **The slow tier takes time.** The slow tiers of the two servers run as parallel CI
  jobs, each within the job timeout, which is raised to 30 minutes in V26-3751.
- **Release-shape change.** The release output set changes, so it ships first as a
  candidate under AGENTS.md, is applied in one adopter, and is then promoted.
- **Python suite budget.** Bundle assertions go into the existing real `cut-release`
  run in `tests/test_cut_release.py`, and its fixture runs where they suffice. No new
  real cut is added, and `tests/suite-budget.json` is not raised.

## Finish line

All of the following hold at one main-push commit:

1. **Conformance on both servers.** The conformance suite's fast tier passes as a
   local check against the Worker (shared Miniflare launcher) and against the
   portable server built from an extracted release bundle. Every planted-failure
   server fails it. The slow tier's pass and the container run are recorded for both
   servers from a service CI run at that exact commit. When the push did not trigger
   one, the lead dispatches it with `workflow_dispatch` on `main`.
2. **Existing tests unchanged.** The existing Worker, DOM and Chromium tests pass
   against the Worker. The Chromium suite also passes against the portable server.
3. **The real publisher works against the portable server.** A service test drives
   the corpus publisher's own `Transport`, through its existing loopback test seam,
   against the portable server: acquire, at least two commits, confirm, status, an
   exact retry and a restart of the server. After the restart, the page renders the
   last committed cut, not the first, in Chromium. This is source and fixture proof. Installed-adopter proof over
   HTTPS stays with live acceptance.
4. **The bundle is reproducible.** `cut-release` emits the bundle and its `.sha256`.
   Two runs give identical bytes, one of them on a copy of the tree whose mtimes and
   at least one mode bit differ. `release_channel.py publish` (checked in
   `--dry-run`) uploads both files and lists the digest.
5. **Docs and the full suite.** Docs and the skill sentence have landed, with
   catalog, profile and provenance digests refreshed and pins re-derived. The full
   local suite passes on macOS with Python 3.11 inside its budget.

Live deployment on a real non-Cloudflare host, and an installed adopter publishing to
it, are operator acceptance. This delivery does not claim them.

## Units and order

| Tier | Lane | Items, in order | Owns |
| --- | --- | --- | --- |
| 1 | 1 | V26-3748 | `hosted/protocol/`, `hosted/conformance/`, `hosted/delivery-viewer/README.md`, `hosted/delivery-viewer/test/`, `.github/workflows/hosted-delivery-viewer.yml`, `.agents/orchestration.json`, `tests/test_own_deliveries.py`, `docs/own-deliveries.md` |
| 2 | 2 | V26-3752 | `scripts/cut-release.py`, `scripts/release_channel.py`, `tests/test_cut_release.py`, `tests/test_release_channel.py` |
| 2 | 1 | V26-3749 | `hosted/delivery-viewer/` |
| 3 | 1 | V26-3750 | `hosted/delivery-viewer/src/node/`, `hosted/delivery-viewer/Dockerfile` |
| 4 | 1 | V26-3751 | `.github/workflows/hosted-delivery-viewer.yml` |
| 5 | 2 | V26-3753 | `docs/hosted-deliveries.md`, `skills/delivery-views/SKILL.md`, `AGENTS.md`, `tests/test_delivery_views_hosted_projection.py`, `tests/test_declared_mechanics_corpus.py` |

The stories are children of the epic V26-3747.

## Stories

### V26-3748. Write the protocol spec and a black-box conformance suite

Lane 1, tier 1, not blocked. Characterization-first. Files: `hosted/protocol/`,
`hosted/conformance/`, `hosted/delivery-viewer/README.md`,
`hosted/delivery-viewer/test/`, `.github/workflows/hosted-delivery-viewer.yml`,
`.agents/orchestration.json`, `tests/test_own_deliveries.py` and
`docs/own-deliveries.md`.

- **Workflow and readers.** Add `hosted/protocol/**` and `hosted/conformance/**` to
  the workflow's `pull_request` and `push` paths and to the hosted check readers.
  Add `skills/delivery-views/scripts/hosted.py` and `views_common.py` to the readers
  as well, because the real-publisher test imports them.
- **New check.** Register `hosted.conformance`: the fast tier plus the
  planted-failure servers, against the Worker. Extend `SERVICE_IDS` and the
  prerequisite assertions in `tests/test_own_deliveries.py` for it.

- Move the transport contract, the anonymous viewer API and the error table into
  `hosted/protocol/hosted-delivery-api-1.md`. The README then points to it.
- Factor the browser harness's Miniflare startup into a shared launcher under
  `hosted/delivery-viewer/test/`.
- Build the suite as a Node package with no runtime dependencies beyond `fetch`. It
  takes an origin, two repository credentials and an optional `--slow` flag, and
  reuses `tests/fixtures/delivery-views/hosted/`.
- **Integration recipe.** Update the integration recipe in `docs/own-deliveries.md`
  to the new `SERVICE_IDS` and hosted reader paths. Its row in
  `tests/test_own_deliveries.py`
  (`test_integration_recipe_carries_only_named_checks_and_refuses_collisions`) stays
  green.
- Done when, before any refactor:
  - the suite's fast and slow tiers pass against today's Worker;
  - every planted-failure server, a copy of today's Worker with one rule removed,
    fails the suite, naming its expected scenario.
- Scenarios:
  - acquire, commit, confirm and status with each of the two credentials, each on
    its own delivery, before any rejection row;
  - hostile `Host` and `X-Forwarded-Host` values give identical responses and
    receipts;
  - an exact retry returning its original receipt, without refreshing freshness;
  - a sequence conflict and a superseded sequence;
  - a fenced writer;
  - a wrong-repository credential gets `unauthorized`, and a mismatched binding gets
    `not_found`;
  - each 400, 401, 404, 405, 413, 415 and 422 code;
  - streamed bodies over the limit without an honest `Content-Length`;
  - query strings, encoded paths and alternate methods refused;
  - security headers and `no-store` on every response, including errors;
  - no redirect, listing or history routes;
  - in the slow tier, expiry, resume, takeover and staleness.

### V26-3749. Split the service into a neutral core and a Cloudflare adapter

Lane 1, tier 2, after V26-3748. Characterization-first. Files: `hosted/delivery-viewer/`.

- Extract `src/core/` behind `MetadataStore` and `BlobStore`, with the contract the
  decisions above state. The Durable Object and R2 implement it in
  `src/cloudflare/`.
- Split the tsconfigs so the core typechecks with no ambient runtime types.
- Add a `generate:core` step without `wrangler types`. `wrangler types` and the
  dry-run build stay on the Cloudflare target.
- `build-assets.mjs` keeps its exact-text hooks.
- Re-derive V26-3748's planted-failure servers onto the core, each the core with one
  rule removed. Each must still fail the suite, naming its expected scenario.
- Move the four ordering rows of `test/ordering.test.ts` into the core-level storage
  contract suite, with deterministic interleaving. Run that suite against the
  Durable Object store.
- No production behaviour changes. Existing tests are re-derived only where import
  paths move.
- Done when every existing service test, the contract suite against the Durable
  Object store, and both conformance tiers pass.

### V26-3750. Ship a portable Node server and container

Lane 1, tier 3, after V26-3749. Test-first. Files: `hosted/delivery-viewer/src/node/`
and `hosted/delivery-viewer/Dockerfile`, with their tests under
`hosted/delivery-viewer/test/`.

- **Configuration:** `HOST`, `PORT`, `DATA_DIR`, and `PUBLISHERS` or
  `PUBLISHERS_FILE`.
- **Lifecycle:** graceful shutdown on SIGTERM, plus the `provision-token` subcommand.
- **Image:** a multi-stage build. The builder runs `npm ci --ignore-scripts` and the
  Node build; the runtime stage holds only the built server and Node. It runs as
  non-root on a read-only root filesystem, with its volume at `/data`.
- **Scenarios:**
  - **Storage contract:** the core-level contract suite passes against the SQLite
    store.
  - **Fault after the blob write:** a fault injected between blob and transaction
    leaves the blob, an unmoved pointer, and a retry that commits.
  - **Durability settings:** `PRAGMA synchronous` and `PRAGMA locking_mode`, read
    back from the open database, are `FULL` and `EXCLUSIVE`. An injected filesystem
    records the order temp-file fsync, `link`, directory fsync, and only then the
    metadata transaction.
  - **Crash:** after two commits, a server killed by its recorded pid restarts with
    the second one as its view.
  - **Different deliveries:** commits to different deliveries do not block each
    other.
  - **Second instance:** a second instance started against an idle first instance,
    on an existing data directory, is refused at startup.
  - **Unknown ids:** reading an unknown public id creates no row.
  - **Injected clock:** lease expiry, resume, takeover and staleness.
  - **Timeouts and malformed requests:** each of these gives what the spec names,
    either a closed socket with no bytes or a fixed code with the full header set:
    - slow headers;
    - a slow body;
    - a header block over the limit;
    - a malformed request line;
    - a connection over the cap.
  - **Provisioned tokens:** a server configured with the `PUBLISHERS` entry that
    `provision-token` printed accepts the token printed beside it. Both the
    `PUBLISHERS` and `PUBLISHERS_FILE` paths are covered, and with two entries the
    second repository's token is also accepted.
  - **Real publisher:** the finish-line driver for the corpus `Transport`.
  - **Conformance:** both tiers and the Chromium suite pass against the server.
- **Gate:** confirm first that `node:sqlite` needs no flag on `^22.22.2 || >=24.15.0`.
  If it does, stop and bring the decision back.

### V26-3751. Run conformance for both adapters as checks

Lane 1, tier 4, after V26-3750 and V26-3752. Sensor-only. Files:
`.github/workflows/hosted-delivery-viewer.yml`, `.agents/orchestration.json` and
`tests/test_own_deliveries.py`.

Lane 1 already owns `.agents/orchestration.json`, `tests/test_own_deliveries.py` and
`docs/own-deliveries.md` from its tier-1 row, so this item's table row adds nothing
for them.

- **Integration recipe.** Update the recipe in `docs/own-deliveries.md` again for the
  new check ids, keeping its row in `tests/test_own_deliveries.py` green.
- **Workflow pin.** Re-derive the pin in `tests/test_own_deliveries.py` on the
  hosted workflow's single `service` job and its exact command list, to match the
  split jobs.

- Add checks, reading the hosted trigger paths:
  - `hosted.node-test`;
  - `hosted.conformance-node`: the fast tier, run from a bundle the V26-3752 builder
    makes and extracts under `TMPDIR`, against a server built from that same
    extraction.
- Extend `SERVICE_IDS` and the prerequisite assertions in
  `tests/test_own_deliveries.py`.
- Split the CI work into parallel jobs:
  - the fast checks;
  - the Worker's slow tier;
  - the Node server's slow tier;
  - the container: `docker build` of the extracted bundle's Dockerfile, then the
    fast tier against the running container.

  Raise each job's timeout to 30 minutes.
- Locally, Docker is an optional sensor: where it is unavailable, it is reported as
  unavailable, not passed.

### V26-3752. Carry a verified service bundle in each release

Lane 2, tier 2, after V26-3748. Test-first. Files: `scripts/cut-release.py`,
`scripts/release_channel.py`, `tests/test_cut_release.py` and
`tests/test_release_channel.py`.

- Build the bundle from the tracked files under the explicit roots, with sorted
  entries, fixed timestamps and modes, and no ignored or untracked file.
- Write the `.sha256`, recheck it against the written bytes, and add both names to
  `named_outputs`, `EARLIER_OUTPUT` and the module docstring.
- `release_channel.py publish` uploads both, and `render_notes` lists the digest.
- Expose the builder as a function V26-3751's check can call on a checkout.
- Scenarios, in the existing real cut and its fixture runs, with no new real cut:
  - **Reproducibility:** a second cut on a copy of the tree with every mtime changed
    and one mode bit changed gives identical bytes. Every entry's `date_time` and
    `external_attr` equal the declared constants, and entry names are sorted.
  - **Contents:** the bundle contains `hosted/protocol/hosted-delivery-api-1.md` and
    the conformance package's entry point, and its roots equal the workflow's
    trigger paths minus itself.
  - **Exclusions:** an untracked file, an ignored file or bytecode never enters the
    bundle.
  - **Missing root:** the cut is refused with a named code.
  - **Corrupted bundle:** a bundle corrupted between write and recheck is refused
    with a named code.
  - **Later run:** a later run into the same `--out` replaces both bundle files.
  - **Publish:** in `tests/test_release_channel.py`, the dry-run publish lists both
    files, the notes list the digest, and the existing notes fixtures are updated.
- **Ordering.** Its roots include `hosted/protocol/` and `hosted/conformance/`,
  which exist only once V26-3748 has merged, and a missing root refuses the cut. So
  V26-3752 starts after V26-3748 merges, and its validation runs on a base containing
  V26-3748. Pinning explicit roots means it does not wait for V26-3749 or V26-3750.

### V26-3753. Document hosting anywhere

Lane 2, tier 5, after V26-3751 and V26-3752. Sensor-only. Files:
`docs/hosted-deliveries.md`, `skills/delivery-views/SKILL.md`, `AGENTS.md`,
`tests/test_delivery_views_hosted_projection.py` and
`tests/test_declared_mechanics_corpus.py`.

- **"Choose a host":** three paths:
  - Cloudflare;
  - the portable container: bundle verification, `provision-token`, a VPS recipe
    (compose plus a TLS proxy with access logging off), and block-volume guidance
    per cloud;
  - your own implementation, proven by the conformance suite.
- **Unsupported filesystems** are listed.
- **AGENTS.md** says the release carries the service bundle.
- **Before editing,** search the tests that read these files for pinned phrases.
  Re-derive each moved pin in the same change.
- **The skill sentence** goes in with its catalog entry, every selected profile and
  its provenance digests, through `scripts/update-provenance.py`.

## Shared resources

- **Ports:** Miniflare, the Node server and Playwright take free loopback ports
  through the shared launcher. No fixed port.
- **Chromium:** the checks share the locked Playwright browser. Install it only with
  `PLAYWRIGHT_SKIP_BROWSER_GC=1`.
- **Docker:** the container check runs only in CI, or locally where Docker exists.
  Images are tagged per run and removed by that run.
- **Processes:** servers started by tests are stopped by their recorded pid, never
  by pattern.
- **Files:** lanes 1 and 2 share none until V26-3753, which waits for both.
  - V26-3752 waits on V26-3748, because its bundle roots are V26-3748's new folders.
  - V26-3751 waits on V26-3752, because `hosted.conformance-node` calls V26-3752's builder.
- **Fresh base checks:** V26-3752's roots-equal-triggers row compares with the
  workflow at the base. Its final validation needs a fresh base check against a
  base containing V26-3748, and against any later change to the workflow's paths.
- **Full suite:** the full Python suite runs through the shared slot script, once per
  hand-off head.

## Sensors

- **Service:** `hosted.install`, `hosted.typecheck`, `hosted.build`, `hosted.test`,
  `hosted.browser`, and the new `hosted.conformance`, `hosted.node-test` and
  `hosted.conformance-node`. The service CI job runs the slow tier and the container.
- **Python:** `tests.test_cut_release`, `tests.test_release_channel`,
  `tests.test_product_release`,
  `tests.test_provenance`, `tests.test_own_deliveries`,
  `tests.test_delivery_views_hosted_projection`, `tests.test_declared_mechanics_corpus`
  and `scripts/validate-registry.py`.
- **No-regression:** `tests.test_delivery_views_hosted` and
  `tests.test_run_delivery_hosted`, unchanged.
- **Before any tag:** the full local suite on macOS with Python 3.11, with
  `--budget tests/suite-budget.json`.

## Approval handoffs

1. The operator approves this plan, after plan review.
2. Tracker items are created for V26-3748 to V26-3753 (`create-linear-ticket`).
3. **Release:**
   - the operator tags the candidate release carrying the bundle;
   - the candidate is applied in one adopter;
   - the final is tagged on the same commit.
4. **Live acceptance:** the operator deploys the portable container on one real
   non-Cloudflare host, records exact bundle and image digests, and runs the live
   acceptance scenarios in `docs/hosted-deliveries.md` against it with an installed
   adopter publishing over HTTPS.

## Risks

- **`node:sqlite`:** it may still need a flag or be unstable on a supported Node
  line. V26-3750 checks this before anything else.
- **Durable Object ordering:** the core may lean on input-gate ordering beyond what
  the `MetadataStore` contract states. V26-3749 makes any such reliance explicit, or
  removes it.
- **Network filesystems:** operators may mount one despite the docs. The exclusive
  SQLite lock helps on some of them but is not relied on.
- **Hook drift:** a third consumer of `build-assets.mjs` makes it costlier.
  `hosted.build` stays among the readers of `skills/delivery-views/assets/**`.
