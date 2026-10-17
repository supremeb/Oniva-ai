/**
 * Backend model policy client and usage reporter (U9) against the fake gateway.
 */
import { afterEach, describe, expect, it } from "vitest";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { ManualClock } from "../../src/core/clock.js";
import { Context } from "../../src/core/context.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import {
  BackendPolicyClient,
  requestIdFor,
  UsageReporter,
} from "../../src/worker/backend-policy.js";
import { workerHostPolicy } from "../../src/worker/worker.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import { FakeGateway } from "../support/fake-gateway.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

let gateways: FakeGateway[] = [];
afterEach(async () => {
  await Promise.all(gateways.map((g) => g.close()));
  gateways = [];
});

async function start(): Promise<FakeGateway> {
  const gw = await FakeGateway.start([]);
  gateways.push(gw);
  return gw;
}

const CONVERSATION = { id: "conv-1", tenantId: "t1", projectId: "p1" };

function info(purpose: "generation" | "compaction" = "generation", round = 2): Any {
  return {
    ctx: Context.background(),
    purpose,
    conversation: CONVERSATION,
    agent: { model: "hint-model" },
    runId: "run-1",
    round,
  };
}

function client(
  gw: FakeGateway,
  extra: Partial<ConstructorParameters<typeof BackendPolicyClient>[0]> = {},
) {
  return new BackendPolicyClient({
    baseUrl: `${gw.baseUrl}/`,
    serviceToken: gw.serviceToken,
    supportedProviders: () => ["fake"],
    retryMs: 0,
    ...extra,
  });
}

/** A virtual clock: `sleep` advances `now` and records each wait; no real time passes. */
function virtualTime() {
  let t = 1_000_000;
  const waits: number[] = [];
  return {
    waits,
    now: () => t,
    random: () => 1,
    sleep: async (ms: number, signal?: AbortSignal) => {
      if (signal?.aborted) return;
      waits.push(ms);
      t += ms;
    },
  };
}

describe("BackendPolicyClient", () => {
  it("posts the request and returns the key for that request", async () => {
    const gw = await start();
    gw.scriptPolicy({ ok: true, apiKey: "sk-tenant", model: "m-1", maxTokens: 77 });
    const res = await client(gw).resolveModelRequest(info());
    expect(res).toEqual({
      ok: true,
      provider: "fake",
      model: "m-1",
      apiKey: "sk-tenant",
      maxTokens: 77,
    });
    const [req] = gw.policyRequests();
    expect(req?.headers.authorization).toBe(`Bearer ${gw.serviceToken}`);
    expect(req?.body).toEqual({
      tenantId: "t1",
      projectId: "p1",
      conversationId: "conv-1",
      engineConversationId: "conv-1",
      runId: "run-1",
      round: 2,
      purpose: "generation",
      requestId: "run-1:2:generation",
      agent: { model: "hint-model" },
      supportedProviders: ["fake"],
    });
  });

  it("sends the root conversation id for a subagent's child conversation", async () => {
    const gw = await start();
    const child = { ...CONVERSATION, id: "conv_child", rootId: "conv-1" };
    await client(gw).resolveModelRequest({ ...info(), conversation: child });
    expect(gw.policyRequests()[0]?.body).toMatchObject({
      conversationId: "conv-1",
      engineConversationId: "conv_child",
    });
  });

  it("maps a backend denial to a denial with the backend's code", async () => {
    const gw = await start();
    gw.scriptPolicy({ ok: false, code: "quota_exceeded", reason: "monthly token quota used" });
    const res = await client(gw).resolveModelRequest(info("compaction"));
    expect(res).toEqual({ ok: false, code: "quota_exceeded", reason: "monthly token quota used" });
    expect(gw.policyRequests()[0]?.body).toMatchObject({
      purpose: "compaction",
      requestId: "run-1:2:compaction",
    });
  });

  it("denies with policy_unavailable on transport failure, HTTP error or junk, never a static key", async () => {
    const gw = await start();
    gw.scriptPolicy({ status: 503 }, { status: 200, body: { ok: true, provider: "fake" } });
    expect(await client(gw).resolveModelRequest(info())).toMatchObject({
      ok: false,
      code: "policy_unavailable",
    });
    expect(await client(gw).resolveModelRequest(info())).toMatchObject({
      ok: false,
      code: "policy_unavailable",
    });
    const down = client(gw, { baseUrl: "http://127.0.0.1:1" });
    expect(await down.resolveModelRequest(info())).toMatchObject({
      ok: false,
      code: "policy_unavailable",
    });
  });

  it("times out a hung backend", async () => {
    const gw = await start();
    gw.scriptPolicy({ hang: true });
    const res = await client(gw, { timeoutMs: 50 }).resolveModelRequest(info());
    expect(res).toMatchObject({ ok: false, code: "policy_unavailable" });
  });

  it("never logs the key", async () => {
    const gw = await start();
    gw.scriptPolicy({ ok: true, apiKey: "sk-secret-123" }, { status: 500 });
    const lines: string[] = [];
    const c = client(gw, { log: (e, f) => lines.push(JSON.stringify({ e, f })) });
    await c.resolveModelRequest(info());
    await c.resolveModelRequest(info());
    expect(lines.join("\n")).not.toContain("sk-secret-123");
  });

  it("is the host policy's model resolution when configured, with approval still local", async () => {
    const gw = await start();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "x",
        model: "x",
        summaryModel: "x",
        maxTokens: 1,
        apiKey: () => "local-key",
      },
      gateway: { requiresApproval: (n) => n === "danger" },
      approvalTtlMs: null,
      clock: new ManualClock(),
      backend: client(gw),
    });
    gw.scriptPolicy({ ok: true, apiKey: "sk-backend" });
    expect(await policy.resolveModelRequest(info())).toMatchObject({ apiKey: "sk-backend" });
    gw.scriptPolicy({ status: 500 });
    expect(await policy.resolveModelRequest(info())).toMatchObject({
      ok: false,
      code: "policy_unavailable",
    });
    const req = { toolName: "danger" } as Any;
    expect(await policy.authorizeTool(req)).toMatchObject({ decision: "requireApproval" });
  });
});

