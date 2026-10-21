import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { modelFetch } from "../../src/models/http.js";

describe("modelFetch (the model connection pool)", () => {
  const seen: string[] = [];
  const server = createServer((req, res) => {
    seen.push(`${req.method} ${req.httpVersion}`);
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.end("data: [DONE]\n\n");
  });
  let base = "";
  beforeAll(async () => {
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterAll(() => new Promise<void>((r) => server.close(() => r())));

  it("speaks HTTP/1.1, streams the body and passes method, headers and body through", async () => {
    const res = await modelFetch(`${base}/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("data: [DONE]\n\n");
    expect(seen.at(-1)).toBe("POST 1.1");
  });

  it("honours an abort signal", async () => {
    const ctl = new AbortController();
    ctl.abort();
    await expect(modelFetch(`${base}/x`, { signal: ctl.signal })).rejects.toThrow();
  });
});
