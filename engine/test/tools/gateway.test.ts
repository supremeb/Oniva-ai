/**
 * Tool gateway client (U13) against the fake gateway.
 */
import { afterEach, describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { LeaseLost } from "../../src/core/errors.js";
import type { JsonObject } from "../../src/core/json.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import {
  createGatewayTools,
  type GatewayClientOptions,
  type GatewayToolset,
  gatewayParameters,
} from "../../src/tools/gateway.js";
import {
  defineTool,
  parametersSchema,
  type ToolApi,
  type ToolDefinition,
  type ToolReconcileApi,
} from "../../src/tools/registry.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import { FakeGateway, type FakeToolSpec } from "../support/fake-gateway.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const TOOLS: FakeToolSpec[] = [
  {
    name: "search_docs",
    description: "Search project documents",
    parameters: {
      type: "object",
      properties: { query: { type: "string", minLength: 1 }, limit: { type: "integer" } },
      required: ["query"],
      additionalProperties: false,
    },
    sideEffect: "none",
    timeoutMs: 2_000,
    handler: (call) => ({ content: `found ${call.args.query}` }),
  },
  {
    name: "send_email",
    description: "Send an email",
    parameters: {
      type: "object",
      properties: { to: { type: "string" }, body: { type: "string" } },
      required: ["to", "body"],
    },
    sideEffect: "write",
    timeoutMs: 2_000,
    requiresApproval: true,
    handler: (call) => ({ content: `sent to ${call.args.to}`, details: { messageId: "m-1" } }),
  },
  {
    name: "upsert_note",
    description: "Create or replace a note",
    parameters: {
      type: "object",
      properties: { id: { type: "string" }, text: { type: "string" } },
      required: ["id", "text"],
    },
    sideEffect: "write",
    idempotent: true,
    timeoutMs: 2_000,
  },
];

const fresh = (): FakeToolSpec[] => TOOLS.map((t) => ({ ...t }));

let gateways: FakeGateway[] = [];
afterEach(async () => {
  await Promise.all(gateways.map((g) => g.close()));
  gateways = [];
});

async function startGateway(tools = fresh()): Promise<FakeGateway> {
  const gw = await FakeGateway.start(tools);
  gateways.push(gw);
  return gw;
}

function client(gw: FakeGateway, extra: Partial<GatewayClientOptions> = {}) {
  return createGatewayTools({
    baseUrl: gw.baseUrl,
    serviceToken: gw.serviceToken,
    fence: () => 7,
    retry: { attempts: 3, baseDelayMs: 1, maxDelayMs: 5 },
    ...extra,
  });
}

const CONVERSATION = { id: "conv-1", tenantId: "t1", projectId: "p1" };

function api(toolCallId: string, ctx = Context.background()): ToolApi {
  return {
    ctx,
    signal: ctx.signal,
    idempotencyKey: `run-1:${toolCallId}`,
    runId: "run-1",
    toolCallId,
    taskId: `task-${toolCallId}`,
    attempt: 1,
    conversation: CONVERSATION,
    output() {},
  };
}

function reconcileApi(toolCallId: string): ToolReconcileApi {
  const ctx = Context.background();
  return {
    ctx,
    idempotencyKey: `run-1:${toolCallId}`,
    runId: "run-1",
    toolCallId,
    taskId: `task-${toolCallId}`,
    conversation: CONVERSATION,
    partialOutput: "",
  };
}

function tool(set: GatewayToolset, name: string): ToolDefinition {
  const t = set.tools.find((x) => x.name === name);
  if (!t) throw new Error(`no tool ${name}`);
  return t;
}

