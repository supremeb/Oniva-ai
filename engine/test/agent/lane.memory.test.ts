/**
 * Scheduling lanes of agent runs: a conversation configured `lane: "background"` (Fleet, workflow model nodes)
 * starts its runs in the background lane, and every task of the run (later rounds, tool calls) follows it.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { runLane } from "../../src/agent/lane.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { TaskRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { memoryBackend } from "./support.js";

const task = (id: string, input: unknown): TaskRecord => ({ id, input }) as unknown as TaskRecord;

describe("runLane", () => {
  it("is interactive unless the run's root task says background", () => {
    const root = task("run1", { runId: "run1", round: 0, lane: "background" });
    const next = task("t2", { runId: "run1", round: 1 });
    const tool = task("t3", { runId: "run1", call: {}, after: [] });
    const chat = task("run2", { runId: "run2", round: 0 });
    const get = (id: string) => [root, next, tool, chat].find((t) => t.id === id);
    expect(runLane(root, get)).toBe("background");
    expect(runLane(next, get)).toBe("background");
    expect(runLane(tool, get)).toBe("background");
    expect(runLane(chat, get)).toBe("interactive");
    expect(runLane(task("x", null), get)).toBe("interactive");
  });
});

describe("runs in a background conversation", () => {
  it("mark every task of the run background; an interactive conversation's stay interactive", async () => {
    const fx = await memoryBackend.fixture();
    const ping = defineTool({
      name: "ping",
      description: "Ping",
      parameters: z.object({}),
      execute: async () => ({ content: "pong" }),
    });
    fx.provider.enqueue(
      fakeToolCall("ping", {}, { id: "c1" }),
      fakeText("bg done"),
      fakeText("chat done"),
    );
    const w = await fx.open({ tools: [ping] });
    const bg = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["ping"], lane: "background" },
    });
    expect((await (await bg.submit({ text: "go" })).wait()).status).toBe("done");
    const chat = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["ping"] },
    });
    expect((await (await chat.submit({ text: "hi" })).wait()).status).toBe("done");

    const all = (await w.harness.session.reader.listTasks({ limit: 100 })).items;
    const lanes = (convId: string) =>
      all
        .filter((t) => t.conversationId === convId)
        .map((t) => runLane(t, (id) => all.find((x) => x.id === id)));
    expect(all.length).toBeGreaterThan(0);
    expect(lanes(bg.id).length).toBeGreaterThanOrEqual(3); // two generations and the tool task
    expect(new Set(lanes(bg.id))).toEqual(new Set(["background"]));
    expect(new Set(lanes(chat.id))).toEqual(new Set(["interactive"]));
    await w.crash();
  });
});
