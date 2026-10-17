/**
 * The node gateway client (plan 2026-10-17-003, U11) against the fake workflow
 * backend: headers, key format, validation, retries, fencing and timeouts.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ManualClock, realClock } from "../../src/core/clock.js";
import { LeaseLost } from "../../src/core/errors.js";
import {
  createNodeGateway,
  finalizeKey,
  type NodeGateway,
  NodeGatewayError,
  nodeKey,
} from "../../src/workflow/node-gateway.js";
import { FakeWorkflowBackend, fixture } from "../support/fake-workflow-backend.js";

const REF = { runId: "run-1", nodeId: "n1" };
const BODY = { contractVersion: 1, runAttempt: 0, nodeAttempt: 0, nodeType: "code" } as const;
const KEY = nodeKey({
  runId: "run-1",
  nodeId: "n1",
  runAttempt: 0,
  nodeAttempt: 0,
  phase: "prepare",
});

describe("node gateway client", () => {
  let be: FakeWorkflowBackend;
  let clock: ManualClock;
  let fence: number | null;
  const rejected: unknown[] = [];

  const gateway = (over: Partial<Parameters<typeof createNodeGateway>[0]> = {}): NodeGateway =>
    createNodeGateway({
      baseUrl: be.baseUrl,
      serviceToken: be.serviceToken,
      fence: () => fence,
      sessionId: "sess-1",
      onFenceRejected: (r) => rejected.push(r),
      clock,
      retry: { attempts: 3, baseDelayMs: 0, maxDelayMs: 0 },
      ...over,
    });

  beforeEach(async () => {
    be = await FakeWorkflowBackend.start();
    clock = new ManualClock(1_000);
    fence = null;
    rejected.length = 0;
  });
  afterEach(() => be.close());

  it("builds keys in the KTD4 shape", () => {
    expect(KEY).toBe("run-1:n1:r0:a0:prepare");
    expect(
      nodeKey({ runId: "r", nodeId: "n", runAttempt: 2, nodeAttempt: 1, phase: "finish" }),
    ).toBe("r:n:r2:a1:finish");
    expect(finalizeKey("r", 2)).toBe("r:r2:finalize");
  });

  it("sends the service token, the key, the fence and the session", async () => {
    fence = 7;
    await gateway().prepare(REF, KEY, BODY);
    const [call] = be.calls("prepare");
    expect(call?.headers).toMatchObject({
      authorization: `Bearer ${be.serviceToken}`,
      "idempotency-key": KEY,
      "x-engine-fence": "7",
      "x-engine-session": "sess-1",
    });
    expect(call?.body).toEqual(BODY);
    expect(be.highWater("sess-1")).toBe(7);
  });

  it("a key that does not match the route is rejected by the backend and runs nothing", async () => {
    const err = await gateway()
      .prepare(REF, "run-1:n1:a0:prepare", BODY)
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(NodeGatewayError);
    expect(err).toMatchObject({ code: "gateway_error", status: 400 });
    expect(be.total("n1", "prepare")).toBe(0);
  });

  it("the same key replays the stored outcome without running again", async () => {
    be.script("n1", "prepare", { body: fixture("prepare.final.failed.valid") });
    const gw = gateway();
    const first = await gw.prepare(REF, KEY, BODY);
    const second = await gw.prepare(REF, KEY, BODY);
    expect(second).toEqual(first);
    expect(second).toMatchObject({ status: "failed" });
    expect(be.executions(KEY)).toBe(1);
    // A different runAttempt is a different call and gets the default result.
    const retry = await gw.prepare(
      REF,
      nodeKey({ runId: "run-1", nodeId: "n1", runAttempt: 1, nodeAttempt: 0, phase: "prepare" }),
      { ...BODY, runAttempt: 1 },
    );
    expect(retry).toMatchObject({ status: "success" });
  });

  it("validates every response with the contract", async () => {
    be.script("n1", "prepare", { body: { contractVersion: 1, kind: "wait" } });
    await expect(gateway().prepare(REF, KEY, BODY)).rejects.toMatchObject({
      code: "invalid_response",
    });
    be.script("n1", "finish", { body: fixture("prepare.wait.duration.valid") });
    await expect(
      gateway().finish(REF, "run-1:n1:r0:a0:finish", fixture("finish.llm.valid") as never),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("refuses to send a finish body the contract rejects", async () => {
    await expect(
      gateway().finish(REF, "run-1:n1:r0:a0:finish", { kind: "nope" } as never),
    ).rejects.toMatchObject({ code: "invalid_body" });
    expect(be.calls("finish")).toHaveLength(0);
  });

  it("retries 5xx and 429 with the same key and gives up after the budget", async () => {
    be.script("n1", "prepare", { status: 503 }, { status: 429, headers: { "retry-after": "0" } });
    const result = await gateway({ clock: realClock }).prepare(REF, KEY, BODY);
    expect(result.kind).toBe("final");
    expect(be.keys("prepare")).toEqual([KEY, KEY, KEY]);

    be.script("n2", "prepare", { status: 500 }, { status: 500 }, { status: 500 });
    await expect(
      gateway({ clock: realClock }).prepare(
        { runId: "run-1", nodeId: "n2" },
        "run-1:n2:r0:a0:prepare",
        BODY,
      ),
    ).rejects.toMatchObject({ code: "gateway_unavailable", status: 500 });
  });

  it("does not retry a definitive 4xx", async () => {
    be.script("n1", "prepare", { status: 422, body: { error: "invalid_request" } });
    await expect(gateway().prepare(REF, KEY, BODY)).rejects.toMatchObject({
      code: "gateway_error",
      status: 422,
    });
    expect(be.calls("prepare")).toHaveLength(1);
  });

  it("a rejected fence token calls onFenceRejected and throws LeaseLost", async () => {
    be.setHighWater("sess-1", 10);
    fence = 3;
    await expect(gateway().prepare(REF, KEY, BODY)).rejects.toBeInstanceOf(LeaseLost);
    expect(rejected).toEqual([{ idempotencyKey: KEY, token: 3 }]);
    expect(be.total("n1", "prepare")).toBe(0);
  });

  it("looks up a stored outcome and answers null for an unknown key", async () => {
    const gw = gateway();
    expect(await gw.lookup(KEY)).toBeNull();
    await gw.prepare(REF, KEY, BODY);
    expect(await gw.lookup(KEY)).toMatchObject({ kind: "final", status: "success" });
  });

  it("finalize is keyed {runId}:r{n}:finalize and idempotent", async () => {
    const gw = gateway();
    const input = fixture("finalize.valid") as never;
    await gw.finalize("run-1", finalizeKey("run-1", 0), input);
    await gw.finalize("run-1", finalizeKey("run-1", 0), input);
    expect(be.executions("run-1:r0:finalize")).toBe(1);
    await expect(gw.finalize("run-1", "run-1:finalize", input)).rejects.toMatchObject({
      status: 400,
    });
  });

  it("a call past its deadline fails with timeout", async () => {
    be.script("n1", "prepare", { hang: true });
    const pending = gateway()
      .prepare(REF, KEY, BODY, { timeoutMs: 1000 })
      .catch((e: unknown) => e);
    while (be.calls("prepare").length === 0) await new Promise((r) => setTimeout(r, 2));
    clock.advance(1000);
    expect(await pending).toMatchObject({ code: "timeout" });
  });

  it("a cancelled call rejects with the signal's reason", async () => {
    be.script("n1", "prepare", { hang: true });
    const controller = new AbortController();
    const pending = gateway()
      .prepare(REF, KEY, BODY, { signal: controller.signal })
      .catch((e: unknown) => e);
    while (be.calls("prepare").length === 0) await new Promise((r) => setTimeout(r, 2));
    const reason = new Error("stop");
    controller.abort(reason);
    expect(await pending).toBe(reason);
  });
});
