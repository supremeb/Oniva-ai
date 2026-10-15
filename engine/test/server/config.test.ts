import { afterEach, describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import { applyJsonPatch } from "../../src/watch/patch.js";
import type { WatchFrame } from "../../src/watch/stream.js";
import { memoryBackend } from "../agent/support.js";
import { AUTH, call, SseClient, serve } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

async function start() {
  const fx = await memoryBackend.fixture();
  const w = await fx.open();
  const srv = await serve(w.harness);
  cleanups.push(() => w.crash());
  cleanups.push(() => srv.close());
  const created = await call(srv.url, "POST", "/v1/conversations", {
    headers: AUTH,
    body: { agent: { systemPrompt: "one", tools: [] } },
  });
  return { w, url: srv.url, id: created.body.conversationId as string };
}

describe("PUT /v1/conversations/:id/agent", () => {
  it("commits a partial update and a watcher receives it as a patch", async () => {
    const { w, url, id } = await start();
    const sse = await SseClient.open(`${url}/v1/conversations/${id}/watch`, AUTH);
    let view = (await sse.frame()).frame as Any;
    view = structuredClone(view.snapshot) as JsonValue;

    const res = await call(url, "PUT", `/v1/conversations/${id}/agent`, {
      headers: AUTH,
      body: { systemPrompt: "two", metadata: { kb: ["k1"] } },
    });
    expect(res.status).toBe(200);
    expect(res.body.agent).toMatchObject({
      systemPrompt: "two",
      tools: [],
      metadata: { kb: ["k1"] },
    });
    expect(await w.harness.conversation(id).agent()).toMatchObject({ systemPrompt: "two" });

    const frame = (await sse.frame()).frame as WatchFrame;
    expect("patch" in frame).toBe(true);
    if ("patch" in frame) view = applyJsonPatch(view, frame.patch);
    expect((view as Any).agent).toMatchObject({ systemPrompt: "two", metadata: { kb: ["k1"] } });
    sse.close();
  });

  it("rejects invalid config with 400 and commits nothing", async () => {
    const { w, url, id } = await start();
    for (const body of [
      { tools: "nope" },
      { depth: -1 },
      { bogus: 1 },
      { thinking: "extreme" },
      { compaction: { enabled: "yes" } },
    ]) {
      const res = await call(url, "PUT", `/v1/conversations/${id}/agent`, { headers: AUTH, body });
      expect(res.status).toBe(400);
      expect(res.body.error).toBe("bad_request");
    }
    expect(await w.harness.conversation(id).agent()).toMatchObject({ systemPrompt: "one" });
  });

  it("requires auth and the conversation's tenant", async () => {
    const { url, id } = await start();
    const noAuth = await call(url, "PUT", `/v1/conversations/${id}/agent`, { body: {} });
    expect(noAuth.status).toBe(401);
    const other = await call(url, "PUT", `/v1/conversations/${id}/agent`, {
      headers: { ...AUTH, "x-oniva-tenant": "t2" },
      body: { systemPrompt: "x" },
    });
    expect([403, 404]).toContain(other.status);
    const missing = await call(url, "PUT", "/v1/conversations/nope/agent", {
      headers: AUTH,
      body: {},
    });
    expect(missing.status).toBe(404);
  });
});
