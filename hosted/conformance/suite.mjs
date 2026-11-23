// The black-box conformance suite for hosted-delivery-api/1. It speaks only HTTP to one origin, with two repository
// credentials, and asks nothing of the server beyond the spec: no test route, clock hook or header.
// The fast tier runs in seconds; the slow tier waits real time for lease expiry (180 s) and staleness (120 s).
import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createClient, json } from "./client.mjs";
import {
  API_VERSION, LEASE_MS, LEGACY_RENDERER_VERSION, LEGACY_SNAPSHOT_VERSION, MAX_CONFIRM_BYTES, MAX_NESTING,
  MAX_SESSION_BYTES, MAX_SNAPSHOT_BYTES, RENDERER_VERSION, SNAPSHOT_VERSION, STALE_MS,
} from "./versions.mjs";

export * from "./versions.mjs";

export const DEFAULT_FIXTURES = new URL("../../tests/fixtures/delivery-views/hosted/", import.meta.url);
const LEGACY_FIXTURE = new URL("./fixtures/snapshot-v2.json", import.meta.url);

export const BASE_HEADERS = Object.freeze({
  "cache-control": "no-store",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
  "x-robots-tag": "noindex, nofollow, noarchive",
  "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
});
export const SHELL_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; " +
  "connect-src 'self'; img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; " +
  "frame-ancestors 'none'";
export const ASSET_CACHE = "public, max-age=31536000, immutable";
const RECEIPT_KEYS = ["digest", "epoch", "protocolVersion", "publishedAt", "rendererVersion", "sequence",
  "sourceConfirmedAt", "sourceFingerprint"];
const SESSION_KEYS = ["deliveryKey", "epoch", "leaseUntil", "ownerId", "protocolVersion", "receipt", "sequence"];
const CURRENT_KEYS = [...RECEIPT_KEYS, "serverNow", "stale", "staleAfterMs"].sort();

