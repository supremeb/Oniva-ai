/**
 * The Tier 0 offload client (U12) against the fake gateway: the request it
 * sends to `POST /internal/workspace/offload`, its retries and its fencing.
 */
import { afterEach, describe, expect, it } from "vitest";
import type { OffloadPutRequest } from "../../src/agent/offload.js";
import { LeaseLost } from "../../src/core/errors.js";
import { createOffloadStore, type FenceRejection } from "../../src/tools/gateway.js";
import { FakeGateway } from "../support/fake-gateway.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to bodies
type Any = any;

const request = (over: Partial<OffloadPutRequest> = {}): OffloadPutRequest => ({
  conversation: { id: "conv-1", tenantId: "t1", projectId: "p1" },
  runId: "run-1",
  toolCallId: "call-1",
  toolName: "browse_url",
  text: "hello world",
  ...over,
});

const RETRY = { attempts: 3, baseDelayMs: 1, maxDelayMs: 2 };

describe("offload gateway client", () => {
  let gw: FakeGateway | undefined;
  afterEach(async () => {
    await gw?.close();
    gw = undefined;
  });

  async function start(options: Parameters<typeof FakeGateway.start>[1] = {}) {
    gw = await FakeGateway.start([], options);
    return gw;
  }

  const store = (g: FakeGateway, extra: Partial<Parameters<typeof createOffloadStore>[0]> = {}) =>
    createOffloadStore({
      baseUrl: g.baseUrl,
      serviceToken: g.serviceToken,
      fence: () => 7,
      sessionId: "sess-1",
      retry: RETRY,
      ...extra,
    });

  it("posts the ids and the text with the idempotency key and fence, and returns the path", async () => {
    const g = await start();
    const out = await store(g).put(request());
    expect(out).toEqual({ path: "tool_results/call-1.txt", bytes: 11 });
    const [req] = g.offloadRequests();
    expect(req?.headers["idempotency-key"]).toBe("run-1:call-1");
    expect(req?.headers["x-engine-fence"]).toBe("7");
    expect(req?.headers["x-engine-session"]).toBe("sess-1");
    expect(req?.headers.authorization).toBe(`Bearer ${g.serviceToken}`);
    expect(req?.body).toEqual({
      tenantId: "t1",
      projectId: "p1",
      conversationId: "conv-1",
      engineConversationId: "conv-1",
      runId: "run-1",
      toolCallId: "call-1",
      toolName: "browse_url",
      text: "hello world",
    });
  });

  it("sends the root conversation id for a subagent's child conversation", async () => {
    const g = await start();
    await store(g).put(
      request({ conversation: { id: "child", rootId: "root", tenantId: "t1", projectId: "p1" } }),
    );
    const body = g.offloadRequests()[0]?.body as Any;
    expect(body.conversationId).toBe("root");
    expect(body.engineConversationId).toBe("child");
  });

  it("is idempotent: the same key answers the same path and stores one object", async () => {
    const g = await start();
    const s = store(g);
    const a = await s.put(request());
    const b = await s.put(request({ text: "different" }));
    expect(b).toEqual(a);
    expect(g.offloaded.size).toBe(1);
  });

  it("retries a 503 (honouring Retry-After) and a 500, then succeeds", async () => {
    const g = await start();
    g.scriptOffload(
      { status: 503, headers: { "retry-after": "0" } },
      { status: 500, body: { error: "boom" } },
    );
    const out = await store(g).put(request());
    expect(out.path).toBe("tool_results/call-1.txt");
    expect(g.offloadRequests()).toHaveLength(3);
    const keys = new Set(g.offloadRequests().map((r) => r.headers["idempotency-key"]));
    expect(keys.size).toBe(1);
  });

  it("gives up after the retry budget with an error", async () => {
    const g = await start();
    g.scriptOffload({ status: 503 }, { status: 503 }, { status: 503 });
    await expect(store(g).put(request())).rejects.toThrow(/503/);
    expect(g.offloadRequests()).toHaveLength(3);
    expect(g.offloaded.size).toBe(0);
  });

  it("does not retry a 413 or a 400", async () => {
    const g = await start({ offloadMaxBytes: 5 });
    await expect(store(g).put(request())).rejects.toThrow(/413/);
    expect(g.offloadRequests()).toHaveLength(1);

    const g2 = await FakeGateway.start([]);
    try {
      g2.scriptOffload({ status: 400, body: { error: "invalid_request" } });
      await expect(store(g2).put(request())).rejects.toThrow(/400/);
      expect(g2.offloadRequests()).toHaveLength(1);
    } finally {
      await g2.close();
    }
  });

  it("a rejected fence throws LeaseLost and tells the host", async () => {
    const g = await start();
    g.setHighWater("sess-1", 100);
    const rejections: FenceRejection[] = [];
    const s = store(g, { onFenceRejected: (r) => rejections.push(r) });
    await expect(s.put(request())).rejects.toBeInstanceOf(LeaseLost);
    expect(rejections).toHaveLength(1);
    expect(rejections[0]?.token).toBe(7);
    expect(g.offloaded.size).toBe(0);
    expect(g.offloadRequests()).toHaveLength(1);
  });

  it("sends nothing when the lease is already lost", async () => {
    const g = await start();
    const s = store(g, {
      fence: () => {
        throw new LeaseLost("sess-1", 7);
      },
    });
    await expect(s.put(request())).rejects.toBeInstanceOf(LeaseLost);
    expect(g.offloadRequests()).toHaveLength(0);
  });

  it("rejects a malformed answer", async () => {
    const g = await start();
    g.scriptOffload({ status: 200, body: { nope: true } });
    await expect(store(g).put(request())).rejects.toThrow(/invalid/);
  });

  it("stops when the caller's signal aborts", async () => {
    const g = await start();
    g.scriptOffload({ hang: true });
    const ac = new AbortController();
    const pending = store(g).put(request({ signal: ac.signal }));
    setTimeout(() => ac.abort(new Error("cancelled")), 20);
    await expect(pending).rejects.toThrow("cancelled");
  });

  it("gives up with a timeout error when the gateway never answers", async () => {
    const g = await start();
    g.scriptOffload({ hang: true });
    await expect(store(g, { timeoutMs: 50 }).put(request())).rejects.toThrow(/timed out/);
  });
});
