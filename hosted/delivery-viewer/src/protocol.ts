import validateSchema from './generated-validator.js';

export const API_VERSION = 'hosted-delivery-api/1';
export const MAX_BYTES = 16 * 1024 * 1024;
export const MAX_SESSION_BYTES = 2048;
export const MAX_CONFIRM_BYTES = 1024;
export const MAX_NESTING = 64;
export const LEASE_MS = 180_000;
export const STALE_MS = 120_000;
export const RENDERER = 'delivery-line-map/3';
export const LEGACY_RENDERER = 'delivery-line-map/2';
export type RendererVersion = typeof RENDERER | typeof LEGACY_RENDERER;
export function supportedRenderer(value: string | null): RendererVersion {
  if (value === RENDERER || value === LEGACY_RENDERER) return value;
  return fail('unsupported_renderer', 422);
}
export const ID = /^[a-f0-9]{64}$/;
export const REPOSITORY = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;
export const KEY = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;
export class ApiError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
export function fail(code: string, status = 400): never { throw new ApiError(code, status); }
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('invalid_envelope');
  return value as Record<string, unknown>;
}
export function exact(value: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) fail('invalid_envelope');
}
export function integer(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) fail('invalid_sequence');
  return Number(value);
}
export function identifier(value: unknown, pattern = ID): string {
  if (typeof value !== 'string' || !pattern.test(value)) fail('invalid_identifier');
  return value;
}
export async function digest(bytes: BufferSource): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('');
}
export async function readBytes(request: Request, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  if (request.headers.get('content-type')?.toLowerCase() !== 'application/json') fail('unsupported_content_type', 415);
  if (request.headers.has('content-encoding')) fail('unsupported_encoding', 415);
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) fail('body_too_large', 413);
  const reader = request.body?.getReader();
  if (!reader) fail('invalid_json');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); fail('body_too_large', 413); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
export function parse(bytes: Uint8Array): unknown {
  // Bound nesting before JSON.parse or schema traversal. Strings are ignored.
  let depth = 0, quoted = false, escape = false;
  for (const c of bytes) {
    if (quoted) {
      if (escape) escape = false;
      else if (c === 92) escape = true;
      else if (c === 34) quoted = false;
    } else if (c === 34) quoted = true;
    else if (c === 123 || c === 91) { if (++depth > MAX_NESTING) fail('invalid_json'); }
    else if (c === 125 || c === 93) depth--;
  }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { return fail('invalid_json'); }
}
const credentialKey = /^(?:password|token|secret|api[_-]?key|credential|authorization|cookie)$/i;
// The snapshot is the local live view's own cut. What it never holds is a credential-named field.
function scan(value: unknown): void {
  if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) fail('invalid_projection');
  if (value && typeof value === 'object') {
    for (const [key, entry] of Object.entries(value)) {
      if (credentialKey.test(key)) fail('invalid_projection');
      scan(key); scan(entry);
    }
  }
}
export function safeUrl(value: unknown): boolean {
  if (typeof value !== 'string' || /[\x00-\x20\x7f\\]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password && !url.port && !url.search && !url.hash;
  } catch { return false; }
}
export function projection(value: unknown): { sourceFingerprint: string } {
  if (!validateSchema(value)) fail('invalid_projection');
  scan(value);
  const body = object(value);
  const links = object(body.links);
  const urls = [...Object.values(object(links.issues)), ...Object.values(object(links.commits)),
    ...Object.values(object(links.changes)).map(row => object(row).href)];
  if (!urls.every(safeUrl)) fail('invalid_projection');
  return { sourceFingerprint: identifier(body.sourceFingerprint) };
}
export const headers = {
  'Cache-Control': 'no-store',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
  'X-Robots-Tag': 'noindex, nofollow, noarchive',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
};
export function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers });
}
export function errorResponse(error: unknown): Response {
  return json({ error: error instanceof ApiError ? error.code : 'temporarily_unavailable' }, error instanceof ApiError ? error.status : 503);
}
export interface Receipt {
  protocolVersion: 'hosted-delivery-api/1';
  rendererVersion: RendererVersion;
  epoch: number;
  sequence: number;
  digest: string;
  sourceFingerprint: string;
  publishedAt: number;
  sourceConfirmedAt: number;
}
export interface Commit {
  epoch: number;
  sequence: number;
  digest: string;
  rendererVersion: RendererVersion;
}

// RPC does not preserve custom Error properties. Return safe tagged failures,
// including unexpected storage failures, without serializing exception messages.
export type Result<T> = { ok: true; value: T } | { ok: false; error: string; status: number };
export async function result<T>(run: () => T | Promise<T>): Promise<Result<T>> {
  try { return { ok: true, value: await run() }; }
  catch (error) { return { ok: false, error: error instanceof ApiError ? error.code : 'temporarily_unavailable', status: error instanceof ApiError ? error.status : 503 }; }
}
export function unwrap<T>(result: Result<T>): T {
  if (!result.ok) throw new ApiError(result.error, result.status);
  return result.value;
}