describe("BackendPolicyClient retries", () => {
  it("retries a dropped connection with the same request id and resolves once", async () => {
    const gw = await start();
    gw.scriptPolicy({ reset: true }, { ok: true, apiKey: "sk-after" });
    const lines: Array<[string, Any]> = [];
    const c = client(gw, { ...virtualTime(), retryMs: 60_000, log: (e, f) => lines.push([e, f]) });
    expect(await c.resolveModelRequest(info())).toMatchObject({ ok: true, apiKey: "sk-after" });
    const posts = gw.policyRequests();
    expect(posts).toHaveLength(2);
    expect(posts[0]?.body).toEqual(posts[1]?.body);
    expect(lines.filter(([e]) => e === "policy_retry")).toHaveLength(1);
    expect(lines.find(([e]) => e === "policy_retry")?.[1]).toMatchObject({
      requestId: "run-1:2:generation",
      attempt: 1,
    });
  });

  it("retries 500, 500 then succeeds with growing, capped delays", async () => {
    const gw = await start();
    gw.scriptPolicy({ status: 500 }, { status: 502 }, { status: 504 }, { ok: true });
    const time = virtualTime();
    const c = client(gw, { ...time, retryMs: 60_000, baseDelayMs: 100, maxDelayMs: 250 });
    expect(await c.resolveModelRequest(info())).toMatchObject({ ok: true });
    expect(gw.policyRequests()).toHaveLength(4);
    expect(time.waits).toEqual([100, 200, 250]);
  });

  it("retries a timeout", async () => {
    const gw = await start();
    gw.scriptPolicy({ hang: true }, { ok: true });
    const c = client(gw, { ...virtualTime(), retryMs: 60_000, timeoutMs: 50 });
    expect(await c.resolveModelRequest(info())).toMatchObject({ ok: true });
    expect(gw.policyRequests()).toHaveLength(2);
  });

  it("does not retry a definitive answer", async () => {
    for (const answer of [
      { status: 403 },
      { status: 401 },
      { status: 422 },
      { status: 404 },
      { ok: false as const, code: "quota_exceeded", reason: "used up" },
    ]) {
      const gw = await start();
      gw.scriptPolicy(answer, { ok: true });
      const time = virtualTime();
      const res = await client(gw, { ...time, retryMs: 60_000 }).resolveModelRequest(info());
      expect(res).toMatchObject({ ok: false });
      expect(res).not.toHaveProperty("transient");
      expect(gw.policyRequests()).toHaveLength(1);
      expect(time.waits).toEqual([]);
    }
  });

  it("does not retry a malformed 200", async () => {
    const gw = await start();
    gw.scriptPolicy({ status: 200, body: { ok: true, provider: "fake" } }, { ok: true });
    const res = await client(gw, { ...virtualTime(), retryMs: 60_000 }).resolveModelRequest(info());
    expect(res).toMatchObject({ ok: false, code: "policy_unavailable" });
    expect(res).not.toHaveProperty("transient");
    expect(gw.policyRequests()).toHaveLength(1);
  });

  it("honours Retry-After on 429 and 503, not on 500", async () => {
    const gw = await start();
    gw.scriptPolicy(
      { status: 429, headers: { "retry-after": "7" } },
      { status: 503, headers: { "retry-after": "3" } },
      { status: 500, headers: { "retry-after": "50" } },
      { ok: true },
    );
    const time = virtualTime();
    const c = client(gw, { ...time, retryMs: 60_000, baseDelayMs: 100 });
    expect(await c.resolveModelRequest(info())).toMatchObject({ ok: true });
    expect(time.waits).toEqual([7000, 3000, 400]);
  });

  it("gives up at once when Retry-After is longer than the remaining budget", async () => {
    const gw = await start();
    gw.scriptPolicy({ status: 429, headers: { "retry-after": "120" } }, { ok: true });
    const time = virtualTime();
    const res = await client(gw, { ...time, retryMs: 60_000 }).resolveModelRequest(info());
    expect(res).toMatchObject({ ok: false, code: "policy_unavailable", transient: true });
    expect(gw.policyRequests()).toHaveLength(1);
    expect(time.waits).toEqual([]);
  });

  it("returns a transient policy_unavailable once the budget is spent", async () => {
    const gw = await start();
    gw.defaultPolicy = { status: 500 };
    const time = virtualTime();
    const lines: string[] = [];
    const c = client(gw, {
      ...time,
      retryMs: 1000,
      baseDelayMs: 300,
      maxDelayMs: 300,
      log: (e) => lines.push(e),
    });
    const res = await c.resolveModelRequest(info());
    expect(res).toMatchObject({ ok: false, code: "policy_unavailable", transient: true });
    expect(time.waits.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(1000);
    expect(gw.policyRequests()).toHaveLength(time.waits.length + 1);
    expect(lines).toContain("policy_error");
    expect(lines.filter((e) => e === "policy_retry")).toHaveLength(time.waits.length);
  });

  it("does not retry when retryMs is 0", async () => {
    const gw = await start();
    gw.scriptPolicy({ status: 500 }, { ok: true });
    const res = await client(gw, { ...virtualTime(), retryMs: 0 }).resolveModelRequest(info());
    expect(res).toMatchObject({ ok: false, transient: true });
    expect(gw.policyRequests()).toHaveLength(1);
  });

  it("stops retrying when the run aborts during a backoff", async () => {
    const gw = await start();
    gw.defaultPolicy = { status: 500 };
    const ctx = Context.background();
    let sleeping!: () => void;
    const slept = new Promise<void>((r) => {
      sleeping = r;
    });
    const c = client(gw, {
      retryMs: 60_000,
      sleep: (_ms, signal) =>
        new Promise<void>((resolve) => {
          sleeping();
          signal?.addEventListener("abort", () => resolve(), { once: true });
        }),
    });
    const pending = c.resolveModelRequest({ ...info(), ctx });
    await slept;
    ctx.cancel();
    const res = await pending;
    expect(res).toMatchObject({ ok: false });
    expect(res).not.toHaveProperty("transient");
    expect(gw.policyRequests()).toHaveLength(1);
  });

  it("makes no request when the run is already aborted", async () => {
    const gw = await start();
    const ctx = Context.background();
    ctx.cancel();
    const res = await client(gw, { retryMs: 60_000 }).resolveModelRequest({ ...info(), ctx });
    expect(res).toMatchObject({ ok: false });
    expect(gw.policyRequests()).toHaveLength(0);
  });

  it("rides out a short outage: the run completes with one decision and one usage row", async () => {
    const gw = await start();
    gw.scriptPolicy({ reset: true }, { status: 500 }, { ok: true, apiKey: "sk-ok" });
    const time = virtualTime();
    const policyClient = client(gw, { ...time, retryMs: 60_000 });
    const fx = await memoryBackend.fixture();
    const rep = new UsageReporter({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      ...time,
    });
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: policyClient,
    });
    fx.provider.enqueue(fakeText("hello"));
    const w = await fx.open({ policy, hooks: rep.hooks });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [] },
    });
    const settled = await (await conv.submit({ text: "hi" })).wait();
    expect(settled.status).toBe("done");
    await rep.flush();
    expect(gw.policyRequests()).toHaveLength(3);
    expect(new Set(gw.policyRequests().map((r) => (r.body as Any).requestId)).size).toBe(1);
    expect(gw.usage).toHaveLength(1);
    await w.crash();
  });

  it("fails the run with a retryable network error after the budget, not a denial", async () => {
    const gw = await start();
    gw.defaultPolicy = { status: 500 };
    const time = virtualTime();
    const fx = await memoryBackend.fixture();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: client(gw, { ...time, retryMs: 2000 }),
    });
    const w = await fx.open({ policy });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [] },
    });
    const settled = await (await conv.submit({ text: "hi" })).wait();
    expect(settled.status).not.toBe("done");
    const entries = await conv.entries();
    const err = entries.find((e: Any) => e.kind === "run_error") as Any;
    expect(err.payload.error).toMatchObject({
      kind: "network",
      code: "policy_unavailable",
      retryable: true,
    });
    expect(fx.provider.requests).toHaveLength(0);
    await w.crash();
  });
});

