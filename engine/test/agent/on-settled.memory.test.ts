/**
 * `Harness.onSettled` (phase 6 U4): a listener hears every submission that
 * settles, once, including aborted runs and orphans settled when the harness
 * opens, and a throwing listener never disturbs settlement.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { SettledEvent } from "../../src/agent/harness.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTask, done } from "../../src/tasks/define.js";
import { defineTool } from "../../src/tools/registry.js";
import { memoryBackend, untilAborted, waitFor } from "./support.js";

const block = defineTool({
  name: "block",
  description: "Blocks",
  parameters: z.object({}),
  execute: async (_a, api) => untilAborted(api.signal),
});

describe("Harness.onSettled (memory)", () => {
  it("fires once per settled submission with its ids and status, and unsubscribes", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(fakeText("one"), fakeText("two"), fakeText("three"));
    const w = await fx.open();
    const events: SettledEvent[] = [];
    const off = w.harness.onSettled((e) => events.push(e));
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const a = await conv.submit({ text: "a", requestId: "req-a" });
    expect((await a.wait()).status).toBe("done");
    await w.harness.waitForIdle();
    expect(events).toEqual([
      {
        conversationId: conv.id,
        submissionId: a.id,
        requestId: "req-a",
        status: "done",
      },
    ]);
    const b = await conv.submit({ text: "b", requestId: "req-b" });
    await b.wait();
    await w.harness.waitForIdle();
    expect(events.map((e) => e.submissionId)).toEqual([a.id, b.id]);
    off();
    await (await conv.submit({ text: "c" })).wait();
    expect(events).toHaveLength(2);
    await w.crash();
  });

  it("fires unanswered(aborted) for an aborted run", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(fakeToolCall("block", {}, { id: "c1" }));
    const w = await fx.open({ tools: [block] });
    const events: SettledEvent[] = [];
    w.harness.onSettled((e) => events.push(e));
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["block"] },
    });
    const sub = await conv.submit({ text: "x", requestId: "req-x" });
    await waitFor(() =>
      w.harness.runtime.tasks().some((t) => t.kind === "agent.tool" && t.phase === "execute"),
    );
    await w.harness.runtime.abortConversation(conv.id, "user_abort");
    await sub.wait();
    await waitFor(() => events.length > 0);
    await w.harness.waitForIdle();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      submissionId: sub.id,
      requestId: "req-x",
      status: "unanswered",
      reason: "aborted",
    });
    await w.crash();
  });

  it("fires for a submission settled by startup orphan settlement", async () => {
    const fx = await memoryBackend.fixture();
    const noop = defineTask<null, null>({
      kind: "test.noop",
      phases: { run: (_task, rt) => rt.commit(() => done(null)) },
    });
    const w1 = await fx.open({ tasks: [noop] });
    const conv = await w1.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const ended = await w1.harness.runtime.spawn(noop, { conversationId: conv.id, input: null });
    await w1.harness.runtime.waitForTask(ended.id);
    // An active submission whose run is gone: what a crash between the run's end and its settlement leaves.
    await w1.harness.runtime.commit((tx) => {
      tx.createSubmission({
        id: "sub-orphan",
        conversationId: conv.id,
        requestId: "req-orphan",
        input: null,
        status: "active",
        result: null,
        taskId: ended.id,
      });
    });
    await w1.crash();
    const events: SettledEvent[] = [];
    const w2 = await fx.open({ onSettled: (e) => events.push(e) });
    expect(events).toEqual([
      {
        conversationId: conv.id,
        submissionId: "sub-orphan",
        requestId: "req-orphan",
        status: "unanswered",
        reason: "faulted",
      },
    ]);
    await w2.crash();
  });

  it("a throwing listener is reported and does not block the next submission or listener", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(fakeText("one"), fakeText("two"));
    const errors: unknown[] = [];
    const w = await fx.open({ onError: (err) => errors.push(err) });
    const heard: string[] = [];
    w.harness.onSettled(() => {
      throw new Error("listener boom");
    });
    w.harness.onSettled((e) => heard.push(e.submissionId));
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const a = await conv.submit({ text: "a" });
    expect((await a.wait()).status).toBe("done");
    const b = await conv.submit({ text: "b" });
    expect((await b.wait()).status).toBe("done");
    await w.harness.waitForIdle();
    expect(heard).toEqual([a.id, b.id]);
    expect(errors.filter((e) => (e as Error).message === "listener boom")).toHaveLength(2);
    await w.crash();
  });
});