class Failure extends Error {}
function check(condition, message) {
  if (!condition) throw new Failure(message);
}
const show = (value) => JSON.stringify(value);
function same(actual, expected, message) {
  check(show(canonical(actual)) === show(canonical(expected)), `${message}: expected ${show(expected)}, got ${show(actual)}`);
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}
function described(response) {
  if (response.closed) return `the connection closed (${response.error})`;
  return `${response.status} ${response.text.slice(0, 160)}`;
}
/** A response with this status and exactly `{"error": code}` as its JSON body. */
function refused(response, status, code, step) {
  check(!response.closed && response.status === status && response.text === JSON.stringify({ error: code }) &&
    String(response.headers["content-type"]).split(";")[0] === "application/json",
    `${step}: expected ${status} {"error":"${code}"}, got ${described(response)}`);
}
function ok(response, step) {
  check(!response.closed && response.status === 200, `${step}: expected 200, got ${described(response)}`);
  const body = json(response);
  check(body !== undefined, `${step}: the 200 body is not JSON`);
  return body;
}
function isReceipt(value, step) {
  check(value && typeof value === "object", `${step}: no receipt`);
  same(Object.keys(value).sort(), RECEIPT_KEYS, `${step}: receipt fields`);
  check(value.protocolVersion === API_VERSION, `${step}: receipt protocolVersion`);
  for (const key of ["epoch", "sequence", "publishedAt", "sourceConfirmedAt"])
    check(Number.isSafeInteger(value[key]) && value[key] >= 0, `${step}: receipt ${key} is not a safe integer`);
  check(value.publishedAt === value.sourceConfirmedAt, `${step}: a receipt's two times are both its acceptance time`);
  return value;
}
function isSession(value, step) {
  same(Object.keys(value).sort(), SESSION_KEYS, `${step}: session fields`);
  check(value.protocolVersion === API_VERSION, `${step}: session protocolVersion`);
  check(Number.isSafeInteger(value.leaseUntil), `${step}: leaseUntil`);
  if (value.receipt !== null) isReceipt(value.receipt, `${step} receipt`);
  return value;
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const hex = (bytes = 32) => randomBytes(bytes).toString("hex");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function loadFixtures(root) {
  const base = root instanceof URL ? root : pathToFileURL(`${resolve(root)}/`);
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", base), "utf8"));
  const cases = manifest.cases.map((row) => ({ ...row, body: JSON.parse(readFileSync(new URL(row.file, base), "utf8")) }));
  const valid = cases.filter((row) => row.valid);
  check(valid.length > 0 && valid.every((row) => row.body.schemaVersion === SNAPSHOT_VERSION),
    `the fixtures hold no valid ${SNAPSHOT_VERSION} snapshot`);
  return { valid, invalid: cases.filter((row) => !row.valid), legacy: JSON.parse(readFileSync(LEGACY_FIXTURE, "utf8")) };
}

/** Everything a scenario needs: the client, the credentials and the requests the protocol is made of. */
function harness(client, credentials, fixtures, run) {
  const [first, second] = credentials;
  const auth = (credential) => ({ Authorization: `Bearer ${credential.token}` });
  const api = (credential, id, action, repository = credential.repository) =>
    `/api/v1/repos/${repository}/deliveries/${id}/${action}`;
  let counter = 0;
  /** A distinct valid snapshot: the base fixture under a fingerprint of its own. */
  function snapshot(fixture = fixtures.valid[0].body) {
    const body = structuredClone(fixture);
    body.sourceFingerprint = sha256(`${run}:${++counter}`);
    const bytes = Buffer.from(JSON.stringify(body));
    return { bytes, digest: sha256(bytes), fingerprint: body.sourceFingerprint };
  }
  const control = (credential, value) => ({ ...auth(credential), "Content-Type": "application/json", ...value });
  function session(credential, id, body, { headers = {}, label = "session" } = {}) {
    return client.send({ method: "POST", path: api(credential, id, "session"), headers: control(credential, headers),
      body: Buffer.from(JSON.stringify(body)), label });
  }
  const acquire = (credential, id, owner, { deliveryKey = "APP-1", headers, label = "acquire" } = {}) =>
    session(credential, id, { mode: "acquire", deliveryKey, ownerId: owner }, { headers, label });
  function envelope(credential, { epoch = 1, sequence, digest, renderer = RENDERER_VERSION }, headers = {}) {
    return { ...control(credential, {}), "X-Writer-Epoch": String(epoch), "X-Snapshot-Sequence": String(sequence),
      "X-Snapshot-Digest": digest, "X-Renderer-Version": renderer, ...headers };
  }
  function commit(credential, id, cut, { epoch = 1, sequence, renderer, headers, label = "commit" }) {
    return client.send({ method: "POST", path: api(credential, id, "snapshots"),
      headers: envelope(credential, { epoch, sequence, digest: cut.digest, renderer }, headers), body: cut.bytes, label });
  }
  function confirm(credential, id, cut, { epoch = 1, sequence, renderer, fingerprint = cut.fingerprint, label = "confirm" }) {
    return client.send({ method: "POST", path: api(credential, id, "confirm"),
      headers: envelope(credential, { epoch, sequence, digest: cut.digest, renderer }),
      body: Buffer.from(JSON.stringify({ sourceFingerprint: fingerprint })), label });
  }
  const status = (credential, id, { headers = {}, label = "status" } = {}) =>
    client.send({ path: api(credential, id, "status"), headers: { ...auth(credential), ...headers }, label });
  const read = (path, { headers = {}, label = "read", method = "GET" } = {}) => client.send({ method, path, headers, label });
  /** A new delivery held by `credential`, at epoch 1 with nothing committed. */
  async function begin(credential = first, owner = hex()) {
    const id = hex();
    const held = isSession(ok(await acquire(credential, id, owner), "acquire a new delivery"), "acquire a new delivery");
    return { id, owner, session: held };
  }
  async function committed(credential, id, sequence, { epoch = 1, cut = snapshot(), step = `commit ${sequence}` } = {}) {
    const receipt = isReceipt(ok(await commit(credential, id, cut, { epoch, sequence }), step), step);
    same([receipt.epoch, receipt.sequence, receipt.digest, receipt.sourceFingerprint, receipt.rendererVersion],
      [epoch, sequence, cut.digest, cut.fingerprint, RENDERER_VERSION], `${step}: receipt identity`);
    return { cut, receipt };
  }
  return { first, second, auth, api, snapshot, session, acquire, commit, confirm, status, read, begin, committed,
    envelope, control, fixtures, client };
}

async function roundTrip(h, credential) {
  const { id, owner, session: held } = await h.begin(credential);
  same([held.deliveryKey, held.ownerId, held.epoch, held.sequence, held.receipt], ["APP-1", owner, 1, 0, null],
    "a new acquisition binds epoch 1 at sequence 0");
  refused(await h.read(`/d/${id}/current`), 404, "not_found", "current before any commit");
  refused(await h.read(`/d/${id}`), 404, "not_found", "the page before any commit");
  const retried = isSession(ok(await h.acquire(credential, id, owner), "an exact acquisition retry"), "an exact acquisition retry");
  same(retried, held, "an exact acquisition retry returns that session without extending it");
  const one = await h.committed(credential, id, 1);
  const two = await h.committed(credential, id, 2);
  const state = isSession(ok(await h.status(credential, id), "status"), "status");
  same([state.epoch, state.sequence, state.receipt, state.ownerId, state.deliveryKey], [1, 2, two.receipt, owner, "APP-1"],
    "status reports the current session and pointer");
  const confirmed = ok(await h.confirm(credential, id, two.cut, { sequence: 2 }), "confirm");
  same(Object.keys(confirmed).sort(), ["leaseUntil", "protocolVersion", "receipt", "sourceConfirmedAt"], "confirm fields");
  same(confirmed.receipt, two.receipt, "confirm returns the original receipt");
  check(confirmed.protocolVersion === API_VERSION, "confirm protocolVersion");
  check(confirmed.sourceConfirmedAt >= two.receipt.sourceConfirmedAt, "confirmation does not move source time backwards");
  check(confirmed.leaseUntil === confirmed.sourceConfirmedAt + LEASE_MS, "confirm extends the lease by 180 s from its own time");
  const current = ok(await h.read(`/d/${id}/current`), "current");
  same(Object.keys(current).sort(), CURRENT_KEYS, "current fields");
  same({ ...current, serverNow: 0, stale: false, sourceConfirmedAt: 0, staleAfterMs: 0 },
    { ...two.receipt, serverNow: 0, stale: false, sourceConfirmedAt: 0, staleAfterMs: 0 }, "current carries the committed receipt");
  same([current.sourceConfirmedAt, current.staleAfterMs, current.stale], [confirmed.sourceConfirmedAt, STALE_MS, false],
    "current carries the confirmation and is fresh");
  const bytes = await h.read(`/d/${id}/snapshots/${two.cut.digest}`);
  check(!bytes.closed && bytes.status === 200 && bytes.bytes.equals(two.cut.bytes), "the current snapshot is its exact bytes");
  check(String(bytes.headers["content-type"]).split(";")[0] === "application/json", "a snapshot is served as JSON");
  refused(await h.read(`/d/${id}/snapshots/${one.cut.digest}`), 404, "not_found", "an earlier committed snapshot");
  const page = await h.read(`/d/${id}`);
  check(!page.closed && page.status === 200 && /^text\/html/.test(String(page.headers["content-type"])), "the page after a commit");
}

async function hostHeaders(h) {
  const foreign = { Host: "conformance.invalid", "X-Forwarded-Host": "forwarded.invalid", "X-Forwarded-Proto": "http",
    "X-Forwarded-For": "203.0.113.9", Forwarded: "host=forwarded.invalid;proto=http" };
  const owner = hex(), id = hex();
  const held = isSession(ok(await h.acquire(h.first, id, owner, { headers: foreign }), "acquire under a foreign Host"), "acquire");
  same([held.epoch, held.sequence], [1, 0], "acquire under a foreign Host");
  const cut = h.snapshot();
  const receipt = isReceipt(ok(await h.commit(h.first, id, cut, { sequence: 1, headers: { "X-Forwarded-Host": "other.invalid" } }),
    "commit under a forwarded host"), "commit");
  const plain = ok(await h.status(h.first, id), "status under the origin's own Host");
  const hostile = ok(await h.status(h.first, id, { headers: foreign }), "status under a foreign Host");
  same(hostile, plain, "status is the same under any Host");
  same(plain.receipt, receipt, "the receipt is the one committed under the forwarded host");
  const retry = ok(await h.commit(h.first, id, cut, { sequence: 1, headers: foreign }), "an exact retry under a foreign Host");
  same(retry, receipt, "the retry under a foreign Host returns the original receipt");
  const strip = (value) => ({ ...value, serverNow: 0, stale: false });
  same(strip(ok(await h.read(`/d/${id}/current`, { headers: foreign }), "current under a foreign Host")),
    strip(ok(await h.read(`/d/${id}/current`), "current")), "current is the same under any Host");
  const bytes = await h.read(`/d/${id}/snapshots/${cut.digest}`, { headers: foreign });
  check(!bytes.closed && bytes.status === 200 && bytes.bytes.equals(cut.bytes), "the snapshot under a foreign Host");
  refused(await h.read("/", { headers: foreign }), 404, "not_found", "the root under a foreign Host");
}

async function exactRetry(h) {
  const { id } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  const before = ok(await h.status(h.first, id), "status after the commit");
  await sleep(40);
  same(ok(await h.commit(h.first, id, one.cut, { sequence: 1 }), "an exact retry"), one.receipt,
    "an exact retry returns its original receipt");
  const after = ok(await h.status(h.first, id), "status after the retry");
  same(after.leaseUntil, before.leaseUntil, "an exact retry does not extend the lease");
  same(ok(await h.read(`/d/${id}/current`), "current after the retry").sourceConfirmedAt, one.receipt.sourceConfirmedAt,
    "an exact retry does not refresh source confirmation");
  const two = await h.committed(h.first, id, 2);
  await sleep(40);
  same(ok(await h.commit(h.first, id, one.cut, { sequence: 1 }), "a retry after a later commit"), one.receipt,
    "a retry after a later commit still returns its original receipt");
  const current = ok(await h.read(`/d/${id}/current`), "current after the late retry");
  same([current.digest, current.sequence, current.sourceConfirmedAt], [two.cut.digest, 2, two.receipt.sourceConfirmedAt],
    "a late retry leaves the pointer and its freshness on the later commit");
  same(ok(await h.status(h.first, id), "status after the late retry").receipt, two.receipt, "status keeps the later receipt");
}

async function sequences(h) {
  const { id } = await h.begin();
  await h.committed(h.first, id, 1);
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 1 }), 409, "sequence_conflict",
    "an accepted sequence reused with other bytes");
  const three = await h.committed(h.first, id, 3);
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 2 }), 409, "sequence_superseded",
    "a never-accepted sequence below the current one");
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 3 }), 409, "sequence_conflict", "the current sequence with other bytes");
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 1 }), 409, "sequence_conflict",
    "an older accepted sequence with other bytes");
  const state = ok(await h.status(h.first, id), "status");
  same([state.sequence, state.receipt], [3, three.receipt], "refused commits leave the pointer where it was");
}