describe("gateway manifest", () => {
  it("registers each manifest tool with its schema, replay policy and approval flag", async () => {
    const gw = await startGateway();
    const set = await client(gw);
    expect(set.tools.map((t) => [t.name, t.replay])).toEqual([
      ["search_docs", "safe"],
      ["send_email", "unsafe"],
      ["upsert_note", "safe"],
    ]);
    const search = tool(set, "search_docs");
    expect(search.description).toBe("Search project documents");
    const schema = parametersSchema(search);
    expect(schema).toMatchObject({
      type: "object",
      properties: { query: { type: "string", minLength: 1 }, limit: { type: "integer" } },
      required: ["query"],
      additionalProperties: false,
    });
    expect(search.parameters.safeParse({ query: "x" }).success).toBe(true);
    expect(search.parameters.safeParse({ query: "" }).success).toBe(false);
    expect(search.parameters.safeParse({ query: "x", extra: 1 }).success).toBe(false);
    expect(tool(set, "send_email").parameters.safeParse({ to: "a" }).success).toBe(false);

    expect(set.requiresApproval("send_email")).toBe(true);
    expect(set.requiresApproval("search_docs")).toBe(false);
    expect(set.requiresApproval("nope")).toBe(false);
    expect(set.describe("upsert_note")).toMatchObject({
      sideEffect: "write",
      idempotent: true,
      timeoutMs: 2_000,
    });
    expect(gw.requests[0]?.headers.authorization).toBe(`Bearer ${gw.serviceToken}`);
  });

  it("falls back to a required-keys check for a schema zod cannot convert, keeping the schema for providers", () => {
    const schema: JsonObject = {
      type: "object",
      properties: { a: { type: "string" }, b: { $ref: "https://schemas.example/b" } },
      required: ["a"],
    };
    const params = gatewayParameters(schema);
    expect(params.safeParse({ a: "x", c: 1 }).success).toBe(true);
    expect(params.safeParse({ c: 1 }).success).toBe(false);
    const t = defineTool({
      name: "t",
      description: "d",
      parameters: params,
      execute: () => ({ content: "" }),
    });
    expect(parametersSchema(t)).toMatchObject(schema);
  });

  it("retries a failing manifest load and rejects an invalid manifest", async () => {
    const gw = await startGateway();
    gw.failManifest(503);
    expect((await client(gw)).tools).toHaveLength(3);
    expect(gw.requests.filter((r) => r.path === "/internal/tools/manifest")).toHaveLength(2);

    const bad = await startGateway([
      { ...fresh()[0], sideEffect: "delete" as Any } as FakeToolSpec,
    ]);
    await expect(client(bad)).rejects.toThrow(/sideEffect/);
    await expect(client(gw, { serviceToken: "wrong" })).rejects.toThrow(/401/);
  });
});

