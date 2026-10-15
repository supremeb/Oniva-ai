/**
 * Backend model policy client and usage reporter (U9) against the fake gateway.
 */
import { afterEach, describe, expect, it } from "vitest";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { ManualClock } from "../../src/core/clock.js";
import { Context } from "../../src/core/context.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
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
    ...extra,
  });
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