async function fencing(h) {
  const { id, owner } = await h.begin();
  const cut = h.snapshot();
  refused(await h.commit(h.first, id, cut, { epoch: 2, sequence: 1 }), 409, "writer_fenced", "a commit at an epoch not held");
  refused(await h.confirm(h.first, id, cut, { epoch: 2, sequence: 1 }), 409, "writer_fenced", "a confirmation at an epoch not held");
  refused(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 2 }), 409, "writer_fenced",
    "a resume at an epoch not held");
  refused(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: hex(), epoch: 1 }), 409, "writer_fenced",
    "a resume by another owner");
  refused(await h.session(h.first, id, { mode: "takeover", deliveryKey: "APP-1", ownerId: hex(), epoch: 2 }), 409,
    "writer_fenced", "a takeover at an epoch not held");
  refused(await h.session(h.first, id, { mode: "takeover", deliveryKey: "APP-1", ownerId: hex(), epoch: 1 }), 409, "lease_held",
    "a takeover of an active lease");
  refused(await h.acquire(h.first, id, hex()), 409, "lease_held", "an acquisition by another owner");
  const resumed = ok(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }),
    "an active resume");
  const state = ok(await h.status(h.first, id), "status");
  same([resumed.epoch, resumed.sequence, resumed.ownerId], [1, 0, owner], "an active resume only returns the session");
  same([state.epoch, state.sequence, state.receipt, state.leaseUntil], [1, 0, null, resumed.leaseUntil],
    "nothing fenced was accepted, and an active resume extends nothing");
  const accepted = await h.committed(h.first, id, 1);
  refused(await h.commit(h.first, id, h.snapshot(), { epoch: 2, sequence: 2 }), 409, "writer_fenced",
    "a commit at a later epoch than the one held");
  same(ok(await h.status(h.first, id), "status after the fenced commit").receipt, accepted.receipt, "the fenced commit moved nothing");
}

async function repositoryScope(h) {
  const { id } = await h.begin(h.first);
  const cut = h.snapshot();
  const asSecond = { Authorization: `Bearer ${h.second.token}` };
  // The second repository's credential on the first repository's routes.
  refused(await h.client.send({ path: h.api(h.first, id, "status"), headers: asSecond }), 401, "unauthorized",
    "another repository's token on status");
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "snapshots"),
    headers: { ...h.envelope(h.first, { sequence: 1, digest: cut.digest }), ...asSecond }, body: cut.bytes }), 401, "unauthorized",
  "another repository's token on a commit");
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "session"),
    headers: { ...h.control(h.first, {}), ...asSecond },
    body: Buffer.from(JSON.stringify({ mode: "acquire", deliveryKey: "APP-1", ownerId: hex() })) }), 401, "unauthorized",
  "another repository's token on a session");
  refused(await h.client.send({ path: `/api/v1/repos/unprovisioned-${hex(4)}/deliveries/${id}/status`, headers: h.auth(h.first) }),
    401, "unauthorized", "an unprovisioned repository");
  // The second repository, with its own credential, on a delivery bound to the first.
  refused(await h.status(h.second, id), 404, "not_found", "a delivery bound to another repository");
  refused(await h.acquire(h.second, id, hex()), 404, "not_found", "acquiring a delivery bound to another repository");
  refused(await h.commit(h.second, id, cut, { sequence: 1 }), 404, "not_found", "committing to a delivery bound to another repository");
  refused(await h.confirm(h.second, id, cut, { sequence: 1 }), 404, "not_found", "confirming a delivery bound to another repository");
  refused(await h.acquire(h.first, id, hex(), { deliveryKey: "APP-2" }), 404, "not_found", "another delivery key on a bound id");
  const unknown = hex();
  refused(await h.status(h.first, unknown), 404, "not_found", "status of an unknown id");
  refused(await h.session(h.first, unknown, { mode: "resume", deliveryKey: "APP-1", ownerId: hex(), epoch: 1 }), 404,
    "not_found", "resuming an unknown id");
  refused(await h.commit(h.first, unknown, cut, { sequence: 1 }), 404, "not_found", "committing to an unknown id");
  const state = ok(await h.status(h.first, id), "status of the bound delivery");
  same([state.epoch, state.sequence, state.receipt], [1, 0, null], "nothing from another repository reached the delivery");
}

