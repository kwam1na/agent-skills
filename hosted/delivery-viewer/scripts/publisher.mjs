// Manage one repository at a time through the operator's authenticated Wrangler.
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { publisherKey, REVOKED } from '../src/publisher-record.mjs';

const [operation, bucket, repository, tokenEnv, ...extra] = process.argv.slice(2);
if (!['set', 'revoke'].includes(operation) || !/^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(bucket || '') ||
    !repository || extra.length || (operation === 'set' ? !/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnv || '') : tokenEnv !== undefined)) {
  console.error('Usage: node scripts/publisher.mjs set BUCKET REPOSITORY TOKEN_ENV | revoke BUCKET REPOSITORY');
  process.exit(2);
}

const objectPath = bucket + '/' + publisherKey(repository);
const wrangler = fileURLToPath(new URL('../node_modules/wrangler/bin/wrangler.js', import.meta.url));
const environment = { ...process.env };
let record = REVOKED;
if (operation === 'set') {
  const token = process.env[tokenEnv] || '';
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(token)) {
    console.error('The named variable must contain a base64url publisher token (43–128 characters).');
    process.exit(2);
  }
  record = createHash('sha256').update(token).digest('hex');
  delete environment[tokenEnv];
}

function run(action, input) {
  const result = spawnSync(process.execPath, [wrangler, 'r2', 'object', action, objectPath, '--remote', '--pipe'], {
    input, env: environment, encoding: 'utf8', stdio: ['pipe', 'pipe', 'inherit'],
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
  return result.stdout;
}
run('put', record);
if (run('get') !== record) throw new Error('Publisher record read-back did not match the requested update');
console.log('Verified publisher ' + operation + ' for ' + repository + ' in ' + bucket);
