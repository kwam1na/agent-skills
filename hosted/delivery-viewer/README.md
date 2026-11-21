# Hosted delivery ingestion

Private Worker + SQLite Durable Object + private R2 snapshot storage. This package
implements ingestion, committed read APIs and the trusted read-only browser viewer.
Local runtime tests are not production or installed-adopter evidence.

Use Node 22.22.2+ or 24.15+ (an even, supported Node release), then `npm ci`.
Run `npm test`, `npm run typecheck`, and `npm run build`. The build is a Wrangler
dry run and deploys nothing. `generate` compiles the corpus's fixed JSON Schema
2020-12 with Ajv standalone at build time and generates bindings from
`wrangler.jsonc`; the Worker never compiles schemas or uses eval/new Function.
The package consumes the shared fixture manifest directly at generation time.
Root dependency manifests and product archives are independent of this package.

Cloudflare runtime references: [SQLite transactions](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#transactions),
[Workers testing](https://developers.cloudflare.com/workers/testing/vitest-integration/write-your-first-test/),
and [generated types](https://developers.cloudflare.com/workers/languages/typescript/#generate-types).

## Transport contract: hosted-delivery-api/1

All IDs in URLs are literal, unencoded ASCII. Public IDs and owner IDs are
64 lowercase hexadecimal characters, generated from 32 cryptographically random
bytes by the publisher under its identity lock. Repository IDs match
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; provision a random ID per repository. Delivery
keys match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and are the canonical local delivery
identity. Creation permanently binds repository, delivery key and public ID.
No route lists deliveries, repositories, objects or history. Query strings,
encoded paths and alternate methods are refused. Authenticated endpoints require
`Authorization: Bearer <repository-publishing-token>` before DO lookup or body
parsing. No redirects are returned. Use HTTPS with an explicit service origin.

The `PUBLISHERS` Worker secret is a JSON object mapping repository IDs to SHA-256
lowercase hex digests of independent, high-entropy bearer publishing tokens.
Provision tokens with at least 256 random bits, encoded base64url without padding
(43–128 characters accepted). Digests are compared with Node's constant-time
primitive in Workers. The Worker has no Cloudflare management credential. Neither
source configuration nor storage contains the original token. Provisioning and
deployment are separate operator work; no resources have been deployed here.

`POST /api/v1/repos/{repo}/deliveries/{id}/session`

Body: `{"mode":"acquire","deliveryKey":"APP-1","ownerId":"<64hex>"}`.
All control bodies require exact `Content-Type: application/json`, no content
encoding, and no extra keys. Session bodies are limited to 2048 bytes.
`acquire` binds a new delivery at epoch 1, sequence 0, lease expiry now + 180s.
An exact initial acquisition retry returns that session without extending it.
Another owner cannot acquire an existing identity. `resume` requires `epoch`
and matching `ownerId`/`deliveryKey`. While active it only returns the session;
after expiry it explicitly advances the matching epoch and resets sequence to 0.
`takeover` requires the current `epoch`, a new owner ID and an expired lease.
Neither operation can silently displace an active writer or accept an old epoch.
A fenced owner must stop ordinary retries. An operator may explicitly resume or
take over an expired session after inspecting status.

Response: `protocolVersion`, `deliveryKey`, `ownerId`, `epoch`, `sequence`,
`leaseUntil` (Unix milliseconds), `receipt` (last committed receipt or null).
`GET /api/v1/repos/{repo}/deliveries/{id}/status` returns that same authenticated
shape. Inspect it after a lost session response; match persisted owner/delivery
identity before adopting its epoch. Public metadata never exposes those private
identity fields. A current receipt may belong to the previous epoch while a new
epoch has sequence 0; publish a new sequence in the current epoch to confirm it.

`POST /api/v1/repos/{repo}/deliveries/{id}/snapshots`

The entire body is the raw UTF-8 `hosted-delivery-snapshot/3` snapshot, at most
16 MiB. It is not wrapped in another JSON object. Required headers:

- `Content-Type: application/json` (no encoding)
- `X-Writer-Epoch: <positive safe integer>`
- `X-Snapshot-Sequence: <positive safe integer>`
- `X-Snapshot-Digest: <lowercase SHA-256 of the exact body bytes>`
- `X-Renderer-Version: delivery-line-map/3`

The receiver counts streamed bytes even without an honest Content-Length,
rejects nesting deeper than 64, malformed UTF-8/JSON, unknown top-level fields,
other versions, unsupported renderers, over-long texts, lists and objects, a
credential-named field at any depth, unsafe links and numbers beyond JavaScript
safe precision. The body is the local live view's own cut, so it accepts the
record's prose as written, paths and commands included, and public markup as
data. Publishers still scan every projected string for their configured secret
values before transport; no service scanner claims to recognize arbitrary prose
secrets.

Only a successful immutable R2 write followed by a synchronous metadata transaction
advances the pointer. The transaction rechecks persisted epoch, lease and sequence
after R2 awaits. Each accepted sequence has one durable receipt; a retry with the
same epoch, sequence and digest returns its original receipt without refreshing
freshness, even after a later commit or expired/displaced writer. Reusing a
sequence with different bytes conflicts. A superseded, never-accepted sequence
conflicts. Failed commits leave the prior pointer intact; staged objects are not
public. Unreferenced immutable objects may remain; no bucket lifecycle deletion
is configured. Committed completed snapshots must be retained.

A receipt contains `protocolVersion: hosted-delivery-api/1`,
`rendererVersion: delivery-line-map/3`, `epoch`, `sequence`, `digest`,
`sourceFingerprint`, `publishedAt`, `sourceConfirmedAt`. Both times are server
Unix milliseconds of original acceptance. Sequence is strictly increasing within
an epoch; compare epoch first. Do not replace current state with an old retry's
receipt. Authenticated status reconciles the current session and pointer.

`POST /api/v1/repos/{repo}/deliveries/{id}/confirm`

Same four X-* headers as snapshot commit; body is only
`{"sourceFingerprint":"<64hex>"}`, at most 1024 bytes. A successful fresh source
check may confirm the exact current epoch/sequence/digest/fingerprint, extending
its lease by 180s and updating source confirmation. A transport heartbeat, status,
active resume, receipt retry or failed source collection never refreshes it.
Response: `protocolVersion`, `sourceConfirmedAt`, `leaseUntil`, original `receipt`.

## Anonymous viewer API

`GET /d/{id}/current` returns the committed receipt fields, current
`sourceConfirmedAt`, `serverNow`, `staleAfterMs: 120000`, and `stale` computed by the server.
Poll every 5s. Keep the existing readable view on failures. Metadata has no-store.
The UI may exempt an acknowledged terminal cut from stale presentation by reading
its delivery outcome; server freshness never changes the recorded outcome.

`GET /d/{id}/snapshots/{digest}` reads only the currently committed pointer and
returns its exact JSON bytes. A pointer change between metadata and data can
produce 404; poll metadata again and retain the previously rendered view. Old
committed or staged object paths are not routable. This version uses no-store for
snapshot responses as well, keeping capability content out of shared caches.
All routes (including errors) set no-referrer, noindex/nofollow/noarchive,
nosniff, restrictive CSP and no-store. The trusted HTML shell uses its
own narrow asset CSP. No CORS/write/viewer controls are offered.

## Errors and retry advice

Errors are only `{"error":"<fixed_code>"}`; no raw exception, URL, bearer,
capability, header or payload is logged. Worker observability is disabled so
request paths are not collected by default. Do not enable request logging that
records capability URLs. Applications must not log fetch URLs on this service.

| HTTP | Codes | Publisher action |
| --- | --- | --- |
| 400 | invalid_identifier, invalid_sequence, invalid_envelope, invalid_json, invalid_projection | Fix input; keep prior remote copy. |
| 401 | unauthorized | Correct repository-scoped credentials. |
| 404 | not_found | Generic unknown/binding mismatch; inspect local identity; never replace it automatically. |
| 405 | method_not_allowed | Fix client method. |
| 409 | lease_held, resume_required, writer_fenced, lease_expired, sequence_conflict, sequence_superseded, confirmation_conflict | Reconcile status; stop fenced retries; use explicit permitted resume only. |
| 413 | body_too_large | Reduce projection; keep prior copy. |
| 415 | unsupported_content_type, unsupported_encoding | Fix headers; JSON data only. |
| 422 | unsupported_renderer, digest_mismatch | Fix version/digest, hash exact bytes. |
| 503 | temporarily_unavailable | Retry with bounded jitter/backoff; inspect receipt after ambiguous acceptance. |

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
commits run, and injects R2 failure. A SQLite abort trigger tests rollback after the
receipt insert and before pointer update. Eviction tests use the runtime's actual
eviction helper and retain session/receipt/binding state. Shared schema and
boundary fixtures run through HTTP handling. These checks prove local runtime
behavior only. No hosted CI, deployment, DNS, live browser or installed-archive
qualification is claimed by this package.


## Trusted browser viewer

`GET /d/{id}` serves a fixed shell only after a committed snapshot exists. The
shell embeds no publisher data and loads content-addressed external JS/CSS. The
build consumes `line-map.js`, `run-summary.js` and their styles directly from the
corpus. Checked narrow store hooks replace the local HTML refresh mechanism with
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
loopback port with synthetic repository credentials. The harness disables request
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