async function errors400(h) {
  const { id, owner } = await h.begin();
  const cut = h.snapshot();
  const sessionBody = (value) => h.session(h.first, id, value);
  refused(await h.client.send({ path: `/api/v1/repos/_leading/deliveries/${id}/status` }), 400, "invalid_identifier",
    "a malformed repository id, before authentication");
  refused(await h.client.send({ path: `/api/v1/repos/${"r".repeat(65)}/deliveries/${id}/status`, headers: h.auth(h.first) }),
    400, "invalid_identifier", "a repository id over 64 characters");
  refused(await h.acquire(h.first, hex(), "not-an-owner"), 400, "invalid_identifier", "a malformed owner id");
  refused(await h.acquire(h.first, hex(), hex(), { deliveryKey: "-APP" }), 400, "invalid_identifier", "a malformed delivery key");
  refused(await h.acquire(h.first, hex(), hex(), { deliveryKey: "A".repeat(129) }), 400, "invalid_identifier",
    "a delivery key over 128 characters");
  refused(await h.confirm(h.first, id, cut, { sequence: 1, fingerprint: "nope" }), 400, "invalid_identifier",
    "a malformed source fingerprint");
  refused(await h.commit(h.first, id, { ...cut, digest: cut.digest.toUpperCase() }, { sequence: 1 }), 400, "invalid_identifier",
    "an uppercase snapshot digest");
  for (const [field, value] of [["X-Writer-Epoch", "0"], ["X-Writer-Epoch", "01"], ["X-Snapshot-Sequence", "-1"],
    ["X-Snapshot-Sequence", "1.0"], ["X-Snapshot-Sequence", "9007199254740993"], ["X-Writer-Epoch", ""]])
    refused(await h.commit(h.first, id, cut, { sequence: 1, headers: { [field]: value } }), 400, "invalid_sequence",
      `${field}: ${show(value)}`);
  refused(await sessionBody({ mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 0 }), 400, "invalid_sequence",
    "a resume at epoch 0");
  refused(await sessionBody({ mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: "1" }), 400, "invalid_sequence",
    "a resume whose epoch is a string");
  refused(await sessionBody({ mode: "acquire", deliveryKey: "APP-1", ownerId: owner, extra: true }), 400, "invalid_envelope",
    "a session body with an extra key");
  refused(await sessionBody({ mode: "steal", deliveryKey: "APP-1", ownerId: owner }), 400, "invalid_envelope", "an unknown mode");
  refused(await sessionBody({ mode: "acquire", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }), 400, "invalid_envelope",
    "an acquisition carrying an epoch");
  refused(await sessionBody([]), 400, "invalid_envelope", "a session body that is an array");
  refused(await sessionBody({ mode: "acquire", deliveryKey: "-APP", ownerId: owner, epoch: 1 }), 400, "invalid_identifier",
    "a malformed delivery key reported before an acquisition's epoch");
  refused(await sessionBody({ mode: "acquire", deliveryKey: "-APP", ownerId: owner, extra: 1 }), 400, "invalid_envelope",
    "an extra key reported before a malformed delivery key");
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "confirm"),
    headers: h.envelope(h.first, { sequence: 1, digest: cut.digest }),
    body: Buffer.from(JSON.stringify({ sourceFingerprint: cut.fingerprint, sequence: 1 })) }), 400, "invalid_envelope",
  "a confirmation body with an extra key");
  const rawSession = (bytes, step) => h.client.send({ method: "POST", path: h.api(h.first, id, "session"),
    headers: h.control(h.first, {}), body: Buffer.from(bytes), label: step });
  refused(await rawSession("{", "truncated JSON"), 400, "invalid_json", "truncated JSON");
  refused(await rawSession("", "an empty body"), 400, "invalid_json", "an empty body");
  refused(await rawSession(`${"[".repeat(MAX_NESTING + 1)}${"]".repeat(MAX_NESTING + 1)}`, "nesting"), 400, "invalid_json",
    `nesting deeper than ${MAX_NESTING}`);
  const malformed = Buffer.concat([Buffer.from('{"schemaVersion":"'), Buffer.from([0xc3, 0x28]), Buffer.from('"}')]);
  refused(await h.commit(h.first, id, { bytes: malformed, digest: sha256(malformed) }, { sequence: 1 }), 400, "invalid_json",
    "a snapshot that is not UTF-8");
  for (const row of h.fixtures.invalid) {
    const bytes = Buffer.from(JSON.stringify(row.body));
    refused(await h.commit(h.first, id, { bytes, digest: sha256(bytes) }, { sequence: 1 }), 400, "invalid_projection",
      `the invalid fixture ${row.file}`);
  }
  same(ok(await h.status(h.first, id), "status").receipt, null, "no refused commit was accepted");
}

async function errors401(h) {
  const { id } = await h.begin();
  const cut = h.snapshot();
  const path = h.api(h.first, id, "status");
  refused(await h.client.send({ path }), 401, "unauthorized", "no Authorization header");
  refused(await h.client.send({ path, headers: { Authorization: "Bearer short" } }), 401, "unauthorized", "a token too short");
  refused(await h.client.send({ path, headers: { Authorization: `Basic ${h.first.token}` } }), 401, "unauthorized", "another scheme");
  refused(await h.client.send({ path, headers: { Authorization: `bearer ${h.first.token}` } }), 401, "unauthorized",
    "a lowercase scheme");
  refused(await h.client.send({ path, headers: { Authorization: `Bearer ${h.first.token}x` } }), 401, "unauthorized",
    "a token one character longer");
  refused(await h.client.send({ path, headers: { Authorization: `Bearer ${"A".repeat(43)}` } }), 401, "unauthorized",
    "a well-formed unknown token");
  // Authentication comes before the method check and before any body is read.
  refused(await h.client.send({ method: "GET", path: h.api(h.first, id, "session") }), 401, "unauthorized",
    "a wrong method without credentials");
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "session"), headers: { "Content-Type": "text/plain" },
    body: Buffer.from("{") }), 401, "unauthorized", "a bad body without credentials");
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "snapshots"),
    headers: { ...h.envelope(h.first, { sequence: 1, digest: cut.digest }), Authorization: "" }, body: cut.bytes }), 401,
  "unauthorized", "a commit without credentials");
  same(ok(await h.status(h.first, id), "status").receipt, null, "no unauthenticated commit was accepted");
}