describe("UsageReporter", () => {
  function reporter(
    gw: FakeGateway,
    extra: Partial<ConstructorParameters<typeof UsageReporter>[0]> = {},
  ) {
    return new UsageReporter({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      baseDelayMs: 1,
      sleep: async () => {},
      ...extra,
    });
  }

  async function run(gw: FakeGateway, rep: UsageReporter, hooks = true) {
    const fx = await memoryBackend.fixture();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: client(gw),
    });
    fx.provider.enqueue(fakeText("hello"));
    const w = await fx.open({ policy, ...(hooks ? { hooks: rep.hooks } : {}) });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [] },
    });
    const settled = await (await conv.submit({ text: "hi" })).wait();
    expect(settled.status).toBe("done");
    await rep.flush();
    await w.crash();
    return fx;
  }

  it("posts once per response with the policy's request id, and the key is used only for the request", async () => {
    const gw = await start();
    gw.scriptPolicy({ ok: true, apiKey: "sk-once", model: "m-1" });
    const rep = reporter(gw);
    const fx = await run(gw, rep);
    expect(fx.provider.requests[0]?.apiKey).toBe("sk-once");
    const policyBody = gw.policyRequests()[0]?.body as Any;
    expect(gw.usageRequests()).toHaveLength(1);
    const [row] = gw.usage;
    expect(row).toMatchObject({
      tenantId: "t1",
      projectId: "p1",
      runId: policyBody.runId,
      requestId: policyBody.requestId,
      provider: "fake",
      status: "success",
    });
    expect(row?.requestId).toBe(requestIdFor((row as Any).runId, 0, "generation"));
    expect((row as Any).usage).toMatchObject({ inputTokens: expect.any(Number) });
    expect(JSON.stringify(gw.requests.filter((r) => r.path.endsWith("/usage")))).not.toContain(
      "sk-once",
    );
  });

  it("tags child-conversation usage with the subagent tool name and leaves the root untagged", async () => {
    const gw = await start();
    const rep = reporter(gw);
    const fx = await memoryBackend.fixture();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: client(gw),
    });
    const worker = createSubagentTool({
      name: "research_worker",
      description: "Research",
      systemPrompt: "child",
      tools: [],
    });
    fx.provider.enqueue(
      fakeToolCall("research_worker", { prompt: "find x" }, { id: "c1" }),
      fakeText("x is 42"),
      fakeText("done"),
    );
    const w = await fx.open({ policy, hooks: rep.hooks, tools: [worker] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["research_worker"] },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("done");
    await rep.flush();
    await w.crash();
    const rows = gw.usage as Any[];
    expect(rows).toHaveLength(3);
    const child = rows.filter((r) => r.engineConversationId !== r.conversationId);
    const root = rows.filter((r) => r.engineConversationId === r.conversationId);
    expect(child).toHaveLength(1);
    expect(child[0].agentRole).toBe("research_worker");
    expect(root).toHaveLength(2);
    for (const r of root) expect("agentRole" in r).toBe(false);
  });

  it("the fake gateway rejects a malformed agentRole with 422 [usage-agent-role-422]", async () => {
    const gw = await start();
    const post = (agentRole: string) =>
      fetch(`${gw.baseUrl}/internal/models/usage`, {
        method: "POST",
        headers: { authorization: `Bearer ${gw.serviceToken}`, "content-type": "application/json" },
        body: JSON.stringify({
          tenantId: "t1",
          runId: "r",
          requestId: `q-${agentRole}`,
          provider: "fake",
          model: "m",
          agentRole,
        }),
      });
    expect((await post("Bad Role!")).status).toBe(422);
    expect((await post("x".repeat(65))).status).toBe(422);
    expect((await post("research_worker")).status).toBe(200);
  });

  it("denied policy blocks the model request and reports no usage", async () => {
    const gw = await start();
    gw.scriptPolicy({ ok: false, code: "no_key", reason: "no key" });
    const rep = reporter(gw);
    const fx = await memoryBackend.fixture();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: client(gw),
    });
    const w = await fx.open({ policy, hooks: rep.hooks });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [] },
    });
    const settled = await (await conv.submit({ text: "hi" })).wait();
    expect(settled.status).not.toBe("done");
    expect(fx.provider.requests).toHaveLength(0);
    await rep.flush();
    expect(gw.usageRequests()).toHaveLength(0);
    await w.crash();
  });

  it("retries network errors and 5xx with the same body; the ledger counts it once", async () => {
    const gw = await start();
    gw.failUsage(503, 500);
    const rep = reporter(gw);
    await run(gw, rep);
    const posts = gw.usageRequests();
    expect(posts).toHaveLength(3);
    expect(new Set(posts.map((p) => JSON.stringify(p.body))).size).toBe(1);
    expect(gw.usage).toHaveLength(1);
  });

  it("gives up after the attempt limit without blocking the conversation", async () => {
    const gw = await start();
    gw.failUsage(500, 500, 500);
    const lines: string[] = [];
    const rep = reporter(gw, { attempts: 3, log: (e) => lines.push(e) });
    await run(gw, rep);
    expect(gw.usageRequests()).toHaveLength(3);
    expect(gw.usage).toHaveLength(0);
    expect(lines).toContain("usage_failed");
  });

  it("keeps retrying through an outage longer than four attempts, within the time budget", async () => {
    const gw = await start();
    gw.failUsage(503, 503, 502, 500, 500, 504);
    const time = virtualTime();
    const rep = reporter(gw, { ...time, retryMs: 60_000, baseDelayMs: 100 });
    await run(gw, rep);
    expect(gw.usageRequests()).toHaveLength(7);
    expect(gw.usage).toHaveLength(1);
  });

  it("gives up when the time budget is spent", async () => {
    const gw = await start();
    gw.failUsage(...Array.from({ length: 50 }, () => 503));
    const time = virtualTime();
    const lines: string[] = [];
    const rep = reporter(gw, {
      ...time,
      retryMs: 1000,
      baseDelayMs: 400,
      maxDelayMs: 400,
      log: (e) => lines.push(e),
    });
    await run(gw, rep);
    expect(gw.usage).toHaveLength(0);
    expect(lines).toContain("usage_failed");
    expect(time.waits.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(1000);
  });

  it("drops a client error without retrying", async () => {
    const gw = await start();
    gw.failUsage(404);
    const lines: string[] = [];
    const rep = reporter(gw, { log: (e) => lines.push(e) });
    await run(gw, rep);
    expect(gw.usageRequests()).toHaveLength(1);
    expect(lines).toContain("usage_dropped");
  });

  it("retries a connection error", async () => {
    const gw = await start();
    let calls = 0;
    const rep = reporter(gw, {
      fetch: (input, init) => {
        calls++;
        if (calls === 1) return Promise.reject(new TypeError("fetch failed"));
        return fetch(input, init);
      },
    });
    await run(gw, rep);
    expect(calls).toBe(2);
    expect(gw.usage).toHaveLength(1);
  });

  it("does not report after close", async () => {
    const gw = await start();
    const rep = reporter(gw);
    await rep.close();
    await run(gw, rep);
    await waitFor(() => true);
    expect(gw.usageRequests()).toHaveLength(0);
  });
});

