import { defineConfig } from "vitest/config";
// The portable server's tests, in Node: its stores against the core storage contract, its durability and ordering,
// and the built server process (npm run build:node) for startup, crash, timeouts and provisioned tokens.
export default defineConfig({
  test: { include: ["test/node/**/*.test.ts"], environment: "node", maxWorkers: 1, fileParallelism: false, testTimeout: 30_000 },
});