async function errors404(h) {
  const { id } = await h.begin();
  for (const path of ["/", "/d", "/d/", `/d/${id}`, `/d/${id}/current`, `/d/${id.toUpperCase()}/current`, `/d/${id.slice(1)}/current`,
    `/d/${hex()}/snapshots/${hex()}`, "/api/v1", `/api/v1/repos/${h.first.repository}/deliveries/${id}`,
    `/api/v1/repos/${h.first.repository}/deliveries/${id}/history`, `/api/v1/repos/${h.first.repository}/deliveries/${id.slice(2)}/status`])
    refused(await h.read(path, { headers: h.auth(h.first) }), 404, "not_found", `GET ${path}`);
}

async function errors405(h) {
  const { id } = await h.begin();
  const auth = h.auth(h.first);
  for (const [method, action] of [["GET", "session"], ["GET", "snapshots"], ["GET", "confirm"], ["POST", "status"], ["PUT", "snapshots"],
    ["PATCH", "session"], ["DELETE", "confirm"], ["DELETE", "status"], ["OPTIONS", "session"], ["HEAD", "session"]]) {
    const response = await h.client.send({ method, path: h.api(h.first, id, action), headers: auth });
    if (method === "HEAD") check(!response.closed && response.status === 405 && response.bytes.length === 0,
      `HEAD ${action}: expected an empty 405, got ${described(response)}`);
    else refused(response, 405, "method_not_allowed", `${method} ${action}`);
  }
}

async function errors413(h) {
  const { id, owner } = await h.begin();
  const big = (size) => {
    const value = Buffer.from(JSON.stringify({ mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }));
    return Buffer.concat([value, Buffer.alloc(size - value.length, 32)]);
  };
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "session"), headers: h.control(h.first, {}),
    body: big(MAX_SESSION_BYTES + 1) }), 413, "body_too_large", `a session body of ${MAX_SESSION_BYTES + 1} bytes`);
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "confirm"), headers: h.envelope(h.first, { sequence: 1,
    digest: hex() }), body: Buffer.alloc(MAX_CONFIRM_BYTES + 1, 32) }), 413, "body_too_large",
  `a confirmation body of ${MAX_CONFIRM_BYTES + 1} bytes`);
  const cut = h.snapshot();
  refused(await h.client.send({ method: "POST", path: h.api(h.first, id, "snapshots"),
    headers: h.envelope(h.first, { sequence: 1, digest: cut.digest }, { "Content-Length": String(MAX_SNAPSHOT_BYTES + 1) }),
    body: Buffer.alloc(0) }), 413, "body_too_large", "a snapshot declaring more than 16 MiB");
}

async function errors415(h) {
  const { id, owner } = await h.begin();
  const body = Buffer.from(JSON.stringify({ mode: "acquire", deliveryKey: "APP-1", ownerId: owner }));
  const path = h.api(h.first, id, "session");
  for (const type of ["text/plain", "application/json; charset=utf-8", "application/jsonx"])
    refused(await h.client.send({ method: "POST", path, headers: { ...h.auth(h.first), "Content-Type": type }, body }), 415,
      "unsupported_content_type", `Content-Type ${show(type)}`);
  refused(await h.client.send({ method: "POST", path, headers: h.auth(h.first), body }), 415, "unsupported_content_type",
    "no Content-Type");
  same(ok(await h.client.send({ method: "POST", path, headers: { ...h.auth(h.first), "Content-Type": "Application/JSON" }, body }),
    "Content-Type compared without case").epoch, 1, "Content-Type compared without case");
  for (const encoding of ["gzip", "identity"])
    refused(await h.client.send({ method: "POST", path, headers: h.control(h.first, { "Content-Encoding": encoding }), body }), 415,
      "unsupported_encoding", `Content-Encoding ${encoding}`);
  const cut = h.snapshot();
  refused(await h.commit(h.first, id, cut, { sequence: 1, headers: { "Content-Type": "text/json" } }), 415,
    "unsupported_content_type", "a snapshot sent as text/json");
}

async function errors422(h) {
  const { id } = await h.begin();
  const cut = h.snapshot();
  for (const renderer of ["delivery-line-map/4", "delivery-line-map/1", "", "Delivery-Line-Map/3"])
    refused(await h.commit(h.first, id, cut, { sequence: 1, renderer }), 422, "unsupported_renderer", `renderer ${show(renderer)}`);
  refused(await h.commit(h.first, id, { ...cut, digest: hex() }, { sequence: 1 }), 422, "digest_mismatch", "another body's digest");
  // Several faults at once: the first in the spec's order of checks is the one reported.
  refused(await h.commit(h.first, id, cut, { sequence: 1, renderer: "delivery-line-map/9", headers: { "X-Snapshot-Sequence": "01" } }),
    422, "unsupported_renderer", "an unsupported renderer reported before a malformed sequence");
  refused(await h.commit(h.first, id, { bytes: Buffer.from([0xc3, 0x28]), digest: hex() }, { sequence: 1 }), 422, "digest_mismatch",
    "a wrong digest reported before malformed JSON");
  // The first accepted snapshot fixes the renderer for the delivery.
  const legacy = h.snapshot(h.fixtures.legacy);
  const legacyReceipt = isReceipt(ok(await h.commit(h.first, id, legacy, { sequence: 1, renderer: LEGACY_RENDERER_VERSION }),
    `a ${LEGACY_SNAPSHOT_VERSION} snapshot`), "legacy commit");
  same(legacyReceipt.rendererVersion, LEGACY_RENDERER_VERSION, "the legacy receipt names its renderer");
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 2 }), 422, "unsupported_renderer",
    "another renderer after the first accepted snapshot");
  refused(await h.confirm(h.first, id, legacy, { sequence: 1 }), 409, "confirmation_conflict",
    "confirming under another renderer");
  ok(await h.confirm(h.first, id, legacy, { sequence: 1, renderer: LEGACY_RENDERER_VERSION }), "confirming the legacy cut");
}

