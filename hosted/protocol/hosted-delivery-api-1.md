# hosted-delivery-api/1

The protocol between a delivery publisher and a hosted delivery service, and the
anonymous read surface the service gives a browser. Any origin that behaves as
this document says is a valid hosted origin: the Cloudflare Worker in
`hosted/delivery-viewer/` is one implementation. The black-box suite in
`hosted/conformance/` checks a running origin against it (see
[Conformance](#conformance)).

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. A
service is the thing that answers on an origin; a publisher is the client that
writes to it.

## Versions and bounds

| Name | Value |
| --- | --- |
| API | `hosted-delivery-api/1` |
| Snapshot schema | `hosted-delivery-snapshot/3`, rendered by `delivery-line-map/3` |
| Retained snapshot schema | `hosted-delivery-snapshot/2`, rendered by `delivery-line-map/2` |
| Lease | 180 000 ms |
| Staleness | 120 000 ms |
| Snapshot body | at most 16 MiB (16 777 216 bytes) |
| Session body | at most 2048 bytes |
| Confirmation body | at most 1024 bytes |
| JSON nesting | at most 64 levels |

The `hosted-delivery-snapshot/3` schema is
`skills/delivery-views/references/hosted-snapshot.schema.json`; the retained
`hosted-delivery-snapshot/2` schema is
`hosted/delivery-viewer/compat/v2/hosted-snapshot.schema.json`. A change to any
version moves this document, the conformance suite and every server in the same
change.

## Identifiers

All IDs in URLs are literal, unencoded ASCII.

- **Public IDs and owner IDs** are 64 lowercase hexadecimal characters,
  generated from 32 cryptographically random bytes by the publisher under its
  identity lock.
- **Repository IDs** match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Provision a random
  ID per repository.
- **Delivery keys** match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and are the
  canonical local delivery identity.
- **Digests and source fingerprints** are 64 lowercase hexadecimal characters.
  A snapshot digest is the SHA-256 of the exact body bytes.

Creation permanently binds repository, delivery key and public ID.

## Routes

| Method | Path | Authenticated |
| --- | --- | --- |
| `POST` | `/api/v1/repos/{repo}/deliveries/{id}/session` | yes |
| `GET` | `/api/v1/repos/{repo}/deliveries/{id}/status` | yes |
| `POST` | `/api/v1/repos/{repo}/deliveries/{id}/snapshots` | yes |
| `POST` | `/api/v1/repos/{repo}/deliveries/{id}/confirm` | yes |
| `GET` | `/d/{id}` | no |
| `GET` | `/d/{id}/current` | no |
| `GET` | `/d/{id}/snapshots/{digest}` | no |
| `GET` | `/assets/{content address}` | no |

Nothing else is routed:

- No route lists deliveries, repositories, objects or history. Every other path
  answers 404 `not_found`. That includes `/`, which is what a health check reads.
- A request with a query string, or a path containing `%`, answers 404
  `not_found`, even on an otherwise valid route. Encoded paths are never decoded
  into a route. A path is read as a URL path, so dot segments are resolved and
  `\` is read as `/` before routing.
- An authenticated route answers 405 `method_not_allowed` to any other method,
  `OPTIONS` and `HEAD` included, once the request has authenticated. An
  anonymous route answers 404 `not_found` to any method but `GET`.
- No redirects are returned.
- Routing and identity MUST NOT read `Host`, `X-Forwarded-Host`, `Forwarded` or
  any other `X-Forwarded-*` header. The same request under any `Host` gets the
  same response and the same receipt.

Use HTTPS with an explicit service origin.

## Authentication

Authenticated routes require `Authorization: Bearer <repository-publishing-token>`
before any delivery lookup or body parsing. The token matches
`[A-Za-z0-9_-]{43,128}`. Provision tokens with at least 256 random bits encoded
as unpadded base64url (43–128 characters).

The service holds, for each repository it accepts, the lowercase SHA-256 digest
of that repository's token. It compares the digest of the presented token with
the stored one in constant time. A token that does not match, a repository with
no stored digest, and a malformed header all answer 401 `unauthorized`. Each
check reads current storage. Corrupt records and failed reads deny access. A
service MUST honour every repository it is configured with, not only the first.

In the Cloudflare Worker, the digest lives in the private R2 bucket under
`_publishers/v1/<repositoryId>`. A stored SHA-256 digest activates that
repository, and a stored `revoked` marker disables it. An absent object uses
the original `PUBLISHERS` Worker secret, a JSON map of repository IDs to
digests. See the
[operator procedure](../../docs/hosted-deliveries.md#manage-a-publisher) to
add, rotate or revoke one publisher.

## Request bodies

All control bodies, and snapshot bodies, require exact
`Content-Type: application/json` (compared without regard to case, with no
parameters) and no `Content-Encoding` header of any value; otherwise 415
`unsupported_content_type` or `unsupported_encoding`. Control bodies are JSON
objects with no extra keys.

The receiver counts streamed bytes even without an honest `Content-Length`. A
declared `Content-Length` over the limit, or a body whose streamed bytes pass
it, answers 413 `body_too_large`, never a closed connection. JSON nested deeper
than 64 levels, malformed JSON and malformed UTF-8 answer 400 `invalid_json`.

### Order of checks

When a request has several faults, the first in this order is the one
reported:

1. the route: a query string, an encoded path or an unrouted path (404);
2. the repository ID in the path (400 `invalid_identifier`);
3. authentication (401);
4. the method (405);
5. for a commit or a confirmation, the envelope headers: `X-Renderer-Version`
   (422 `unsupported_renderer`), then `X-Writer-Epoch` and
   `X-Snapshot-Sequence` (400 `invalid_sequence`), then `X-Snapshot-Digest`
   (400 `invalid_identifier`);
6. the body: content type and encoding (415), size (413);
7. for a commit, the digest of the received bytes (422 `digest_mismatch`), then
   JSON (400 `invalid_json`), then the snapshot rules (400 `invalid_projection`);
   for a control body, JSON (400 `invalid_json`), its shape (400
   `invalid_envelope`), its identifiers (400 `invalid_identifier`) and epoch
   (400 `invalid_sequence`);
8. the delivery: its binding (404), then the rules of the operation (409, or
   422 `unsupported_renderer` when a commit names another renderer than the
   delivery's first accepted snapshot).

## Sessions

`POST /api/v1/repos/{repo}/deliveries/{id}/session`

Body: `{"mode":"acquire","deliveryKey":"APP-1","ownerId":"<64hex>"}`, at most
2048 bytes. `resume` and `takeover` also carry `epoch`, a positive safe integer;
`acquire` carries no `epoch`. Any other mode is `invalid_envelope`.

- `acquire` binds a new delivery at epoch 1, sequence 0, lease expiry now +
  180 s. An exact initial acquisition retry returns that session without
  extending it. Another owner cannot acquire an existing identity (409
  `lease_held`). Once the epoch is past 1 or the lease has expired, an
  acquisition by the current owner answers 409 `resume_required`.
- `resume` requires `epoch` and matching `ownerId`/`deliveryKey`. While active
  it only returns the session; after expiry it explicitly advances the matching
  epoch and resets sequence to 0.
- `takeover` requires the current `epoch`, a new owner ID and an expired lease.
  A takeover of an active lease answers 409 `lease_held`.

Neither operation can silently displace an active writer or accept an old
epoch: an epoch other than the current one, or a resume by another owner,
answers 409 `writer_fenced`. A fenced owner must stop ordinary retries. An
operator may explicitly resume or take over an expired session after
inspecting status. A different delivery key, or another repository, on a bound
public ID answers 404 `not_found`, as does a resume or takeover of an unbound
one.

Response: `protocolVersion`, `deliveryKey`, `ownerId`, `epoch`, `sequence`,
`leaseUntil` (Unix milliseconds), `receipt` (last committed receipt or null).

`GET /api/v1/repos/{repo}/deliveries/{id}/status` returns that same
authenticated shape. Inspect it after a lost session response; match persisted
owner/delivery identity before adopting its epoch. Public metadata never
exposes those private identity fields. A current receipt may belong to the
previous epoch while a new epoch has sequence 0; publish a new sequence in the
current epoch to confirm it.

## Snapshot commits

`POST /api/v1/repos/{repo}/deliveries/{id}/snapshots`

The body is a raw UTF-8 snapshot of at most 16 MiB. Pair
`hosted-delivery-snapshot/2` with `delivery-line-map/2`, or
`hosted-delivery-snapshot/3` with `delivery-line-map/3`. The first accepted
snapshot fixes the renderer for that delivery link. It is not wrapped in
another JSON object. Required headers:

- `Content-Type: application/json` (no encoding)
- `X-Writer-Epoch: <positive safe integer>`
- `X-Snapshot-Sequence: <positive safe integer>`
- `X-Snapshot-Digest: <lowercase SHA-256 of the exact body bytes>`
- `X-Renderer-Version` naming the matching renderer

Epoch and sequence are decimal with no sign and no leading zero, at most 16
digits, and within JavaScript safe precision.

The receiver rejects nesting deeper than 64, malformed UTF-8/JSON, unknown
top-level fields, other versions, unsupported renderers, over-long texts, lists
and objects, a credential-named field at any depth, unsafe links and numbers
beyond JavaScript safe precision. The v2 validator retains the deployed v2
privacy rules. The v3 body is the local live view's own cut, so it accepts the
record's prose as written, paths and commands included, and public markup as
data. Publishers still scan every projected string for their configured secret
values before transport; no service scanner claims to recognize arbitrary prose
secrets.

Only a successful immutable blob write followed by a synchronous metadata
transaction advances the pointer. The transaction rechecks persisted epoch,
lease and sequence after the blob write. Each accepted sequence has one durable
receipt; a retry with the same epoch, sequence and digest returns its original
receipt without refreshing freshness or the lease, even after a later commit or
expired/displaced writer. Reusing a sequence with different bytes conflicts
(409 `sequence_conflict`). A superseded, never-accepted sequence conflicts (409
`sequence_superseded`). A commit at an epoch other than the current one is 409
`writer_fenced`; one after the lease expired is 409 `lease_expired`. Failed
commits leave the prior pointer intact; staged objects are not public.
Unreferenced immutable objects may remain; no deletion of them is configured.
Committed completed snapshots must be retained.

An accepted commit extends the lease to its acceptance time + 180 s.

## Receipts

A receipt contains `protocolVersion: hosted-delivery-api/1`, the accepted
`rendererVersion`, `epoch`, `sequence`, `digest`, `sourceFingerprint`,
`publishedAt`, `sourceConfirmedAt`, and nothing else. Both times are server
Unix milliseconds of original acceptance. Sequence is strictly increasing
within an epoch; compare epoch first. Do not replace current state with an old
retry's receipt. Authenticated status reconciles the current session and
pointer.

## Confirmation

`POST /api/v1/repos/{repo}/deliveries/{id}/confirm`

Same four `X-*` headers as snapshot commit; body is only
`{"sourceFingerprint":"<64hex>"}`, at most 1024 bytes. A successful fresh
source check may confirm the exact current epoch/sequence/digest/fingerprint
and renderer, extending its lease by 180 s from the confirmation and updating
source confirmation. Anything else about the current cut, or no cut at all,
answers 409 `confirmation_conflict`; a fenced or expired writer answers 409
`writer_fenced` or `lease_expired`. A transport heartbeat, status, active
resume, receipt retry or failed source collection never refreshes it.

Response: `protocolVersion`, `sourceConfirmedAt`, `leaseUntil`, original
`receipt`.

## Anonymous viewer API

`GET /d/{id}/current` returns the committed receipt fields, current
`sourceConfirmedAt`, `serverNow`, `staleAfterMs: 120000`, and `stale` computed
by the server: true once `serverNow - sourceConfirmedAt` reaches 120 000. Poll
every 5 s. Keep the existing readable view on failures. Metadata has
`no-store`. The UI may exempt an acknowledged terminal cut from stale
presentation by reading its delivery outcome; server freshness never changes
the recorded outcome. Before the first commit, and for an unknown ID, it
answers 404 `not_found`.

`GET /d/{id}/snapshots/{digest}` reads only the currently committed pointer and
returns its exact JSON bytes as `application/json`. A pointer change between
metadata and data can produce 404; poll metadata again and retain the
previously rendered view. Old committed or staged object paths are not
routable. This version uses `no-store` for snapshot responses as well, keeping
capability content out of shared caches.

`GET /d/{id}` serves the trusted viewer shell, `text/html; charset=utf-8`, only
after a committed snapshot exists (404 `not_found` before). The shell embeds no
publisher data and loads content-addressed external JS/CSS from `/assets/`.
Asset routes match only exact build hashes and are served with
`Cache-Control: public, max-age=31536000, immutable`.

The read routes serve acknowledged delivery digests only. A read of an unknown
public ID creates no state. No CORS, write or viewer controls are offered.

## Response headers

Every response, errors included, carries:

| Header | Value |
| --- | --- |
| `Cache-Control` | `no-store` (assets: `public, max-age=31536000, immutable`) |
| `Referrer-Policy` | `no-referrer` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Robots-Tag` | `noindex, nofollow, noarchive` |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` |

The trusted HTML shell uses its own narrow asset CSP instead:
`default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; connect-src 'self'; img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`.

No response carries `Access-Control-*`, `Set-Cookie` or `Location`.

## Errors and retry advice

Errors are only `{"error":"<fixed_code>"}` as `application/json`; no raw
exception, URL, bearer, capability, header or payload is logged. Do not enable
request logging that records capability URLs. Applications must not log fetch
URLs on this service.

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

An unexpected failure, a storage failure included, answers 503
`temporarily_unavailable`.

### Failures before a request reaches the service

A server that terminates HTTP itself, rather than behind a platform that does,
meets some failures before any route is read: a header block that does not
arrive in time, a body that does not arrive in time, a header block over the
server's limit, a malformed request line, and a connection over the server's
connection cap. For each of these the socket closes with no bytes written: no
status line, and none of the platform's default 400, 408 or 431 responses. A
server sets header, request and keep-alive timeouts and a connection cap of its
own choosing. A platform in front of the service, such as Cloudflare's edge for
the Worker, answers these itself.

## Conformance

`hosted/conformance/` is a Node package with no dependencies. It runs against
one origin with two repository credentials:

```bash
node hosted/conformance/cli.mjs --origin https://deliveries.example --credentials credentials.json
node hosted/conformance/cli.mjs --origin https://deliveries.example --credentials credentials.json --slow
```

`credentials.json` holds `[{"repository": "<id>", "token": "<token>"}, ...]`,
two repositories the origin accepts. The suite creates deliveries under fresh
random public IDs and never removes them, so point it at an origin whose
deliveries are disposable. The fast tier covers every route, error code,
header rule, size bound, idempotency rule, fencing rule and the anonymous read
surface in seconds. `--slow` adds lease expiry, resume, takeover and staleness,
waited out in real time (about three and a half minutes). It adds no test
route, clock hook or header: it holds a server to this document and nothing
else.
