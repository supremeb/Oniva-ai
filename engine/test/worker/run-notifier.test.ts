/**
 * Run-finished push (phase 6 U5): the notifier enqueues a `run_finished` outbox
 * row for conversations tagged `metadata.notify = "backend"`, posts it with the
 * usage retry policy, acks on 2xx, and keeps the row when the backend is down.
 */
import { describe, expect, it } from "vitest";
import { agentConfigDoc, applyAgentConfig } from "../../src/agent/prompt.js";
import { fakeText } from "../../src/models/fake.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { defineTask, waiting } from "../../src/tasks/define.js";
import { RunNotifier } from "../../src/worker/run-notifier.js";
import { memoryBackend } from "../agent/support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

interface Call {
  url: string;
  auth: string | null;
  body: Any;
}

/** A fetch that answers from `statuses` (last one repeats) and records calls. */
function fakeBackend(statuses: number[] = [200]) {
  const calls: Call[] = [];
  const fetchFn = (async (url: string, init: RequestInit) => {
    calls.push({
      url,
      auth: new Headers(init.headers).get("authorization"),
      body: JSON.parse(init.body as string),
    });
    const status = statuses[Math.min(calls.length - 1, statuses.length - 1)] as number;
    return new Response(null, { status });
  }) as unknown as typeof fetch;
  return { calls, fetchFn };
}

function notifier(
  outbox: MemoryStorageProvider,
  fetchFn: typeof fetch,
  extra: Partial<ConstructorParameters<typeof RunNotifier>[0]> = {},
) {
  return new RunNotifier({
    baseUrl: "http://backend.invalid/",
    serviceToken: "svc-token",
    outbox,
    fetch: fetchFn,
    baseDelayMs: 1,
    sleep: async () => {},
    claimTimeoutMs: 0,
    ...extra,
  });
}

async function runOnce(
  n: RunNotifier,
  fx: Awaited<ReturnType<typeof memoryBackend.fixture>>,
  metadata: Record<string, string>,
) {
  fx.provider.enqueue(fakeText("hello"));
  const w = await fx.open({ onSettled: n.listener });
  n.attach(w.harness);
  const conv = await w.harness.createConversation({
    tenantId: "t1",
    projectId: "p1",
    agent: { systemPrompt: "s", tools: [], metadata },
  });
  const sub = await conv.submit({ text: "hi" });
  const settled = await sub.wait();
  await n.flush();
  return { w, conv, sub, settled };
}