async function conflicts409(h) {
  const { id, owner } = await h.begin();
  refused(await h.confirm(h.first, id, h.snapshot(), { sequence: 1 }), 409, "confirmation_conflict", "confirming before any commit");
  const one = await h.committed(h.first, id, 1);
  refused(await h.confirm(h.first, id, one.cut, { sequence: 1, fingerprint: hex() }), 409, "confirmation_conflict",
    "confirming another fingerprint");
  refused(await h.confirm(h.first, id, { ...one.cut, digest: hex() }, { sequence: 1 }), 409, "confirmation_conflict",
    "confirming another digest");
  refused(await h.confirm(h.first, id, one.cut, { sequence: 2 }), 409, "confirmation_conflict", "confirming another sequence");
  await h.committed(h.first, id, 2);
  refused(await h.confirm(h.first, id, one.cut, { sequence: 1 }), 409, "confirmation_conflict", "confirming a superseded cut");
  refused(await h.acquire(h.first, id, hex()), 409, "lease_held", "acquiring an active lease as another owner");
  same(ok(await h.status(h.first, id), "status").ownerId, owner, "the holder is unchanged");
}

async function projections(h) {
  const { id } = await h.begin();
  let sequence = 0;
  for (const row of h.fixtures.valid) {
    const bytes = Buffer.from(JSON.stringify(row.body));
    const receipt = isReceipt(ok(await h.commit(h.first, id, { bytes, digest: sha256(bytes) }, { sequence: ++sequence }),
      `the valid fixture ${row.file}`), row.file);
    same([receipt.sequence, receipt.sourceFingerprint], [sequence, row.body.sourceFingerprint], `${row.file} receipt`);
    const served = await h.read(`/d/${id}/snapshots/${sha256(bytes)}`);
    check(!served.closed && served.status === 200 && served.bytes.equals(bytes), `${row.file}: served as its exact bytes`);
  }
}

async function streamedLimits(h) {
  const { id, owner } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  const streamed = (action, headers, body, step) => h.client.send({ method: "POST", path: h.api(h.first, id, action), headers,
    body, chunked: true, label: step });
  const exact = Buffer.from(JSON.stringify({ mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }));
  const padded = Buffer.concat([exact, Buffer.alloc(MAX_SESSION_BYTES - exact.length, 32)]);
  ok(await streamed("session", h.control(h.first, {}), padded, "a streamed session body of exactly the limit"),
    `a streamed session body of exactly ${MAX_SESSION_BYTES} bytes`);
  refused(await streamed("session", h.control(h.first, {}), Buffer.concat([padded, Buffer.from(" ")]), "session"), 413,
    "body_too_large", `a streamed session body of ${MAX_SESSION_BYTES + 1} bytes`);
  refused(await streamed("confirm", h.envelope(h.first, { sequence: 1, digest: one.cut.digest }),
    Buffer.alloc(MAX_CONFIRM_BYTES + 1, 32), "confirm"), 413, "body_too_large", `a streamed confirmation of ${MAX_CONFIRM_BYTES + 1} bytes`);
  const huge = Buffer.alloc(MAX_SNAPSHOT_BYTES + 1, 32);
  huge.set(one.cut.bytes);
  refused(await streamed("snapshots", h.envelope(h.first, { sequence: 2, digest: sha256(huge) }), huge, "snapshot"), 413,
    "body_too_large", "a streamed snapshot of 16 MiB and one byte");
  same(ok(await h.status(h.first, id), "status").receipt, one.receipt, "no oversized body moved the pointer");
}