describe("gateway invoke", () => {
  it("sends the idempotency key, fence, service token and the stored tenant", async () => {
    const gw = await startGateway();
    const set = await client(gw, { sessionId: "sess-9" });
    const result = await tool(set, "send_email").execute(
      { to: "a@x", body: "hi", tenantId: "evil" },
      api("c1"),
    );
    expect(result).toEqual({ content: "sent to a@x", details: { messageId: "m-1" } });
    const [req] = gw.invokes("send_email");
    expect(req?.headers.authorization).toBe(`Bearer ${gw.serviceToken}`);
    expect(req?.headers["idempotency-key"]).toBe("run-1:c1");
    expect(req?.headers["x-engine-fence"]).toBe("7");
    expect(req?.headers["x-engine-session"]).toBe("sess-9");
    expect(req?.body).toEqual({
      args: { to: "a@x", body: "hi", tenantId: "evil" },
      runId: "run-1",
      toolCallId: "c1",
      tenantId: "t1",
      projectId: "p1",
    });
    expect(gw.effects("send_email")).toBe(1);
  });

  it("omits the fence header when the engine holds no lease", async () => {
    const gw = await startGateway();
    const set = await client(gw, { fence: () => null });
    await tool(set, "search_docs").execute({ query: "q" }, api("c1"));
    expect(gw.invokes("search_docs")[0]?.headers["x-engine-fence"]).toBeUndefined();
  });

  it("does not retry a write that got a 503; the model sees an error", async () => {
    const gw = await startGateway();
    gw.script("send_email", { status: 503 });
    const set = await client(gw);
    const result = await tool(set, "send_email").execute({ to: "a", body: "b" }, api("c1"));
    expect(result.isError).toBe(true);
    expect(result.details).toMatchObject({ error: "gateway_error", status: 503 });
    expect(gw.invokes("send_email")).toHaveLength(1);
    expect(gw.effects("send_email")).toBe(0);
  });

  it("retries a read on 503 and returns its eventual result", async () => {
    const gw = await startGateway();
    gw.script("search_docs", { status: 503 }, { status: 502 });
    const set = await client(gw);
    const result = await tool(set, "search_docs").execute({ query: "q" }, api("c1"));
    expect(result).toEqual({ content: "found q" });
    expect(gw.invokes("search_docs")).toHaveLength(3);
    expect(gw.effects("search_docs")).toBe(1);
  });

  it("gives up on a read after the retry budget", async () => {
    const gw = await startGateway();
    gw.script("search_docs", { status: 503 }, { status: 503 }, { status: 503 }, { status: 503 });
    const set = await client(gw);
    const result = await tool(set, "search_docs").execute({ query: "q" }, api("c1"));
    expect(result.details).toMatchObject({ error: "gateway_error", status: 503 });
    expect(gw.invokes("search_docs")).toHaveLength(3);
  });

  it("does not retry a client error on a read", async () => {
    const gw = await startGateway();
    gw.script("search_docs", { status: 400, body: { error: "invalid_request", message: "bad" } });
    const set = await client(gw);
    const result = await tool(set, "search_docs").execute({ query: "q" }, api("c1"));
    expect(result.isError).toBe(true);
    expect(String(result.content)).toContain("bad");
    expect(gw.invokes("search_docs")).toHaveLength(1);
  });

  it("aborts a call past the tool's timeout and returns a timeout result", async () => {
    const tools = fresh();
    (tools[1] as FakeToolSpec).timeoutMs = 50;
    const gw = await startGateway(tools);
    gw.delay("send_email", 1_000);
    const set = await client(gw);
    const started = Date.now();
    const result = await tool(set, "send_email").execute({ to: "a", body: "b" }, api("c1"));
    expect(Date.now() - started).toBeLessThan(900);
    expect(result.isError).toBe(true);
    expect(result.details).toMatchObject({ error: "timeout", timeoutMs: 50 });
    expect(String(result.content)).toMatch(/timed out/);
    // The request was aborted, so the delayed effect never ran.
    await new Promise((r) => setTimeout(r, 1_100));
    expect(gw.effects("send_email")).toBe(0);
    expect(gw.invokes("send_email")).toHaveLength(1);
  });

  it("rethrows when the caller cancels instead of reporting a timeout", async () => {
    const gw = await startGateway();
    gw.script("search_docs", { hang: true });
    const set = await client(gw);
    const ctx = Context.background();
    const pending = tool(set, "search_docs").execute({ query: "q" }, api("c1", ctx));
    await waitFor(() => gw.invokes("search_docs").length === 1);
    ctx.cancel(new Error("stop"));
    await expect(pending).rejects.toThrow("stop");
  });

  it("a fence rejection yields lease_lost and fences the worker", async () => {
    const gw = await startGateway();
    gw.setHighWater("sess-1", 9);
    const lost: unknown[] = [];
    const set = await client(gw, {
      sessionId: "sess-1",
      fence: () => 3,
      onFenceRejected: (info) => lost.push(info),
    });
    const result = await tool(set, "send_email").execute({ to: "a", body: "b" }, api("c1"));
    expect(result.isError).toBe(true);
    expect(result.details).toMatchObject({ error: "lease_lost" });
    expect(lost).toEqual([{ tool: "send_email", idempotencyKey: "run-1:c1", token: 3 }]);
    expect(gw.effects("send_email")).toBe(0);
  });

  it("a fence that already reports the lease lost never reaches the gateway", async () => {
    const gw = await startGateway();
    const set = await client(gw, {
      fence: () => {
        throw new LeaseLost("sess-1", 3);
      },
    });
    const result = await tool(set, "send_email").execute({ to: "a", body: "b" }, api("c1"));
    expect(result.details).toMatchObject({ error: "lease_lost" });
    expect(gw.invokes("send_email")).toHaveLength(0);
  });
});

