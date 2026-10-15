/**
 * Chat compaction (U11, AE9): a conversation that crosses the context threshold
 * compacts through the backend policy, the summary heads the next request's
 * context, the transcript keeps every entry, and the compaction model spend is
 * posted to the usage ledger with purpose `compaction`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { SUMMARY_PREFIX } from "../../src/agent/context-builder.js";
import { fakeText } from "../../src/models/fake.js";
import { BackendPolicyClient, UsageReporter } from "../../src/worker/backend-policy.js";
import { workerHostPolicy } from "../../src/worker/worker.js";
import { FakeGateway } from "../support/fake-gateway.js";
import { memoryBackend } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

let gateways: FakeGateway[] = [];
afterEach(async () => {
  await Promise.all(gateways.map((g) => g.close()));
  gateways = [];
});

const COMPACTION = {
  enabled: true,
  contextWindow: 1000,
  reserveTokens: 100,
  backgroundMargin: 200,
  keepRecentTokens: 50,
};

describe("chat compaction through the backend policy", () => {
  it("compacts past the threshold, keeps the transcript, and reports compaction usage", async () => {
    const gw = await FakeGateway.start([]);
    gateways.push(gw);
    const reporter = new UsageReporter({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      baseDelayMs: 1,
      sleep: async () => {},
    });
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
      backend: new BackendPolicyClient({
        baseUrl: gw.baseUrl,
        serviceToken: gw.serviceToken,
        supportedProviders: () => ["fake"],
      }),
    });
    // Policy answers in request order: generation, compaction (low tier), generation.
    gw.scriptPolicy(
      { ok: true, model: "fake-chat", apiKey: "sk-1" },
      { ok: true, model: "fake-low", apiKey: "sk-2" },
      { ok: true, model: "fake-chat", apiKey: "sk-3" },
    );
    fx.provider.enqueue(fakeText("first answer", { usage: { inputTokens: 950, outputTokens: 1 } }));
    fx.provider.enqueue(fakeText("SUMMARY-1", { usage: { inputTokens: 40, outputTokens: 7 } }));
    fx.provider.enqueue(fakeText("second answer"));

    const w = await fx.open({ policy, hooks: reporter.hooks });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "sys", tools: [], compaction: COMPACTION },
    });
    expect((await (await conv.submit({ text: "question one" })).wait()).status).toBe("done");
    expect((await (await conv.submit({ text: "question two" })).wait()).status).toBe("done");
    await reporter.flush();

    // The compaction request used the low tier chosen by the policy.
    const purposes = gw.policyRequests().map((r) => (r.body as Any).purpose);
    expect(purposes).toEqual(["generation", "compaction", "generation"]);
    expect(fx.provider.requests.map((r) => r.model)).toEqual([
      "fake-chat",
      "fake-low",
      "fake-chat",
    ]);

    // The transcript keeps every entry, with the compaction entry in place.
    const entries = await conv.entries();
    expect(entries.map((e) => e.kind)).toEqual([
      "user",
      "system",
      "assistant",
      "user",
      "compaction",
      "assistant",
    ]);

    // The next request's context starts with the summary.
    expect(fx.provider.requests[2]?.messages).toEqual([
      { role: "system", text: "sys" },
      { role: "user", content: `${SUMMARY_PREFIX}SUMMARY-1` },
      { role: "user", content: "question two" },
    ]);

    // The ledger gets one row per response, the summary one with purpose compaction.
    const rows = gw.usage as Any[];
    expect(rows.map((r) => r.purpose)).toEqual(["generation", "compaction", "generation"]);
    const compaction = rows[1];
    expect(compaction).toMatchObject({
      model: "fake-low",
      usage: { inputTokens: 40, outputTokens: 7 },
      status: "success",
    });
    expect(compaction.requestId).toMatch(/^[^:]+:\d+:compaction:\S+$/);
    expect(new Set(rows.map((r) => r.requestId)).size).toBe(3);
    await w.crash();
  });
});