async function refusedRequests(h) {
  const { id } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  const auth = h.auth(h.first);
  for (const path of [`${h.api(h.first, id, "status")}?x=1`, `/d/${id}/current?x=1`, `/d/${id}?view=1`,
    `/d/${id}/snapshots/${one.cut.digest}?download=1`, "/?q"])
    refused(await h.read(path, { headers: auth }), 404, "not_found", `a query string: ${path}`);
  refused(await h.client.send({ method: "POST", path: `${h.api(h.first, id, "session")}?mode=acquire`, headers: h.control(h.first, {}),
    body: Buffer.from(JSON.stringify({ mode: "acquire", deliveryKey: "APP-1", ownerId: hex() })) }), 404, "not_found",
  "a write route with a query string");
  for (const path of [`/d/%${id.charCodeAt(0).toString(16)}${id.slice(1)}/current`, `/d/${id}/%63urrent`, `/d%2F${id}/current`,
    `/api/v1/repos/${h.first.repository}/deliveries/${id}/%73tatus`, `//d/${id}/current`, `/d/${id}//current`])
    refused(await h.read(path, { headers: auth }), 404, "not_found", `an encoded or unusual path: ${path}`);
  for (const method of ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    for (const path of [`/d/${id}`, `/d/${id}/current`, `/d/${id}/snapshots/${one.cut.digest}`]) {
      const response = await h.client.send({ method, path, headers: { "Content-Type": "application/json", Origin: "https://elsewhere.invalid",
        "Access-Control-Request-Method": "POST" }, body: Buffer.from("{}") });
      refused(response, 404, "not_found", `${method} ${path}`);
    }
  // A bare trailing "?" carries no query: it reads as the same route without it.
  const strip = (value) => ({ ...value, serverNow: 0, stale: false });
  same(strip(ok(await h.read(`/d/${id}/current?`), "current with a bare trailing ?")), strip(ok(await h.read(`/d/${id}/current`), "current")),
    "a bare trailing ? reads as no query");
  const head = await h.read(`/d/${id}/current`, { method: "HEAD" });
  check(!head.closed && head.status === 404, `HEAD on a read route is refused, got ${described(head)}`);
  same(ok(await h.status(h.first, id), "status").receipt, one.receipt, "no refused request changed the delivery");
}

async function noDirectory(h) {
  const { id } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  await h.committed(h.first, id, 2);
  const auth = h.auth(h.first);
  for (const path of ["/", "/d", "/d/", `/d/${id}/`, `/d/${id}/history`, `/d/${id}/snapshots`, `/d/${id}/snapshots/`,
    `/d/${id}/snapshots/${one.cut.digest}`, `/d/${id}/receipts`, "/api", "/api/v1/repos", `/api/v1/repos/${h.first.repository}`,
    `/api/v1/repos/${h.first.repository}/deliveries`, `/api/v1/repos/${h.first.repository}/deliveries/`, "/assets", "/assets/",
    `/assets/${hex()}.js`, "/robots.txt", "/favicon.ico", "/index.html", "/health", "/healthz", "/.well-known/security.txt"]) {
    const response = await h.read(path, { headers: auth });
    check(!response.closed && !(response.status >= 300 && response.status < 400) && !response.headers.location,
      `${path}: a redirect`);
    refused(response, 404, "not_found", `${path}: no listing, history or directory`);
  }
}

async function anonymousRead(h) {
  const { id } = await h.begin();
  const cut = h.snapshot();
  const title = h.fixtures.valid[0].body.document.state.queue?.[0]?.title;
  await h.committed(h.first, id, 1, { cut });
  const page = await h.read(`/d/${id}`);
  check(!page.closed && page.status === 200, `the page: ${described(page)}`);
  check(page.headers["content-type"] === "text/html; charset=utf-8", "the page is served as UTF-8 HTML");
  check(!page.text.includes(id) && (!title || !page.text.includes(title)) && !page.text.includes(cut.fingerprint),
    "the page shell embeds no publisher data and no capability");
  const assets = [...new Set(page.text.match(/\/assets\/[a-f0-9]{64}\.(?:js|css)/g) ?? [])];
  check(assets.length > 0, "the page loads content-addressed assets");
  for (const path of assets) {
    const asset = await h.read(path);
    check(!asset.closed && asset.status === 200 && asset.bytes.length > 0, `${path}: ${described(asset)}`);
    check(asset.headers["cache-control"] === ASSET_CACHE, `${path}: immutable caching`);
    const type = path.endsWith(".js") ? "text/javascript; charset=utf-8" : "text/css; charset=utf-8";
    check(asset.headers["content-type"] === type, `${path}: served as ${type}`);
  }
  const bytes = await h.read(`/d/${id}/snapshots/${cut.digest}`, { headers: { Authorization: `Bearer ${h.first.token}`, Cookie: "a=b" } });
  check(!bytes.closed && bytes.bytes.equals(cut.bytes), "the snapshot is the same with credentials offered");
  const unknown = hex();
  refused(await h.read(`/d/${unknown}`), 404, "not_found", "the page of an unknown id");
  refused(await h.read(`/d/${unknown}/current`), 404, "not_found", "current of an unknown id");
  refused(await h.read(`/d/${unknown}/snapshots/${cut.digest}`), 404, "not_found", "another delivery's digest under an unknown id");
  // A read of an unknown id creates no state: the id is still free for any repository to bind.
  const fresh = isSession(ok(await h.acquire(h.second, unknown, hex()), "acquire after anonymous reads"), "acquire");
  same([fresh.epoch, fresh.sequence, fresh.receipt], [1, 0, null], "an id read anonymously is still unbound");
}

const audited = (response) => !response.closed && response.status !== undefined;
async function securityHeaders(h) {
  const { id } = await h.begin();
  const cut = h.snapshot();
  await h.committed(h.first, id, 1, { cut });
  // One of each kind of response, so the audit below never depends on which scenarios ran.
  await h.read(`/d/${id}`);
  await h.read(`/d/${id}/current`);
  await h.read(`/d/${id}/snapshots/${cut.digest}`);
  await h.read("/");
  await h.read(`/d/${id}/current?x`);
  await h.status(h.first, id);
  await h.client.send({ path: h.api(h.first, id, "status") });
  await h.client.send({ method: "POST", path: h.api(h.first, id, "status"), headers: h.auth(h.first) });
  await h.client.send({ method: "POST", path: h.api(h.first, id, "session"), headers: h.control(h.first, {}), body: Buffer.alloc(3000, 32) });
  await h.client.send({ method: "POST", path: h.api(h.first, id, "session"), headers: h.control(h.first, {}), body: Buffer.from("{") });
  await h.commit(h.first, id, cut, { sequence: 1, renderer: "x" });
  await h.commit(h.first, id, h.snapshot(), { sequence: 1 });
  const problems = [];
  for (const response of h.client.responses.filter(audited)) {
    const where = `${response.method} ${response.path.replace(/[a-f0-9]{64}/g, "<id>")} (${response.status})`;
    const isAsset = response.status === 200 && response.path.startsWith("/assets/");
    const isShell = response.status === 200 && /^text\/html/.test(String(response.headers["content-type"]));
    for (const [name, value] of Object.entries(BASE_HEADERS)) {
      const expected = name === "cache-control" && isAsset ? ASSET_CACHE : name === "content-security-policy" && isShell ? SHELL_CSP : value;
      if (response.headers[name] !== expected) problems.push(`${where}: ${name} is ${show(response.headers[name])}`);
    }
    for (const name of ["access-control-allow-origin", "access-control-allow-credentials", "set-cookie", "location"])
      if (response.headers[name] !== undefined) problems.push(`${where}: sends ${name}`);
  }
  check(problems.length === 0, `responses without the security header set: ${[...new Set(problems)].slice(0, 8).join("; ")}`);
}

// The slow tier: lease expiry, resume, takeover and staleness, waited out in real time.
async function untilExpired(h, credential, id) {
  const state = ok(await h.status(credential, id), "status before waiting");
  const now = ok(await h.read(`/d/${id}/current`), "server time").serverNow;
  await sleep(Math.max(0, state.leaseUntil - now) + 1500);
}
async function leaseExpiry(h) {
  const { id, owner } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  await untilExpired(h, h.first, id);
  refused(await h.commit(h.first, id, h.snapshot(), { sequence: 2 }), 409, "lease_expired", "a commit after the lease expired");
  refused(await h.confirm(h.first, id, one.cut, { sequence: 1 }), 409, "lease_expired", "a confirmation after the lease expired");
  refused(await h.acquire(h.first, id, owner), 409, "resume_required", "an acquisition retry after the lease expired");
  same(ok(await h.commit(h.first, id, one.cut, { sequence: 1 }), "an exact retry after expiry"), one.receipt,
    "an exact retry after expiry still returns its original receipt");
  same(ok(await h.status(h.first, id), "status").receipt, one.receipt, "expiry leaves the pointer");
}
async function resume(h) {
  const { id, owner } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  await untilExpired(h, h.first, id);
  const resumed = isSession(ok(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }),
    "resume after expiry"), "resume");
  same([resumed.epoch, resumed.sequence, resumed.ownerId, resumed.receipt], [2, 0, owner, one.receipt],
    "resume after expiry advances the epoch and resets the sequence");
  refused(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }), 409, "writer_fenced",
    "a second resume at the old epoch");
  refused(await h.commit(h.first, id, h.snapshot(), { epoch: 1, sequence: 2 }), 409, "writer_fenced", "a commit at the old epoch");
  const two = await h.committed(h.first, id, 1, { epoch: 2, step: "the first commit of the new epoch" });
  same(ok(await h.status(h.first, id), "status").receipt, two.receipt, "the new epoch's commit is current");
}
async function takeover(h) {
  const { id, owner } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  await untilExpired(h, h.first, id);
  const next = hex();
  const taken = isSession(ok(await h.session(h.first, id, { mode: "takeover", deliveryKey: "APP-1", ownerId: next, epoch: 1 }),
    "takeover after expiry"), "takeover");
  same([taken.epoch, taken.sequence, taken.ownerId, taken.receipt], [2, 0, next, one.receipt], "takeover installs the new owner");
  refused(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }), 409, "writer_fenced",
    "the displaced owner resuming");
  refused(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 2 }), 409, "writer_fenced",
    "the displaced owner resuming at the new epoch");
  refused(await h.commit(h.first, id, h.snapshot(), { epoch: 1, sequence: 2 }), 409, "writer_fenced", "the displaced owner committing");
  refused(await h.session(h.first, id, { mode: "takeover", deliveryKey: "APP-1", ownerId: hex(), epoch: 2 }), 409, "lease_held",
    "a takeover of the new active lease");
  await h.committed(h.first, id, 1, { epoch: 2, step: "the new owner's first commit" });
}
async function staleness(h) {
  const { id, owner } = await h.begin();
  const one = await h.committed(h.first, id, 1);
  const committedAt = Date.now();
  await sleep(STALE_MS / 2);
  ok(await h.status(h.first, id), "status while waiting");
  ok(await h.session(h.first, id, { mode: "resume", deliveryKey: "APP-1", ownerId: owner, epoch: 1 }), "an active resume while waiting");
  same(ok(await h.commit(h.first, id, one.cut, { sequence: 1 }), "an exact retry while waiting"), one.receipt, "retry");
  const fresh = ok(await h.read(`/d/${id}/current`), "current halfway");
  check(fresh.stale === false, "a commit is fresh before 120 s");
  await sleep(Math.max(0, committedAt + STALE_MS + 1500 - Date.now()));
  const stale = ok(await h.read(`/d/${id}/current`), "current after 120 s");
  same([stale.stale, stale.sourceConfirmedAt, stale.digest], [true, one.receipt.sourceConfirmedAt, one.cut.digest],
    "with no confirmation for 120 s the pointer is stale; status, resume and retries refreshed nothing");
  check(stale.serverNow - stale.sourceConfirmedAt >= STALE_MS, "stale is computed from server time");
  const confirmed = ok(await h.confirm(h.first, id, one.cut, { sequence: 1 }), "a confirmation after staleness");
  const again = ok(await h.read(`/d/${id}/current`), "current after the confirmation");
  same([again.stale, again.sourceConfirmedAt], [false, confirmed.sourceConfirmedAt], "a confirmation makes the pointer fresh");
}

