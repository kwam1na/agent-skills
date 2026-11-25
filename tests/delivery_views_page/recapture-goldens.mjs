// Re-derives the line map's golden outputs from the page as it now stands, with each case's clock, stops and folds kept
// as its golden names them: `node tests/delivery_views_page/recapture-goldens.mjs`. A golden changes only by a run of
// this, committed apart from any page change, so the diff shows what the page now draws and nothing else.
import { writeFileSync } from "node:fs";
import { recapturedGoldens } from "./goldens.mjs";

process.env.TZ = "UTC";

for (const [file, text] of Object.entries(await recapturedGoldens())) writeFileSync(file, text);
