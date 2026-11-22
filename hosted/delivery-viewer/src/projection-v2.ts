// Retained publishing rules from the deployed v2 viewer (d1cf0706).
import validateSchema from './generated-validator-v2.js';
import { fail, identifier, object, safeUrl } from './protocol';

// Keep the local-reference boundary aligned with public_projection.py, including option assignments.
const localPath = /(?:file:\/\/|(?:^|[\s\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e“”‘’])(?:\/[A-Za-z0-9_.-]+\/|[A-Za-z]:[\\/]|\\\\|~[/\\]|\.{1,2}[/\\]|(?:\.?agents|\.agent-skills|\.orchestration|docs|scripts|skills|tests)[/\\]))/;
function hasLocalPath(value: string): boolean {
  const starts = /https:\/\/(?!\/)/gi;
  const closing: Record<string, string> = { ')': '(', ']': '[', '}': '{' };
  const pieces: string[] = []; let position = 0;
  let match: RegExpExecArray | null;
  while ((match = starts.exec(value)) !== null) {
    let end = starts.lastIndex; const stack: string[] = [];
    // An unmatched closing wrapper ends this URL; its suffix remains prose.
    while (end < value.length) {
      const character = value[end];
      if (/[\s<>"'`“”‘’\\]/.test(character)) break;
      if ('([{'.includes(character)) stack.push(character);
      else if (character in closing) {
        if (stack.at(-1) !== closing[character]) break;
        stack.pop();
      }
      end++;
    }
    const token = value.slice(match.index, end);
    const unwrapped = token.replace(/[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e“”‘’]+$/, '');
    pieces.push(value.slice(position, match.index), safeUrl(token) || safeUrl(unwrapped) ? ' ' : token);
    position = end; starts.lastIndex = end;
  }
  pieces.push(value.slice(position));
  return localPath.test(pieces.join(''));
}
const credentialKey = /^(?:password|token|secret|api[_-]?key|credential|authorization|cookie)$/i;
function scan(value: unknown): void {
  if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) fail('invalid_projection');
  if (typeof value === 'string' && hasLocalPath(value)) fail('invalid_projection');
  if (value && typeof value === 'object') {
    for (const [key, entry] of Object.entries(value)) {
      if (credentialKey.test(key)) fail('invalid_projection');
      scan(key); scan(entry);
    }
  }
}
export function projectionV2(value: unknown): { sourceFingerprint: string } {
  if (!validateSchema(value)) fail('invalid_projection');
  scan(value);
  const body = object(value);
  const links = object(body.links);
  const urls = [...Object.values(object(links.issues)), ...Object.values(object(links.commits)),
    ...Object.values(object(links.changes)).map(row => object(row).href)];
  if (!urls.every(safeUrl)) fail('invalid_projection');
  const state = object(object(body.document).state);
  const rows = [...(state.queue as Record<string, unknown>[] || []), ...(state.planned as Record<string, unknown>[] || [])];
  const subjects = new Set(rows.map(row => row.subject));
  const runs = new Set(rows.map(row => row.runId));
  if (Object.keys(object(body.runs)).some(key => !runs.has(key)) || Object.keys(object(body.reviews)).some(key => !subjects.has(key))) fail('invalid_projection');
  return { sourceFingerprint: identifier(body.sourceFingerprint) };
}
