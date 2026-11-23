import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    include: ["test/viewer.test.ts", "test/fonts.test.ts", "test/versions.test.ts"],
    environment: "node",
    maxWorkers: 1,
    fileParallelism: false,
  },
});
