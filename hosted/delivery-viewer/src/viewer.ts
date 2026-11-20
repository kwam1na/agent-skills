import {
  createViewStore,
  type PublicProjection,
  type ViewStatus,
} from "./view-store";

const match = /^\/d\/([a-f0-9]{64})$/.exec(location.pathname);
const statusNode = document.getElementById("hosting-status");
const timesNode = document.getElementById("hosting-times");
const app = document.getElementById("app");
const entry = document.querySelector<HTMLScriptElement>(
  "script[data-renderer]",
);
if (match && statusNode && timesNode && app && entry) {
  const store = createViewStore(match[1]);
  let started = false;
  let assetFailed = false;
  function showStatus(value: ViewStatus) {
    if (assetFailed) {
      statusNode!.textContent = "Viewer assets unavailable · reload to retry";
      statusNode!.dataset.state = "unavailable";
      return;
    }
    if (statusNode!.textContent !== value.detail)
      statusNode!.textContent = value.detail;
    statusNode!.dataset.state = value.kind;
    const r = value.receipt;
    timesNode!.textContent = r
      ? `Source confirmed ${new Date(r.sourceConfirmedAt).toLocaleString()} · Published ${new Date(r.publishedAt).toLocaleString()} · Revision ${r.epoch}.${r.sequence}`
      : "";
  }
  function seed(body: PublicProjection) {
    // The blocks the local page embeds, by the ids the corpus renderer reads them from.
    const fields: Record<string, unknown> = {
      state: body.document,
      runs: body.runs,
      reviews: body.reviews,
      links: body.links,
      view: body.view,
      plan: body.plan,
      needs: body.needs,
      files: body.files,
      "left-open": body.leftOpen,
      integration: body.integration,
    };
    for (const [key, value] of Object.entries(fields)) {
      const id = `delivery-${key}`;
      const existing = document.getElementById(id);
      if (value === undefined || value === null) {
        existing?.remove();
        continue;
      }
      if (existing) {
        existing.textContent = JSON.stringify(value);
        continue;
      }
      const block = document.createElement("script");
      block.type = "application/json";
      block.id = id;
      block.textContent = JSON.stringify(value);
      document.body.appendChild(block);
    }
    document.title =
      typeof body.view.title === "string" ? body.view.title : "Hosted delivery";
  }
  store.onHostingStatus(showStatus);
  store.subscribe(() => {
    const body = store.projection();
    if (!body) return;
    document.title =
      typeof body.view.title === "string" ? body.view.title : "Hosted delivery";
    // Keep inert blocks current even while the first renderer request is delayed.
    // The active renderer still receives its coherent native subscription push.
    seed(body);
    if (started) return;
    started = true;
    Object.assign(globalThis, { deliveryStateStore: store });
    const renderer = document.createElement("script");
    renderer.src = entry!.dataset.renderer!;
    renderer.addEventListener("load", () =>
      app!.setAttribute("aria-busy", "false"),
    );
    renderer.addEventListener("error", () => {
      assetFailed = true;
      app!.textContent =
        "Viewer assets unavailable. Reload this page to retry.";
      app!.setAttribute("aria-busy", "false");
      showStatus(store.status());
    });
    document.head.appendChild(renderer);
  });
  store.start();
  addEventListener("pagehide", () => store.stop());
  addEventListener("pageshow", () => store.start());
}