export const SCENARIOS = Object.freeze([
  { name: "round-trip.first-repository", tier: "fast", run: (h) => roundTrip(h, h.first) },
  { name: "round-trip.second-repository", tier: "fast", run: (h) => roundTrip(h, h.second) },
  { name: "host-headers-ignored", tier: "fast", run: hostHeaders },
  { name: "exact-retry", tier: "fast", run: exactRetry },
  { name: "sequence-rules", tier: "fast", run: sequences },
  { name: "writer-fencing", tier: "fast", run: fencing },
  { name: "repository-scope", tier: "fast", run: repositoryScope },
  { name: "errors.400", tier: "fast", run: errors400 },
  { name: "errors.401", tier: "fast", run: errors401 },
  { name: "errors.404", tier: "fast", run: errors404 },
  { name: "errors.405", tier: "fast", run: errors405 },
  { name: "errors.409", tier: "fast", run: conflicts409 },
  { name: "errors.413", tier: "fast", run: errors413 },
  { name: "errors.415", tier: "fast", run: errors415 },
  { name: "errors.422", tier: "fast", run: errors422 },
  { name: "projection-fixtures", tier: "fast", run: projections },
  { name: "streamed-body-limit", tier: "fast", run: streamedLimits },
  { name: "refused-requests", tier: "fast", run: refusedRequests },
  { name: "no-redirect-listing-or-history", tier: "fast", run: noDirectory },
  { name: "anonymous-read", tier: "fast", run: anonymousRead },
  // Last of the fast tier: it audits every response the scenarios before it received.
  { name: "security-headers", tier: "fast", run: securityHeaders },
  { name: "slow.lease-expiry", tier: "slow", run: leaseExpiry },
  { name: "slow.resume", tier: "slow", run: resume },
  { name: "slow.takeover", tier: "slow", run: takeover },
  { name: "slow.staleness", tier: "slow", run: staleness },
]);

export function checkCredentials(credentials) {
  check(Array.isArray(credentials) && credentials.length === 2, "two repository credentials are required");
  for (const row of credentials) {
    check(row && typeof row.repository === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(row.repository),
      "each credential names a repository id");
    check(typeof row.token === "string" && /^[A-Za-z0-9_-]{43,128}$/.test(row.token), "each credential holds a publishing token");
  }
  check(credentials[0].repository !== credentials[1].repository, "the two credentials name two repositories");
  return credentials.map((row) => ({ repository: row.repository, token: row.token }));
}

/**
 * Runs the suite against `origin`. Returns one result per scenario, `{ name, tier, ok, message }`; it never throws
 * for a failing server. The fast tier runs in order, then the slow tier, whose scenarios wait side by side.
 */
export async function runSuite({ origin, credentials, slow = false, fixtures = DEFAULT_FIXTURES, report = () => {} }) {
  const client = createClient(origin);
  const loaded = loadFixtures(fixtures);
  const h = harness(client, checkCredentials(credentials), loaded, hex(8));
  const results = [];
  const attempt = async (scenario) => {
    let row;
    try {
      await scenario.run(h);
      row = { name: scenario.name, tier: scenario.tier, ok: true, message: "" };
    } catch (error) {
      const message = error instanceof Failure ? error.message : `the scenario stopped: ${error?.stack ?? error}`;
      row = { name: scenario.name, tier: scenario.tier, ok: false, message };
    }
    report(row);
    return row;
  };
  for (const scenario of SCENARIOS.filter((row) => row.tier === "fast")) results.push(await attempt(scenario));
  if (slow) results.push(...await Promise.all(SCENARIOS.filter((row) => row.tier === "slow").map(attempt)));
  return results;
}