describe("UsageReporter outbox", () => {
  function reporter(
    gw: FakeGateway,
    outbox: MemoryStorageProvider,
    extra: Partial<ConstructorParameters<typeof UsageReporter>[0]> = {},
  ) {
    return new UsageReporter({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      baseDelayMs: 1,
      sleep: async () => {},
      outbox,
      claimTimeoutMs: 0,
      ...extra,
    });
  }

  /** Run one response through the harness so `afterResponse` reports its usage. */
  async function run(gw: FakeGateway, rep: UsageReporter) {
    const fx = await memoryBackend.fixture();
    const policy = workerHostPolicy({
      modelDefaults: {
        provider: "fake",
        model: "m",
        summaryModel: "m",
        maxTokens: 10,
        apiKey: () => "k",
      },
      gateway: null,
      approvalTtlMs: null,
      clock: fx.clock,
      backend: client(gw),
    });
    fx.provider.enqueue(fakeText("hello"));
    const w = await fx.open({ policy, hooks: rep.hooks });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [] },
    });
    const settled = await (await conv.submit({ text: "hi" })).wait();
    expect(settled.status).toBe("done");
    await rep.flush();
    await w.crash();
    return fx;
  }

  it("leaves the outbox empty after a delivered report (enqueued then acked)", async () => {
    const gw = await start();
    const outbox = new MemoryStorageProvider();
    await run(gw, reporter(gw, outbox));
    expect(gw.usage).toHaveLength(1);
    expect(gw.usageRequests()).toHaveLength(1);
    expect(await outbox.claimPending(0)).toEqual([]);
  });

  it("keeps a row when the backend is down for the budget, then flushes it exactly once", async () => {
    const gw = await start();
    gw.failUsage(503);
    const outbox = new MemoryStorageProvider();
    const rep = reporter(gw, outbox, { retryMs: 0 });
    await run(gw, rep);
    expect(gw.usage).toHaveLength(0);
    const [attempted] = gw.usageRequests();
    const [parked] = await outbox.claimPending(0);
    expect(parked?.requestId).toBe((attempted as Any).body.requestId);

    // Backend recovered: the flusher redelivers the parked row and deletes it.
    await rep.flushOutbox();
    expect(gw.usage).toHaveLength(1);
    expect(gw.usageRequests()).toHaveLength(2);
    expect(await outbox.claimPending(0)).toEqual([]);
  });

  it("delivers pending rows after a restart (a new reporter over the same outbox)", async () => {
    const gw = await start();
    gw.failUsage(503);
    const outbox = new MemoryStorageProvider();
    await run(gw, reporter(gw, outbox, { retryMs: 0 }));
    expect(gw.usage).toHaveLength(0);

    const restarted = reporter(gw, outbox);
    await restarted.flushOutbox(); // like a worker start
    expect(gw.usage).toHaveLength(1);
    expect(await outbox.claimPending(0)).toEqual([]);
  });

  it("acks a redelivered report the ledger already recorded", async () => {
    const gw = await start();
    const outbox = new MemoryStorageProvider();
    const rep = reporter(gw, outbox);
    await run(gw, rep);
    const recorded = gw.usage[0] as Any;
    // The same report is parked again (a crash after the ledger committed, before ack).
    await outbox.enqueue({
      requestId: recorded.requestId,
      body: recorded,
      createdAt: Date.now(),
      claimedAt: null,
    });
    await rep.flushOutbox();
    expect(gw.usage).toHaveLength(1); // recorded: false; the ledger kept one row
    expect(await outbox.claimPending(0)).toEqual([]);
  });

  it("logs usage_stale for a row past the max age but still delivers it", async () => {
    const gw = await start();
    const outbox = new MemoryStorageProvider();
    const lines: string[] = [];
    const rep = reporter(gw, outbox, { log: (e) => lines.push(e), staleAgeMs: 1_000 });
    await outbox.enqueue({
      requestId: "req-stale",
      body: {
        tenantId: "t1",
        runId: "r1",
        requestId: "req-stale",
        provider: "fake",
        model: "m",
        usage: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 },
        status: "success",
      },
      createdAt: Date.now() - 10_000,
      claimedAt: null,
    });
    await rep.flushOutbox();
    expect(gw.usage).toHaveLength(1);
    expect(lines).toContain("usage_stale");
  });
});

