/**
 * Harness follow-ups, run against every storage backend:
 * - a run that ends aborted outside the generation (an abort through the
 *   runtime) still starts the conversation's next queued input and settles
 *   the steers placed into it;
 * - gateway failures (`details.error`) map to the `timeout` and `lease_lost`
 *   tool result statuses.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

const block = () =>
  defineTool({
    name: "block",
    description: "Blocks until aborted",
    parameters: z.object({}),
    execute: async (_a, api) => untilAborted(api.signal),
  });

function gate() {
  let open: () => void = () => {};
  const opened = new Promise<void>((r) => {
    open = r;
  });
  const tool = defineTool({
    name: "gate",
    description: "Waits for the gate",
    parameters: z.object({}),
    replay: "safe",
    execute: async (_a, api) => {
      await Promise.race([opened, untilAborted(api.signal)]);
      return { content: "opened" };
    },
  });
  return { tool, open: () => open() };
}

const executing = (tasks: Any[], name: string) =>
  tasks.some((t) => t.kind === "agent.tool" && t.input.call.name === name && t.phase === "execute");

export function defineFollowupSuite(backend: AgentBackend): void {
  describe(`harness follow-ups (${backend.name})`, () => {
    it("a queued follow-up starts after the run is aborted through the runtime", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("block", {}, { id: "c1" }), fakeText("second"));
      const w = await fx.open({ tools: [block()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["block"] },
      });
      const main = await conv.submit({ text: "one" });
      await waitFor(() => executing(w.harness.runtime.tasks(), "block"));
      const follow = await conv.submit({ text: "two" });
      expect((await follow.record())?.status).toBe("pending");

      await w.harness.runtime.abortConversation(conv.id, "operator");
      const m = await main.wait();
      expect(m.status).toBe("unanswered");
      expect((m.result as Any).reason).toBe("aborted");
      const f = await follow.wait();
      expect(f.status).toBe("done");
      expect((f.result as Any).text).toBe("second");
      await w.crash();
    });

    it("a steer placed into a run aborted through the runtime settles with it", async () => {
      const fx = await backend.fixture();
      const g = gate();
      fx.provider.enqueue(
        fakeToolCall("gate", {}, { id: "c1" }),
        fakeToolCall("block", {}, { id: "c2" }),
        fakeText("after"),
      );
      const w = await fx.open({ tools: [g.tool, block()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["gate", "block"] },
      });
      const main = await conv.submit({ text: "one" });
      await waitFor(() => executing(w.harness.runtime.tasks(), "gate"));
      const steer = await conv.submit({ text: "steer", whenBusy: "steer" });
      g.open();
      await waitFor(() => executing(w.harness.runtime.tasks(), "block"));
      expect((await steer.record())?.status).toBe("active");
      const follow = await conv.submit({ text: "three" });

      await w.harness.runtime.abortConversation(conv.id, "operator");
      expect((await main.wait()).status).toBe("unanswered");
      const s = await steer.wait();
      expect(s.status).toBe("unanswered");
      expect((s.result as Any).reason).toBe("aborted");
      expect(((await follow.wait()).result as Any).text).toBe("after");
      await w.crash();
    });

    it("gateway timeout and lease_lost failures get their own tool result statuses", async () => {
      const fx = await backend.fixture();
      const failing = (name: string, error: string) =>
        defineTool({
          name,
          description: name,
          parameters: z.object({}),
          replay: "safe",
          execute: async () => ({
            content: `${name} failed`,
            isError: true,
            details: { error },
          }),
        });
      const fine = defineTool({
        name: "fine",
        description: "fine",
        parameters: z.object({}),
        execute: async () => ({ content: "fine", details: { error: "timeout" } }),
      });
      fx.provider.enqueue(
        {
          steps: [
            { type: "tool_call", id: "a", name: "slow", arguments: {} },
            { type: "tool_call", id: "b", name: "fenced", arguments: {} },
            { type: "tool_call", id: "c", name: "broken", arguments: {} },
            { type: "tool_call", id: "d", name: "fine", arguments: {} },
          ],
        },
        fakeText("done"),
      );
      const w = await fx.open({
        tools: [
          failing("slow", "timeout"),
          failing("fenced", "lease_lost"),
          failing("broken", "gateway_error"),
          fine,
        ],
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["slow", "fenced", "broken", "fine"] },
      });
      await (await conv.submit({ text: "x" })).wait();
      const byId = Object.fromEntries(
        results(await conv.entries()).map((r) => [r.toolCallId, r.status]),
      );
      expect(byId).toEqual({ a: "timeout", b: "lease_lost", c: "error", d: "ok" });
      await w.crash();
    });
  });
}
