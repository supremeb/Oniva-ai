/**
 * Per-run subagent overrides from `agent.metadata.subagents` (KTD4), several
 * subagent tools in one harness, and the run caps (`maxRounds`, `deadlineMs`).
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { agentConfigDoc } from "../../src/agent/prompt.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool } from "../../src/tools/registry.js";
import { memoryBackend } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const noop = (name: string) =>
  defineTool({
    name,
    description: name,
    parameters: z.object({}),
    replay: "safe",
    execute: () => ({ content: `${name} ok` }),
  });

async function childConfig(h: Any, parentId: string, toolName: string) {
  const task = h.runtime
    .tasks({ conversationId: parentId })
    .find((t: Any) => t.kind === "agent.tool" && t.input.call.name === toolName);
  const childId = task.checkpoint.subagent.conversationId as string;
  return h.session.document(agentConfigDoc, childId);
}

describe("subagent overrides", () => {
  it("metadata changes the child's model, tools and prompt; a tool without metadata keeps its static spec", async () => {
    const fx = await memoryBackend.fixture();
    const worker = createSubagentTool({
      name: "research_worker",
      systemPrompt: "static worker",
      tools: ["a"],
      model: "static-model",
    });
    const reflector = createSubagentTool({ name: "reflector", systemPrompt: "static reflector" });
    fx.provider.enqueue(
      fakeToolCall("research_worker", { prompt: "w" }, { id: "c1" }),
      fakeText("w done"),
      fakeToolCall("reflector", { prompt: "r" }, { id: "c2" }),
      fakeText("r done"),
      fakeText("all done"),
    );
    const w = await fx.open({ tools: [worker, reflector, noop("a"), noop("b")] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: {
        systemPrompt: "parent",
        tools: ["research_worker", "reflector"],
        metadata: {
          subagents: {
            research_worker: { model: "tier-fast", tools: ["b"], systemPrompt: "run worker" },
          },
        },
      },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("done");

    expect(await childConfig(w.harness, conv.id, "research_worker")).toMatchObject({
      model: "tier-fast",
      tools: ["b"],
      systemPrompt: "run worker",
      depth: 1,
    });
    // reflector has no entry: the parent's tools and the static prompt apply.
    expect(await childConfig(w.harness, conv.id, "reflector")).toMatchObject({
      systemPrompt: "static reflector",
      tools: ["research_worker", "reflector"],
      model: null,
      depth: 1,
    });
    await w.crash();
  });

  it("absent metadata equals the static configuration", async () => {
    const fx = await memoryBackend.fixture();
    const tool = createSubagentTool({
      name: "research",
      systemPrompt: "static",
      tools: ["a"],
      model: "m",
    });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "p" }, { id: "c1" }),
      fakeText("x"),
      fakeText("y"),
    );
    const w = await fx.open({ tools: [tool, noop("a")] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "parent", tools: ["research"], metadata: { team: "a" } },
    });
    await (await conv.submit({ text: "go" })).wait();
    expect(await childConfig(w.harness, conv.id, "research")).toMatchObject({
      systemPrompt: "static",
      tools: ["a"],
      model: "m",
      metadata: { team: "a" },
    });
    await w.crash();
  });

  it("metadata cannot raise maxDepth", async () => {
    const fx = await memoryBackend.fixture();
    const tool = createSubagentTool({ name: "research", tools: ["research"] });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "p" }, { id: "c1" }),
      fakeToolCall("research", { prompt: "deeper" }, { id: "c2" }),
      fakeText("child done"),
      fakeText("parent done"),
    );
    const w = await fx.open({ tools: [tool] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: {
        tools: ["research"],
        metadata: { subagents: { research: { maxDepth: 5, tools: ["research"] } } },
      },
    });
    await (await conv.submit({ text: "go" })).wait();
    const entries = await w.harness.conversation(conv.id).entries();
    // The child (depth 1) asked for a grandchild: refused at the tool's own depth limit of 1.
    const parentResult = entries.find((e) => e.kind === "tool_result")?.payload as Any;
    const childId = parentResult.details.conversationId as string;
    const childEntries = await w.harness.conversation(childId).entries();
    const refused = childEntries.find((e) => e.kind === "tool_result")?.payload as Any;
    expect(refused.status).toBe("limit_exceeded");
    await w.crash();
  });

  it("a malformed override fails the call without creating a child", async () => {
    const fx = await memoryBackend.fixture();
    const tool = createSubagentTool({ name: "research" });
    fx.provider.enqueue(fakeToolCall("research", { prompt: "p" }, { id: "c1" }), fakeText("ok"));
    const w = await fx.open({ tools: [tool] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { tools: ["research"], metadata: { subagents: { research: { tools: "nope" } } } },
    });
    await (await conv.submit({ text: "go" })).wait();
    const result = (await conv.entries()).find((e) => e.kind === "tool_result")?.payload as Any;
    expect(result.status).toBe("error");
    expect(result.content).toContain("agent.metadata.subagents.research.tools");
    expect((await w.harness.session.reader.listConversations({ limit: 10 })).items).toHaveLength(1);
    await w.crash();
  });
});

describe("run caps", () => {
  const errors = (entries: Any[]) =>
    entries.filter((e) => e.kind === "run_error").map((e) => e.payload.error);

  it("maxRounds ends the run with a max_rounds denial at the next request", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(
      fakeToolCall("a", {}, { id: "k1" }),
      fakeToolCall("a", {}, { id: "k2" }),
      fakeToolCall("a", {}, { id: "k3" }),
    );
    const w = await fx.open({ tools: [noop("a")] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { tools: ["a"], maxRounds: 2 },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("unanswered");
    expect(fx.provider.requests).toHaveLength(2);
    expect(errors(await conv.entries())).toEqual([
      expect.objectContaining({ kind: "denied", code: "max_rounds" }),
    ]);
    await w.crash();
  });

  it("deadlineMs ends the run once the clock passes it, and children inherit it", async () => {
    const fx = await memoryBackend.fixture();
    const research = createSubagentTool({ name: "research", tools: ["a"] });
    const slow = defineTool({
      name: "a",
      description: "advances the clock",
      parameters: z.object({}),
      replay: "safe",
      execute: () => {
        fx.clock.advance(10_000);
        return { content: "late" };
      },
    });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "p" }, { id: "c1" }),
      fakeToolCall("a", {}, { id: "k1" }),
    );
    const w = await fx.open({ tools: [research, slow] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { tools: ["research"], deadlineMs: fx.clock.now() + 5_000 },
    });
    const sub = await conv.submit({ text: "go" });
    const settled = await sub.wait();
    expect(settled.status).toBe("unanswered");
    // The child's tool advanced the clock past the deadline: its next request was denied,
    // so the parent got an error result and its own next request was denied too.
    const all = await w.harness.session.reader.listConversations({ limit: 10 });
    expect(all.items).toHaveLength(2);
    const child = all.items.find((c) => c.ownerTaskId !== null);
    expect(errors(await w.harness.conversation(child?.id as string).entries())).toEqual([
      expect.objectContaining({ kind: "denied", code: "run_deadline" }),
    ]);
    expect(errors(await conv.entries())).toEqual([
      expect.objectContaining({ kind: "denied", code: "run_deadline" }),
    ]);
    expect(agentConfigDoc.initial.maxRounds).toBeNull();
    expect(agentConfigDoc.initial.deadlineMs).toBeNull();
    await w.crash();
  });
});