describe("gateway reconcile", () => {
  it("returns the outcome the gateway stored for the idempotency key, without re-executing", async () => {
    const gw = await startGateway();
    gw.storeOutcome("run-1:c1", { content: "sent earlier", details: { messageId: "m-0" } });
    const set = await client(gw);
    const send = tool(set, "send_email");
    expect(send.reconcile).toBeTypeOf("function");
    const outcome = await send.reconcile?.({ to: "a", body: "b" }, reconcileApi("c1"));
    expect(outcome).toEqual({ content: "sent earlier", details: { messageId: "m-0" } });
    const [req] = gw.requests.filter((r) => r.path.startsWith("/internal/tools/invocations/"));
    expect(req?.path).toBe("/internal/tools/invocations/run-1%3Ac1");
    expect(req?.headers.authorization).toBe(`Bearer ${gw.serviceToken}`);
    expect(gw.effects("send_email")).toBe(0);
  });

  it("returns null when the gateway has no outcome", async () => {
    const gw = await startGateway();
    const set = await client(gw);
    expect(await tool(set, "send_email").reconcile?.({}, reconcileApi("c2"))).toBeNull();
  });
});

describe("gateway tools in the agent loop (memory backend)", () => {
  const results = (entries: EntryRecord[]): Any[] =>
    entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

  async function setup() {
    const gw = await startGateway();
    const set = await client(gw, { fence: () => null });
    const fx = await memoryBackend.fixture();
    return { gw, set, fx };
  }

  const agent = { systemPrompt: "s", tools: ["send_email", "search_docs"] };

  it("runs a gateway write for the conversation's stored tenant", async () => {
    const { gw, set, fx } = await setup();
    fx.provider.enqueue(
      fakeToolCall("send_email", { to: "a@x", body: "hi" } as JsonObject, { id: "c1" }),
      fakeText("Sent."),
    );
    const w = await fx.open({ tools: set.tools });
    const conv = await w.harness.createConversation({ tenantId: "t9", projectId: "p9", agent });
    const settled = await (await conv.submit({ text: "send it" })).wait();
    expect(settled.status).toBe("done");
    const [result] = results(await conv.entries());
    expect(result).toMatchObject({ status: "ok", content: "sent to a@x" });
    const [req] = gw.invokes("send_email");
    expect(req?.body).toMatchObject({ tenantId: "t9", projectId: "p9", toolCallId: "c1" });
    expect(req?.headers["idempotency-key"]).toMatch(/:c1$/);
    expect(gw.effects("send_email")).toBe(1);
    await w.crash();
  });

  for (const recorded of [true, false]) {
    it(`AE1: a gateway write cut off by a crash ${recorded ? "uses the recorded outcome" : "is reported interrupted"}`, async () => {
      const { gw, set, fx } = await setup();
      gw.script("send_email", { hang: true });
      fx.provider.enqueue(
        fakeToolCall("send_email", { to: "a@x", body: "hi" } as JsonObject, { id: "c1" }),
      );
      const w1 = await fx.open({ tools: set.tools });
      const conv = await w1.harness.createConversation({ tenantId: "t1", projectId: "p1", agent });
      const sub = await conv.submit({ text: "send it" });
      await waitFor(() => gw.invokes("send_email").length === 1);
      await w1.crash();

      const key = gw.invokes("send_email")[0]?.headers["idempotency-key"] as string;
      if (recorded) gw.storeOutcome(key, { content: "sent by the gateway" });

      fx.provider.enqueue(fakeText("ok"));
      const w2 = await fx.open({ tools: set.tools });
      expect((await w2.harness.submission(sub.id).wait()).status).toBe("done");
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      if (recorded) {
        expect(result).toMatchObject({ status: "ok", content: "sent by the gateway" });
      } else {
        expect(result.status).toBe("interrupted");
        expect(result.content).toContain("may have happened");
      }
      expect(gw.invokes("send_email")).toHaveLength(1);
      expect(gw.effects("send_email")).toBe(0);
      await w2.crash();
    });
  }
});
