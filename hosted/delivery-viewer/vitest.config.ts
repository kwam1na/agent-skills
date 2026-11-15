import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';
export default defineConfig({
  plugins: [cloudflareTest({ wrangler: { configPath: './wrangler.jsonc' },
    miniflare: { bindings: { PUBLISHERS: JSON.stringify({'repo-a': '7f2af277120cad20a00501603ae89d5a30a2e895af64b036dc2aec3b01ba365e', 'repo-b': '8779350a838b078e087f6ee8c48788ca69a652da4a6f742c77bf9d627a011bd9'}) } } })],
  test: { maxWorkers: 1, fileParallelism: false, testTimeout: 15000 }
});
