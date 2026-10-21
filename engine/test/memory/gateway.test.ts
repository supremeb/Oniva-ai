/**
 * The memory curator gateway client (plan 2026-10-21-001, U30) against the fake
 * memory backend: headers, key format, validation, retries, fencing, timeouts.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ManualClock, realClock } from "../../src/core/clock.js";
import { LeaseLost } from "../../src/core/errors.js";
import {
  parseCurateApplyRequest,
  parseCurateApplyResponse,
  parseCuratePrepareRequest,
  parseCuratePrepareResponse,
} from "../../src/memory/contract.js";
import {
  createMemoryGateway,
  curateKey,
  type MemoryGateway,
  MemoryGatewayError,
} from "../../src/memory/gateway.js";
import { FakeMemoryBackend, llmPrepare, SKIP_PREPARE } from "../support/fake-memory-backend.js";

const PREPARE = {
  contractVersion: 1,
  conversationId: "conv-1",
  runId: "run-1",
  curateConversationId: "cc-1",
  curateTaskId: "ct-1",
} as const;
const APPLY = {
  contractVersion: 1,
  conversationId: "conv-1",
  runId: "run-1",
  ctx: "ctx-1",
  answer: "[]",
} as const;
const PKEY = curateKey("conv-1", "run-1", "prepare");
const AKEY = curateKey("conv-1", "run-1", "apply");

describe("memory curator gateway client", () => {
  let be: FakeMemoryBackend;
  let clock: ManualClock;
  let fence: number | null;
  const rejected: unknown[] = [];

  const gateway = (over: Partial<Parameters<typeof createMemoryGateway>[0]> = {}): MemoryGateway =>
    createMemoryGateway({
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
    be = await FakeMemoryBackend.start();
    clock = new ManualClock(1_000);
    fence = null;
    rejected.length = 0;
  });
  afterEach(() => be.close());

  it("builds keys as curate:{conversationId}:{runId}:{phase}", () => {
    expect(PKEY).toBe("curate:conv-1:run-1:prepare");
    expect(AKEY).toBe("curate:conv-1:run-1:apply");
  });

  it("sends the service token, the key, the fence and the session", async () => {
    fence = 7;
    const res = await gateway().prepare(PKEY, PREPARE);
    expect(res).toMatchObject({ kind: "llm", tier: "low", ctx: "ctx-1" });
    const [call] = be.calls("prepare");
    expect(call?.path).toBe("/internal/memory/curate/prepare");
    expect(call?.headers).toMatchObject({
      authorization: `Bearer ${be.serviceToken}`,
      "idempotency-key": PKEY,
      "x-engine-fence": "7",
      "x-engine-session": "sess-1",
    });
    expect(call?.body).toEqual(PREPARE);
    expect(be.highWater("sess-1")).toBe(7);
  });

  it("answers skip and apply bodies the contract accepts", async () => {
    be.script("prepare", { body: SKIP_PREPARE });
    expect(await gateway().prepare(PKEY, PREPARE)).toEqual({
      contractVersion: 1,
      kind: "skip",
      reason: "mode_off",
    });
    expect(await gateway().apply(AKEY, APPLY)).toMatchObject({
      ok: true,
      status: "applied",
      counts: { applied: 2, pending: 0, dropped: 0 },
    });
    expect(be.calls("apply")[0]?.body).toEqual(APPLY);
  });

  it("a key that does not match the route or body is rejected by the backend and runs nothing", async () => {
    await expect(gateway().prepare("curate:conv-1:run-1:apply", PREPARE)).rejects.toMatchObject({
      code: "gateway_error",
      status: 400,
    });
    await expect(gateway().apply(PKEY, APPLY)).rejects.toMatchObject({ status: 400 });
    await expect(gateway().prepare(PKEY, { ...PREPARE, runId: "run-2" })).rejects.toMatchObject({
      status: 400,
    });
    expect(be.executions(PKEY)).toBe(0);
  });

  it("the same key replays the stored outcome without running again", async () => {
    be.script("apply", {
      body: { contractVersion: 1, ok: true, status: "skipped", reason: "budget" },
    });
    const gw = gateway();
    const first = await gw.apply(AKEY, APPLY);
    const second = await gw.apply(AKEY, APPLY);
    expect(second).toEqual(first);
    expect(second).toMatchObject({ status: "skipped", reason: "budget" });
    expect(be.executions(AKEY)).toBe(1);
  });

  it("validates every response with the contract", async () => {
    be.script("prepare", { body: { contractVersion: 1, kind: "llm", system: "s" } });
    await expect(gateway().prepare(PKEY, PREPARE)).rejects.toMatchObject({
      code: "invalid_response",
    });
    be.script("apply", { body: { contractVersion: 1, ok: true, status: "weird" } });
    await expect(gateway().apply(AKEY, APPLY)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it("refuses to send a request the contract rejects", async () => {
    await expect(gateway().apply(AKEY, { ...APPLY, answer: "" })).rejects.toBeInstanceOf(
      MemoryGatewayError,
    );
    await expect(gateway().prepare(PKEY, { ...PREPARE, curateTaskId: "" })).rejects.toMatchObject({
      code: "gateway_error",
    });
    expect(be.requests).toHaveLength(0);
  });

  it("the backend answers 422 to a body the schema rejects", async () => {
    const res = await fetch(`${be.baseUrl}/internal/memory/curate/apply`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${be.serviceToken}`,
        "idempotency-key": AKEY,
        "content-type": "application/json",
      },
      body: JSON.stringify({ ...APPLY, answer: 5 }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ error: "invalid_request" });
  });

  it("a missing or wrong token is 401 and a definitive failure", async () => {
    const bad = gateway({ serviceToken: "nope" });
    await expect(bad.prepare(PKEY, PREPARE)).rejects.toMatchObject({
      code: "gateway_error",
      status: 401,
    });
    expect(be.calls("prepare")).toHaveLength(1);
  });

  it("retries 5xx and 429 with the same key and gives up after the budget", async () => {
    be.script("prepare", { status: 503 }, { status: 429, headers: { "retry-after": "0" } });
    const result = await gateway({ clock: realClock }).prepare(PKEY, PREPARE);
    expect(result.kind).toBe("llm");
    expect(be.keys("prepare")).toEqual([PKEY, PKEY, PKEY]);

    be.script("apply", { status: 500 }, { status: 500 }, { status: 500 });
    await expect(gateway({ clock: realClock }).apply(AKEY, APPLY)).rejects.toMatchObject({
      code: "gateway_unavailable",
      status: 500,
    });
  });

  it("keeps retrying one key through 503 in_flight past the outage budget", async () => {
    be.script(
      "prepare",
      ...Array.from({ length: 6 }, () => ({
        status: 503,
        body: { error: "in_flight" },
        headers: { "retry-after": "0" },
      })),
    );
    const result = await gateway({
      clock: realClock,
      retry: { attempts: Number.POSITIVE_INFINITY, baseDelayMs: 10, maxDelayMs: 10, budgetMs: 15 },
    }).prepare(PKEY, PREPARE);
    expect(result.kind).toBe("llm");
    expect(be.keys("prepare")).toHaveLength(7);
  });

  it("does not retry a definitive 4xx", async () => {
    be.script("prepare", { status: 422, body: { error: "invalid_request" } });
    await expect(gateway().prepare(PKEY, PREPARE)).rejects.toMatchObject({
      code: "gateway_error",
      status: 422,
    });
    expect(be.calls("prepare")).toHaveLength(1);
  });

  it("a rejected fence token calls onFenceRejected and throws LeaseLost", async () => {
    be.setHighWater("sess-1", 10);
    fence = 3;
    await expect(gateway().prepare(PKEY, PREPARE)).rejects.toBeInstanceOf(LeaseLost);
    expect(rejected).toEqual([{ idempotencyKey: PKEY, token: 3 }]);
    expect(be.executions(PKEY)).toBe(0);
  });

  it("a call past its deadline fails with timeout", async () => {
    be.script("prepare", { hang: true });
    const pending = gateway()
      .prepare(PKEY, PREPARE, { timeoutMs: 1000 })
      .catch((e: unknown) => e);
    while (be.calls("prepare").length === 0) await new Promise((r) => setTimeout(r, 2));
    clock.advance(1000);
    expect(await pending).toMatchObject({ code: "timeout" });
  });

  it("a cancelled call rejects with the signal's reason", async () => {
    be.script("apply", { hang: true });
    const controller = new AbortController();
    const pending = gateway()
      .apply(AKEY, APPLY, { signal: controller.signal })
      .catch((e: unknown) => e);
    while (be.calls("apply").length === 0) await new Promise((r) => setTimeout(r, 2));
    const reason = new Error("stop");
    controller.abort(reason);
    expect(await pending).toBe(reason);
  });
});

describe("memory curator contract", () => {
  it("accepts the documented shapes and ignores unknown response fields", () => {
    expect(parseCuratePrepareRequest(PREPARE)).toEqual(PREPARE);
    expect(parseCurateApplyRequest(APPLY)).toEqual(APPLY);
    expect(parseCuratePrepareResponse({ ...(llmPrepare() as object), extra: 1 })).toMatchObject({
      kind: "llm",
    });
    expect(parseCurateApplyResponse({ contractVersion: 1, ok: true, status: "applied" })).toEqual({
      contractVersion: 1,
      ok: true,
      status: "applied",
    });
  });

  it("rejects a wrong version, tier, empty prompt and oversized fields", () => {
    expect(() =>
      parseCuratePrepareResponse({ ...(llmPrepare() as object), contractVersion: 2 }),
    ).toThrow(/contractVersion/);
    expect(() => parseCuratePrepareResponse(llmPrepare({ tier: "ultra" }))).toThrow(/tier/);
    expect(() => parseCuratePrepareResponse(llmPrepare({ prompt: "" }))).toThrow(/prompt/);
    expect(() => parseCuratePrepareResponse(llmPrepare({ ctx: "x".repeat(5000) }))).toThrow(/ctx/);
    expect(() => parseCuratePrepareResponse({ contractVersion: 1, kind: "other" })).toThrow();
    expect(() =>
      parseCurateApplyResponse({
        contractVersion: 1,
        ok: true,
        status: "applied",
        counts: { a: -1 },
      }),
    ).toThrow(/counts/);
    expect(() => parseCurateApplyRequest({ ...APPLY, answer: "x".repeat(131_073) })).toThrow(
      /answer/,
    );
  });
});
