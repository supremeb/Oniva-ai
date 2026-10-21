import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool } from "../../src/tools/registry.js";
import { memoryBackend } from "../agent/support.js";
import { AUTH, call, serve, TENANT, TOKEN } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

async function start() {
  const fx = await memoryBackend.fixture();
  const research = createSubagentTool({ name: "research", tools: ["wait"] });
  const wait = defineTool({
    name: "wait",
    description: "Waits",
    parameters: z.object({}),
    replay: "safe",
    execute: async () => ({ content: "waited" }),
  });
  const w = await fx.open({ tools: [research, wait], settings: { progressIntervalMs: 0 } });
  const srv = await serve(w.harness);
  cleanups.push(() => w.crash());
  cleanups.push(() => srv.close());
  return { fx, w, url: srv.url };
}

async function runWithChild(s: Awaited<ReturnType<typeof start>>): Promise<string> {
  s.fx.provider.enqueue(
    fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
    fakeText("found"),
    fakeText("done"),
  );
  const created = await call(s.url, "POST", "/v1/conversations", {
    headers: AUTH,
    body: { agent: { systemPrompt: "s", tools: ["research"] } },
  });
  const id = created.body.conversationId as string;
  const conv = s.w.harness.conversation(id);
  await (await conv.submit({ text: "go" })).wait();
  return id;
}

describe("GET /v1/conversations/:id/trace", () => {
  it("returns the root and its subagent child to the bound caller", async () => {
    const s = await start();
    const id = await runWithChild(s);
    const res = await call(s.url, "GET", `/v1/conversations/${id}/trace`, { headers: AUTH });
    expect(res.status).toBe(200);
    expect(res.body.conversationId).toBe(id);
    expect(res.body.entries.length).toBeGreaterThan(0);
    const children = Object.values(res.body.children) as Any[];
    expect(children).toHaveLength(1);
    expect(children[0].role).toBe("research");
    expect(res.body.truncated).toBe(false);
  });

  it("pages with limit and cursor", async () => {
    const s = await start();
    const id = await runWithChild(s);
    const first = await call(s.url, "GET", `/v1/conversations/${id}/trace?limit=2`, {
      headers: AUTH,
    });
    expect(first.status).toBe(200);
    expect(first.body.truncated).toBe(true);
    const next = await call(
      s.url,
      "GET",
      `/v1/conversations/${id}/trace?cursor=${encodeURIComponent(first.body.nextCursor)}`,
      { headers: AUTH },
    );
    expect(next.status).toBe(200);
    expect(next.body.truncated).toBe(false);
  });

  it("400s a bad limit or cursor", async () => {
    const s = await start();
    const id = await runWithChild(s);
    for (const q of ["limit=0", "limit=abc", "cursor=nope"]) {
      const res = await call(s.url, "GET", `/v1/conversations/${id}/trace?${q}`, {
        headers: AUTH,
      });
      expect(res.status, q).toBe(400);
    }
  });

  it("401 without the service token, 403 for another tenant or project, 404 when unknown", async () => {
    const s = await start();
    const id = await runWithChild(s);
    const path = `/v1/conversations/${id}/trace`;
    expect((await call(s.url, "GET", path, { headers: TENANT })).status).toBe(401);
    const wrong = { ...TENANT, authorization: `Bearer ${TOKEN}x` };
    expect((await call(s.url, "GET", path, { headers: wrong })).status).toBe(401);
    for (const headers of [
      { ...AUTH, "x-oniva-tenant": "t2" },
      { ...AUTH, "x-oniva-project": "p2" },
    ]) {
      const res = await call(s.url, "GET", path, { headers });
      expect(res.status).toBe(403);
      expect(JSON.stringify(res.body)).not.toContain("entries");
    }
    const missing = await call(s.url, "GET", "/v1/conversations/conv_missing/trace", {
      headers: AUTH,
    });
    expect(missing.status).toBe(404);
  });
});