describe("RunNotifier", () => {
  it("posts a run_finished row for a notify=backend conversation and acks it", async () => {
    const outbox = new MemoryStorageProvider();
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    const fx = await memoryBackend.fixture();
    const { w, conv, sub } = await runOnce(n, fx, { notify: "backend" });
    expect(be.calls).toHaveLength(1);
    const [call] = be.calls as [Call];
    expect(call.url).toBe("http://backend.invalid/internal/runs/finished");
    expect(call.auth).toBe("Bearer svc-token");
    expect(call.body).toMatchObject({
      kind: "run_finished",
      conversationId: conv.id,
      submissionId: sub.id,
      status: "done",
    });
    expect(call.body.usage.requests).toBeGreaterThanOrEqual(0);
    expect(await outbox.claimPending(0)).toEqual([]);
    await w.crash();
  });

  it("produces no outbox row and no post without the notify tag", async () => {
    const outbox = new MemoryStorageProvider();
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    const fx = await memoryBackend.fixture();
    const { w } = await runOnce(n, fx, {});
    expect(be.calls).toEqual([]);
    expect(await outbox.claimPending(0)).toEqual([]);
    await w.crash();
  });

  it("keeps the row while the backend is down and delivers it once after recovery", async () => {
    const outbox = new MemoryStorageProvider();
    const down = fakeBackend([503]);
    const n = notifier(outbox, down.fetchFn, { retryMs: 0 });
    const fx = await memoryBackend.fixture();
    const { w, conv, sub } = await runOnce(n, fx, { notify: "backend" });
    expect(down.calls.length).toBeGreaterThanOrEqual(1);
    const rows = await outbox.claimPending(0, "run_finished");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "run_finished" });
    // Backend recovers: a restarted worker's notifier flushes the parked row.
    const up = fakeBackend();
    const after = notifier(outbox, up.fetchFn);
    await after.flushOutbox();
    expect(up.calls).toHaveLength(1);
    expect(up.calls[0]?.body).toMatchObject({ conversationId: conv.id, submissionId: sub.id });
    await after.flushOutbox();
    expect(up.calls).toHaveLength(1);
    expect(await outbox.claimPending(0)).toEqual([]);
    await w.crash();
  });

  it("keeps the row on 401 (a fixable config error) but drops it on a permanent 4xx", async () => {
    for (const [status, kept] of [
      [401, 1],
      [403, 1],
      [422, 0],
    ] as const) {
      const outbox = new MemoryStorageProvider();
      const be = fakeBackend([status]);
      const n = notifier(outbox, be.fetchFn, { retryMs: 0 });
      const fx = await memoryBackend.fixture();
      const { w } = await runOnce(n, fx, { notify: "backend" });
      expect((await outbox.claimPending(0, "run_finished")).length).toBe(kept);
      await w.crash();
    }
  });

  it("does not deliver usage rows and does not take run_finished rows as usage", async () => {
    const outbox = new MemoryStorageProvider();
    await outbox.enqueue({ requestId: "u1", body: { a: 1 }, createdAt: 1, claimedAt: null });
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    await n.flushOutbox();
    expect(be.calls).toEqual([]);
    const usage = await outbox.claimPending(0, "usage");
    expect(usage.map((r) => r.requestId)).toEqual(["u1"]);
  });

  it("carries the unanswered reason", async () => {
    const outbox = new MemoryStorageProvider();
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    const fx = await memoryBackend.fixture();
    const w = await fx.open();
    n.attach(w.harness);
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [], metadata: { notify: "backend" } },
    });
    n.listener({
      conversationId: conv.id,
      submissionId: "sub-1",
      requestId: "req-1",
      status: "unanswered",
      reason: "aborted",
    });
    await n.flush();
    expect(be.calls[0]?.body).toMatchObject({ status: "unanswered", reason: "aborted" });
    await w.crash();
  });

  it("delivers a row enqueued before attach once the harness attaches", async () => {
    const outbox = new MemoryStorageProvider();
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    const fx = await memoryBackend.fixture();
    const w = await fx.open();
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: [], metadata: { notify: "backend" } },
    });
    n.listener({
      conversationId: conv.id,
      submissionId: "sub-1",
      requestId: "req-1",
      status: "done",
    });
    expect(be.calls).toEqual([]);
    n.attach(w.harness);
    await n.flush();
    expect(be.calls).toHaveLength(1);
    await w.crash();
  });

  it("does not report a child conversation that inherits the notify tag (an owned LLM-node child)", async () => {
    const outbox = new MemoryStorageProvider();
    const be = fakeBackend();
    const n = notifier(outbox, be.fetchFn);
    const fx = await memoryBackend.fixture();
    const owner = defineTask<null, null>({
      kind: "test_owner",
      phases: {
        start: async (_t, rt) => rt.commit(() => waiting({ until: fx.clock.now() + 1e9 })),
      },
    });
    const w = await fx.open({ tasks: [owner] });
    n.attach(w.harness);
    const agent = { systemPrompt: "s", tools: [], metadata: { notify: "backend" } };
    const parent = await w.harness.createConversation({ tenantId: "t1", projectId: "p1", agent });
    const def = w.harness.runtime.registry.get("test_owner");
    if (!def) throw new Error("owner task is not registered");
    const task = await w.harness.runtime.spawn(def, {
      conversationId: parent.id,
      input: null as never,
    });
    const childId = await w.harness.runtime.commit(async (tx, ops) => {
      const child = ops.createConversation({
        tenantId: "t1",
        projectId: "p1",
        ownerTaskId: task.id,
      });
      (await tx.doc(agentConfigDoc, child.id)).set(applyAgentConfig(agentConfigDoc.initial, agent));
      return child.id;
    });
    for (const conversationId of [childId, parent.id]) {
      n.listener({ conversationId, submissionId: "sub-1", requestId: "req-1", status: "done" });
    }
    await n.flush();
    expect(be.calls.map((c) => c.body.conversationId)).toEqual([parent.id]);
    await w.crash();
  });
});
