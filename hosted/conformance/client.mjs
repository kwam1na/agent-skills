// A raw HTTP client: the suite needs to send what fetch would normalise or refuse (a foreign Host, encoded and
// backslashed paths, chunked bodies with no Content-Length) and to tell a closed connection from a response.
import http from "node:http";
import https from "node:https";

export function parseOrigin(origin) {
  let url;
  try {
    url = new URL(origin);
  } catch {
    throw new Error(`not an origin: ${origin}`);
  }
  if (!["http:", "https:"].includes(url.protocol) || url.pathname !== "/" || url.search || url.hash ||
      url.username || url.password || !url.hostname || origin.replace(/\/$/, "") !== url.origin)
    throw new Error(`an origin is a scheme, a host and an optional port, with no path: ${origin}`);
  return url;
}

export function createClient(origin, { timeoutMs = 60_000 } = {}) {
  const base = parseOrigin(origin);
  const transport = base.protocol === "https:" ? https : http;
  /** Every response this client received, for the header audit. */
  const responses = [];
  function send({ method = "GET", path, headers = {}, body, chunked = false, label }) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      const outgoing = { ...headers };
      if (body !== undefined && !chunked && !Object.keys(outgoing).some((key) => key.toLowerCase() === "content-length"))
        outgoing["Content-Length"] = String(body.length);
      if (chunked) outgoing["Transfer-Encoding"] = "chunked";
      const request = transport.request(
        { protocol: base.protocol, hostname: base.hostname, port: base.port || undefined, method, path,
          headers: outgoing, agent: false },
        (response) => {
          const chunks = [];
          response.on("data", (chunk) => chunks.push(chunk));
          response.on("end", () => {
            const bytes = Buffer.concat(chunks);
            const row = { label, method, path, status: response.statusCode, headers: response.headers, bytes,
              text: bytes.toString("utf8") };
            responses.push(row);
            finish(row);
          });
          response.on("error", (error) => finish({ label, method, path, closed: true, error: error.code || error.message }));
        },
      );
      const timer = setTimeout(() => request.destroy(Object.assign(new Error("timed out"), { code: "ETIMEDOUT" })), timeoutMs);
      // A write error after the response arrived belongs to that response, which still settles this call.
      request.on("error", (error) => {
        if (!request.res) finish({ label, method, path, closed: true, error: error.code || error.message });
      });
      if (body === undefined) return request.end();
      if (!chunked) return request.end(body);
      // Streamed in pieces, so the receiver can only count, never trust a declared length.
      let offset = 0;
      const step = 64 * 1024;
      const pump = () => {
        while (offset < body.length) {
          const piece = body.subarray(offset, offset + step);
          offset += piece.length;
          if (!request.write(piece)) return request.once("drain", pump);
        }
        request.end();
      };
      pump();
    });
  }
  return { origin: base.origin, host: base.host, send, responses };
}

export function json(response) {
  if (response.closed) return undefined;
  try {
    return JSON.parse(response.text);
  } catch {
    return undefined;
  }
}
