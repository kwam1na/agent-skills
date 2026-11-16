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
    const fields: Record<string, unknown> = {
      state: body.document,
      runs: body.runs,
      reviews: body.reviews,
      links: body.links,
      view: body.view,
      plan: body.plan,
      needs: body.needs,
    };
    for (const [key, value] of Object.entries(fields)) {
      if (value === undefined || value === null) continue;
      const block = document.createElement("script");
      block.type = "application/json";
      block.id = `delivery-${key}`;
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
    if (started) return;
    started = true;
    seed(body);
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
