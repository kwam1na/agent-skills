// Prints one JSON line per finished test for tests/test_delivery_views_page.py.
export default async function* reporter(source) {
  for await (const event of source) {
    if (event.type !== "test:pass" && event.type !== "test:fail") continue;
    const { data } = event;
    const error = data.details?.error;
    yield `${JSON.stringify({
      outcome: event.type === "test:pass" ? "pass" : "fail",
      file: data.file ?? null,
      name: data.name,
      nesting: data.nesting,
      suite: data.details?.type === "suite",
      error: error ? String(error.cause ?? error.message ?? error) : null,
    })}\n`;
  }
}
