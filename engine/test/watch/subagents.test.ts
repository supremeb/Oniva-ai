/**
 * The `subagents` member of the view (KTD2): a parent's view carries each
 * child's progress and recent tool calls, as a snapshot and as patches, and a
 * view advanced by patches equals one loaded later.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { MessageBuilder } from "../../src/models/stream.js";
import { defineTool } from "../../src/tools/registry.js";
import { type WatchFrame, WatchHub } from "../../src/watch/stream.js";
import {
  loadConversationView,
  SUBAGENT_CALL_CHARS,
  SUBAGENT_RECENT_CALLS,
} from "../../src/watch/view.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import { ClientView, gate, gatedTool } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to view internals
type Any = any;

const AGENT = { systemPrompt: "parent", tools: ["research"] };

const quickTool = defineTool({
  name: "wait",
  description: "Quick",
  parameters: z.object({ note: z.string().optional() }),
  replay: "safe",
  execute: () => ({ content: "ok" }),
});

function drain(consumer: { poll(): WatchFrame | null }, client: ClientView): void {
  for (let f = consumer.poll(); f !== null; f = consumer.poll()) client.apply(f);
}

describe("watch view: subagents", () => {
  it("a conversation without subagents has an empty member", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(fakeText("hi"));
    const w = await fx.open();
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    await (await conv.submit({ text: "x" })).wait();
    expect((await loadConversationView(w.harness.session, conv.id)).subagents).toEqual({});
    await w.crash();
  });

  it("a child's tool call reaches the parent's view as a patch and in a snapshot", async () => {
    const fx = await memoryBackend.fixture();
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    const research = createSubagentTool({ name: "research", tools: ["work"] });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
      fakeToolCall("work", { topic: "x" }, { id: "k1" }),
      fakeText("found it"),
      fakeText("parent done"),
    );
    const w = await fx.open({
      tools: [research, gatedTool(gates)],
      settings: { progressIntervalMs: 0 },
    });
    const hub = new WatchHub(w.harness);
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: AGENT,
    });
    const consumer = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await consumer.next()) as WatchFrame);
    expect((client.view as Any).subagents).toEqual({});

    const sub = await conv.submit({ text: "go" });
    await waitFor(async () => {
      const v = await loadConversationView(w.harness.session, conv.id);
      return Object.values(v.subagents).some((s) => s.recent.length === 1);
    });
    drain(consumer, client);

    const live = await loadConversationView(w.harness.session, conv.id);
    const [taskId] = Object.keys(live.subagents);
    const child = live.subagents[taskId as string];
    expect(child).toMatchObject({
      status: "running",
      toolCalls: 1,
      recent: ['work({"topic":"x"})'],
    });
    expect(child?.endedAt).toBeUndefined();
    expect(child?.usage.requests).toBe(1);
    // The patch stream reproduced it, and a snapshot taken now says the same.
    expect((client.view as Any).subagents).toEqual(live.subagents);
    const late = await hub.watch(conv.id, { cursor: 10_000 });
    expect(((late.poll() as Any).snapshot as Any).subagents).toEqual(live.subagents);
    // The child's own tool task is not part of the parent's view (verification (e)).
    const childTools = w.harness.runtime
      .tasks({ conversationId: child?.conversationId as string })
      .map((t) => t.id);
    for (const id of childTools) expect(Object.keys(live.toolOutput)).not.toContain(id);

    gates[0].open();
    gates[1].open();
    expect((await sub.wait()).status).toBe("done");
    await w.harness.waitForIdle();
    drain(consumer, client);
    const final = await loadConversationView(w.harness.session, conv.id);
    expect(client.view).toEqual(final);
    const done = final.subagents[taskId as string];
    expect(done?.status).toBe("done");
    expect(typeof done?.endedAt).toBe("string");
    expect(done?.usage.requests).toBe(2);
    await hub.close();
    await w.crash();
  });

  it("recent keeps the last five calls, each clipped", async () => {
    const fx = await memoryBackend.fixture();
    const research = createSubagentTool({ name: "research", tools: ["wait"] });
    const calls = Array.from({ length: 7 }, (_, i) =>
      fakeToolCall("wait", { note: `${i}:${"x".repeat(400)}` }, { id: `k${i}` }),
    );
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
      ...calls,
      fakeText("done"),
      fakeText("parent done"),
    );
    const w = await fx.open({
      tools: [research, quickTool],
      settings: { progressIntervalMs: 0 },
    });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: AGENT,
    });
    await (await conv.submit({ text: "go" })).wait();
    await w.harness.waitForIdle();
    const view = await loadConversationView(w.harness.session, conv.id);
    const [child] = Object.values(view.subagents);
    expect(child?.toolCalls).toBe(7);
    expect(child?.recent).toHaveLength(SUBAGENT_RECENT_CALLS);
    expect(child?.recent[0]).toContain("2:");
    expect(child?.recent.at(-1)).toContain("6:");
    for (const line of child?.recent ?? []) {
      expect(line.length).toBeLessThanOrEqual(SUBAGENT_CALL_CHARS);
      expect(line.startsWith("wait(")).toBe(true);
    }
    await w.crash();
  });

  it("an aborted child reads as aborted and a view loaded after equals the patched one", async () => {
    const fx = await memoryBackend.fixture();
    const hold = gate();
    const research = createSubagentTool({ name: "research", tools: ["work"] });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
      fakeToolCall("work", {}, { id: "k1" }),
    );
    const w = await fx.open({
      tools: [research, gatedTool([hold, hold])],
      settings: { progressIntervalMs: 0 },
    });
    const hub = new WatchHub(w.harness);
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: AGENT,
    });
    const consumer = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await consumer.next()) as WatchFrame);
    const sub = await conv.submit({ text: "go" });
    await waitFor(async () => {
      const v = await loadConversationView(w.harness.session, conv.id);
      return Object.values(v.subagents).some((s) => s.toolCalls === 1);
    });
    await conv.abort("user_abort");
    expect((await sub.wait()).status).toBe("unanswered");
    drain(consumer, client);
    const final = await loadConversationView(w.harness.session, conv.id);
    const [child] = Object.values(final.subagents);
    expect(child?.status).toBe("aborted");
    expect(typeof child?.endedAt).toBe("string");
    expect(client.view).toEqual(final);
    await hub.close();
    await w.crash();
  });
});

describe("live view and tool arguments (verification c)", () => {
  it("a streaming tool call has empty arguments until its block ends", () => {
    const b = new MessageBuilder("p", "m");
    b.start();
    b.startBlock({ type: "tool_call", id: "t1", name: "dw_write_section", arguments: {} });
    b.delta(0, { type: "tool_args", partialJson: '{"heading":"Intro","markdown":"Hel' });
    const mid = b.snapshot().blocks[0] as Any;
    expect(mid.arguments).toEqual({});
    expect(JSON.stringify(mid)).not.toContain("Intro");
    b.delta(0, { type: "tool_args", partialJson: 'lo"}' });
    b.endBlock(0);
    expect((b.snapshot().blocks[0] as Any).arguments).toEqual({
      heading: "Intro",
      markdown: "Hello",
    });
  });
});
