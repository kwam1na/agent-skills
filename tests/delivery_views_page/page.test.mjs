import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

test("an inlined page script runs against the document", () => {
  const dom = new JSDOM(
    '<main id="view"></main><script>document.getElementById("view").textContent = "ready";</script>',
    { runScripts: "dangerously" },
  );
  assert.equal(dom.window.document.getElementById("view").textContent, "ready");
});
