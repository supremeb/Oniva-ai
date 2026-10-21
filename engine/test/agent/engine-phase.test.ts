/**
 * The engine-phase tool seam (U18, KTD12): a tool may declare an
 * `enginePhase` handler that runs inside its tool task, with the task
 * runtime. Subagent spawn is its first user; `label` rides in the checkpoint.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { createSubagentTool } from "../../src/agent/subagents.js";
import type { JsonObject } from "../../src/core/json.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { waiting } from "../../src/tasks/define.js";
import { defineTool } from "../../src/tools/registry.js";
import { memoryBackend, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);
const AGENT = { systemPrompt: "p", tools: ["research"], metadata: {} };

const slow = () =>
  defineTool({
    name: "slow",
    description: "slow",
    parameters: z.object({}),
    replay: "safe",
    execute: async () => ({ content: "slow done" }),
  });

describe("engine-phase seam", () => {
  it("a tool with enginePhase can wait on tasks and then commit a result", async () => {
    const fx = await memoryBackend.fixture();
    const seen: string[] = [];
    const gate = defineTool({
      name: "gate",
      description: "gate",
      parameters: z.object({}),
      enginePhase: {
        phase: "engine",
        run: async (_deps, task, rt, writeResult) => {
          seen.push(`${task.phase}:${task.wake === null ? "first" : "woken"}`);
          if (task.wake === null) {
            const sibling = rt.runtime
              .tasks({ conversationId: task.conversationId })
              .find((t) => t.kind === "agent.tool" && t.id !== task.id && t.status !== "terminal");
            if (sibling) {
              return rt.commit(() => waiting({ on: [sibling.id], phase: "engine" }));
            }
          }
          return rt.commit((tx) => writeResult(tx, task, "ok", { content: "gated" }, 0));
        },
      },
      execute: () => ({ content: "never", isError: true }),
    });
    fx.provider.enqueue(
      fakeToolCall("slow", {}, { id: "s1" }),
      fakeToolCall("gate", {}, { id: "g1" }),
      fakeText("end"),
    );
    const w = await fx.open({ tools: [slow(), gate] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { ...AGENT, tools: ["slow", "gate"] },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("done");
    const out = results(await conv.entries());
    expect(out.map((r) => r.content)).toContain("gated");
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((s) => s.startsWith("engine:"))).toBe(true);
    await w.crash();
  });

  describe("subagent label", () => {
    const run = async (args: JsonObject) => {
      const fx = await memoryBackend.fixture();
      const research = createSubagentTool({ name: "research", tools: [] });
      fx.provider.enqueue(
        fakeToolCall("research", args, { id: "c1" }),
        fakeText("child answer"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "go" })).wait();
      const task = w.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t) => t.kind === "agent.tool");
      const entries = results(await conv.entries());
      await w.crash();
      return { task, entries };
    };

    it("records a valid label in the checkpoint and keeps the result unchanged", async () => {
      const { task, entries } = await run({
        prompt: "dig",
        label: "worker_1-A",
        context: { brief: "b" },
      });
      expect((task as Any).checkpoint.label).toBe("worker_1-A");
      expect((task as Any).checkpoint.subagent.conversationId).toBeTypeOf("string");
      expect(entries[0]).toMatchObject({ status: "ok", content: "child answer" });
    });

    it("trims the label", async () => {
      const { task } = await run({ prompt: "dig", label: "  w1  " });
      expect((task as Any).checkpoint.label).toBe("w1");
    });

    it("without a label the checkpoint has no label key", async () => {
      const { task } = await run({ prompt: "dig" });
      expect(Object.hasOwn((task as Any).checkpoint, "label")).toBe(false);
    });

    it.each([["has space"], ["bad/char"], ["x".repeat(65)], [""]])(
      "invalid label %j is invalid_arguments",
      async (label) => {
        const { entries } = await run({ prompt: "dig", label });
        expect(entries[0]).toMatchObject({ status: "invalid_arguments", isError: true });
      },
    );
  });

  it("a subagent task keeps the `subagent` phase while its child runs", async () => {
    const fx = await memoryBackend.fixture();
    const research = createSubagentTool({ name: "research", tools: ["wait"] });
    const wait = defineTool({
      name: "wait",
      description: "w",
      parameters: z.object({}),
      replay: "safe",
      execute: async (_a, api) => untilAborted(api.signal),
    });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "d" }, { id: "c1" }),
      fakeToolCall("wait", {}, { id: "k" }),
    );
    const w = await fx.open({ tools: [research, wait] });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: AGENT,
    });
    await conv.submit({ text: "go" });
    await waitFor(() =>
      w.harness.runtime
        .tasks({ conversationId: conv.id })
        .some((t) => t.kind === "agent.tool" && t.phase === "subagent" && t.status === "waiting"),
    );
    await w.crash();
  });
});
