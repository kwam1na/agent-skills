// The Chromium suite against the portable Node server, built with `npm run build:node`.
import config from "./playwright.config";
process.env.HOSTED_TARGET = "node";
export default { ...config, outputDir: "test-results/node" };
