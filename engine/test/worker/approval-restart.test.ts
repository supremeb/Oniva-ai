/**
 * Approvals across a worker restart (Approvals & Policy plan, U13), at the worker level.
 *
 * A worker parks a gateway call on an approval whose verdict a stubbed backend `authorize` gave, is
 * stopped, and a second worker on the same session finds the approval still pending. A human decision
 * then runs the call exactly once, with the very arguments the backend was asked about: the hash it
 * received covers the arguments after schema defaults, which is what the gateway is invoked with
 * (otherwise the backend's argument binding would refuse every approved call).
 */
import { afterEach, describe, expect, it } from "vitest";
import { uuidv7 } from "../../src/core/ids.js";
import { FakeProvider, fakeText, fakeToolCall } from "../../src/models/fake.js";
import { ModelRegistry } from "../../src/models/stream.js";
import { argsHash } from "../../src/worker/args-hash.js";
import { startWorker, type WorkerHandle } from "../../src/worker/worker.js";
import { waitFor } from "../agent/support.js";
import { FakeGateway } from "../support/fake-gateway.js";
import { usePostgres } from "../support/pg.js";

const env = usePostgres();

// biome-ignore lint/suspicious/noExplicitAny: test access to request bodies
type Any = any;

const workers: WorkerHandle[] = [];
const gateways: FakeGateway[] = [];
afterEach(async () => {
  for (const w of workers.splice(0)) await w.stop();
  await Promise.all(gateways.splice(0).map((g) => g.close()));
});

const RUN_CODE = {
  name: "run_code",
  description: "Run code",
  parameters: {
    type: "object",
    properties: {
      code: { type: "string" },
      timeout: { type: "integer", default: 60 },
    },
    required: ["code"],
  },
  sideEffect: "write" as const,
  timeoutMs: 5_000,
  requiresApproval: true,
};

async function worker(sessionId: string, gw: FakeGateway, provider: FakeProvider) {
  const w = await startWorker({
    pool: env.newPool(),
    sessionId,
    serviceToken: "tok",
    leaseTtlMs: 3_000,
    leaseRetryMs: 25,
    migrate: false,
    models: new ModelRegistry().register(provider),
    modelDefaults: {
      provider: "fake",
      model: "m",
      summaryModel: "s",
      maxTokens: 10,
      apiKey: () => "k",
    },
    gateway: { baseUrl: gw.baseUrl, serviceToken: gw.serviceToken },
    backendPolicy: { baseUrl: gw.baseUrl, serviceToken: gw.serviceToken, retryMs: 0 },
  });
  workers.push(w);
  await w.serving;
  return w;
}

describe("approval across a worker restart", () => {
  it("stays pending after the worker is replaced, then runs once with the approved arguments", async () => {
    const gw = await FakeGateway.start([RUN_CODE]);
    gateways.push(gw);
    gw.defaultAuthorize = {
      decision: "requireApproval",
      reason: "run_code requires approval",
      approvalTtlMs: 600_000,
    };
    const sessionId = `sess-${uuidv7()}`;

    const first = new FakeProvider();
    first.enqueue(fakeToolCall("run_code", { code: "print(1)" }, { id: "c1" }));
    const a = await worker(sessionId, gw, first);
    const harnessA = a.harness as NonNullable<typeof a.harness>;
    const conv = await harnessA.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["run_code"] },
    });
    await conv.submit({ text: "run it" });
    await waitFor(async () =>
      (await harnessA.approvals(conv.id)).some((i) => i.status === "pending"),
    );
    const item = (await harnessA.approvals(conv.id)).find((i) => i.status === "pending");
    if (!item) throw new Error("no pending approval");
    expect(gw.effects("run_code")).toBe(0);

    await a.stop(); // the worker goes away with the approval pending

    const second = new FakeProvider();
    second.enqueue(fakeText("all done"));
    const b = await worker(sessionId, gw, second);
    const harnessB = b.harness as NonNullable<typeof b.harness>;
    const after = await harnessB.approvals(conv.id);
    expect(after.map((i) => [i.toolTaskId, i.status])).toEqual([[item.toolTaskId, "pending"]]);
    expect(gw.effects("run_code")).toBe(0);

    await harnessB.decideApproval({
      conversationId: conv.id,
      toolTaskId: item.toolTaskId,
      decision: "approve",
      principal: "admin-1",
    });
    await waitFor(() => gw.effects("run_code") === 1);
    await harnessB.waitForIdle();
    expect(gw.effects("run_code")).toBe(1);

    // The backend was asked about, and the gateway invoked with, the same arguments (defaults included).
    const asked = (gw.authorizeRequests()[0]?.body ?? {}) as Any;
    const invoked = (gw.invokes("run_code")[0]?.body ?? {}) as Any;
    expect(invoked.args).toEqual({ code: "print(1)", timeout: 60 });
    expect(asked.arguments).toEqual(invoked.args);
    expect(asked.argumentsHash).toBe(argsHash(invoked.args));
  });
});