describe("UsageReporter outbox failures", () => {
  const EVENT: Any = {
    runId: "run-x",
    round: 1,
    conversation: CONVERSATION,
    message: { provider: "fake", model: "m" },
    usage: { inputTokens: 1, outputTokens: 2, cacheReadTokens: 0, cacheWriteTokens: 0 },
  };

  function stub(failOn: "enqueue" | "ack" | "claim") {
    const inner = new MemoryStorageProvider();
    const calls = { enqueue: 0, ack: 0, claim: 0 };
    const boom = (): never => {
      throw new Error("pg down");
    };
    return {
      inner,
      calls,
      outbox: {
        enqueue: async (r: Any) => {
          calls.enqueue++;
          if (failOn === "enqueue") boom();
          return inner.enqueue(r);
        },
        ack: async (id: string) => {
          calls.ack++;
          if (failOn === "ack") boom();
          return inner.ack(id);
        },
        claimPending: async (ms: number) => {
          calls.claim++;
          if (failOn === "claim") boom();
          return inner.claimPending(ms);
        },
      },
    };
  }

  function make(gw: FakeGateway, outbox: Any, lines: string[]) {
    return new UsageReporter({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      retryMs: 0,
      outbox,
      claimTimeoutMs: 0,
      log: (e) => lines.push(e),
    });
  }

  async function noUnhandled(fn: () => Promise<void>) {
    const seen: unknown[] = [];
    const h = (e: unknown) => seen.push(e);
    process.on("unhandledRejection", h);
    try {
      await fn();
      await new Promise((r) => setTimeout(r, 20));
    } finally {
      process.off("unhandledRejection", h);
    }
    expect(seen).toEqual([]);
  }

  it("still sends the report when the enqueue throws, and logs usage_outbox_error", async () => {
    const gw = await start();
    const s = stub("enqueue");
    const lines: string[] = [];
    const rep = make(gw, s.outbox, lines);
    await noUnhandled(async () => {
      rep.report(EVENT);
      await rep.flush();
    });
    expect(gw.usage).toHaveLength(1);
    expect(lines).toContain("usage_outbox_error");
    // The worker keeps going: a later report is still handled.
    rep.report({ ...EVENT, round: 2 });
    await rep.flush();
    expect(gw.usage).toHaveLength(2);
  });

  it("leaves the row when the ack throws (no crash, report was sent)", async () => {
    const gw = await start();
    const s = stub("ack");
    const lines: string[] = [];
    const rep = make(gw, s.outbox, lines);
    await noUnhandled(async () => {
      rep.report(EVENT);
      await rep.flush();
    });
    expect(gw.usage).toHaveLength(1);
    expect(lines).toContain("usage_outbox_error");
    expect(await s.inner.claimPending(0)).toHaveLength(1);
  });

  it("catches and logs a flush that cannot claim rows", async () => {
    const gw = await start();
    const s = stub("claim");
    const lines: string[] = [];
    const rep = make(gw, s.outbox, lines);
    await noUnhandled(async () => {
      rep.startFlushing();
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(lines).toContain("usage_outbox_error");
    await rep.close();
  });

  it("keeps the row and logs usage_auth_error on 401 and 403", async () => {
    for (const status of [401, 403]) {
      const gw = await start();
      gw.failUsage(status);
      const inner = new MemoryStorageProvider();
      const lines: string[] = [];
      const rep = make(gw, inner, lines);
      rep.report(EVENT);
      await rep.flush();
      expect(lines).toContain("usage_auth_error");
      expect(lines).not.toContain("usage_dropped");
      expect(await inner.claimPending(0)).toHaveLength(1);
    }
  });
});
