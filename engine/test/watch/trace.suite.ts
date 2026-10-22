/**
 * The conversation trace (U2): a root conversation and every subagent child
 * below it, in one read-only structure, run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeError, fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool } from "../../src/tools/registry.js";
import { buildTrace } from "../../src/watch/trace.js";
import { loadConversationView } from "../../src/watch/view.js";
import type { AgentBackend } from "../agent/support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const quickWait = () =>
  defineTool({
    name: "wait",
    description: "Waits",
    parameters: z.object({}),
    replay: "safe",
    execute: async () => ({ content: "waited" }),
  });

export function defineTraceSuite(backend: AgentBackend): void {
  describe(`conversation trace (${backend.name})`, () => {
    it("returns the root and each subagent child with role, parent task and usage", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "one" }, { id: "c1" }),
        fakeText("child one"),
        fakeToolCall("research", { prompt: "two" }, { id: "c2" }),
        fakeText("child two"),
        fakeText("all done"),
      );
      const w = await fx.open({ tools: [research, quickWait()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["research"] },
      });
      await (await conv.submit({ text: "go" })).wait();

      const trace = await buildTrace(w.harness.session, conv.id);
      expect(trace.conversationId).toBe(conv.id);
      expect(trace.truncated).toBe(false);
      expect(trace.nextCursor).toBeNull();
      const childIds = Object.keys(trace.children);
      expect(childIds).toHaveLength(2);
      for (const id of childIds) {
        const child = trace.children[id] as Any;
        expect(child.role).toBe("research");
        expect(typeof child.parentToolTaskId).toBe("string");
        expect(child.entries.length).toBeGreaterThan(0);
        expect(child.entries.every((e: Any) => e.conversationId === id)).toBe(true);
        expect(child.usage.total.requests).toBe(1);
      }
      expect(trace.usage.requests).toBe(3);
      expect(trace.agent).toMatchObject({ depth: 0, tools: ["research"] });
      expect(trace.taint.tainted).toBe(false);
      await w.crash();
    });

    it("follows nesting to depth 2 and agrees with the view on root entry ids", async () => {
      const fx = await backend.fixture();
      const outer = createSubagentTool({ name: "outer", tools: ["inner"], maxDepth: 3 });
      const inner = createSubagentTool({ name: "inner", tools: ["wait"], maxDepth: 3 });
      fx.provider.enqueue(
        fakeToolCall("outer", { prompt: "o" }, { id: "c1" }),
        fakeToolCall("inner", { prompt: "i" }, { id: "k1" }),
        fakeText("grandchild"),
        fakeText("child"),
        fakeText("root"),
      );
      const w = await fx.open({ tools: [outer, inner, quickWait()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["outer"] },
      });
      await (await conv.submit({ text: "go" })).wait();

      const trace = await buildTrace(w.harness.session, conv.id);
      const children = Object.values(trace.children) as Any[];
      expect(children.map((c) => c.role).sort()).toEqual(["inner", "outer"]);
      expect(children.map((c) => c.agent.depth).sort()).toEqual([1, 2]);
      const view = await loadConversationView(w.harness.session, conv.id);
      expect(trace.entries.map((e) => e.id)).toEqual(view.entries.map((e) => e.id));
      await w.crash();
    });

    it("pages by entry budget: truncated with a cursor, and a second call returns the rest", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "one" }, { id: "c1" }),
        fakeText("child one"),
        fakeText("all done"),
      );
      const w = await fx.open({ tools: [research, quickWait()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["research"] },
      });
      await (await conv.submit({ text: "go" })).wait();

      const full = await buildTrace(w.harness.session, conv.id);
      const total =
        full.entries.length +
        Object.values(full.children).reduce((n, c) => n + (c as Any).entries.length, 0);
      const seen: string[] = [];
      let cursor: string | null = null;
      let pages = 0;
      do {
        const page: Awaited<ReturnType<typeof buildTrace>> = await buildTrace(
          w.harness.session,
          conv.id,
          { limit: 2, cursor },
        );
        seen.push(...page.entries.map((e) => e.id));
        for (const c of Object.values(page.children) as Any[]) {
          seen.push(...c.entries.map((e: Any) => e.id));
        }
        expect(page.truncated).toBe(page.nextCursor !== null);
        cursor = page.nextCursor;
        pages++;
      } while (cursor !== null && pages < 20);
      expect(pages).toBeGreaterThan(1);
      expect(seen).toHaveLength(total);
      expect(new Set(seen).size).toBe(total);
      await w.crash();
    });

    it("reports compactions and taint, and masks secrets in agent metadata", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeText("hi"));
      const w = await fx.open({});
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: {
          systemPrompt: "s",
          tools: [],
          metadata: { apiKey: "sk-abcdefghijklmnopqrstuvwxyz0123456789", team: "a" },
        },
      });
      await (await conv.submit({ text: "go" })).wait();
      await w.harness.runtime.commit(async (tx) => {
        tx.appendEntry({
          conversationId: conv.id,
          taskId: null,
          kind: "compaction",
          payload: { summary: "old stuff", firstKeptEntryId: null },
        });
      });
      const trace = await buildTrace(w.harness.session, conv.id);
      expect(trace.compactions).toHaveLength(1);
      expect(trace.compactions[0]).toMatchObject({ summaryChars: "old stuff".length });
      expect(JSON.stringify(trace)).not.toContain("sk-abcdefghijklmnop");
      expect(trace.agent.metadata).toMatchObject({ team: "a" });
      await w.crash();
    });

    it("says how each submission ended, the unanswered reason included, for the root and each child", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeText("first answer"),
        fakeToolCall("research", { prompt: "one" }, { id: "c1" }),
        fakeText("child one"),
        fakeText("second answer"),
        fakeError("invalid_request", "the model refused"),
      );
      const w = await fx.open({ tools: [research, quickWait()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["research"] },
      });
      await (await conv.submit({ text: "one", requestId: "r1" })).wait();
      await (await conv.submit({ text: "two", requestId: "r2" })).wait();
      await (await conv.submit({ text: "three", requestId: "r3" })).wait();

      const trace = await buildTrace(w.harness.session, conv.id);
      const byRequest = Object.fromEntries(trace.submissions.map((s) => [s.requestId, s]));
      expect(byRequest.r1).toMatchObject({ status: "done", unanswered: null });
      expect(byRequest.r2).toMatchObject({ status: "done", unanswered: null });
      expect(byRequest.r3?.status).toBe("unanswered");
      expect(byRequest.r3?.unanswered).toEqual({
        reason: "model_error",
        message: expect.any(String),
      });
      const [child] = Object.values(trace.children);
      expect(child?.submissions).toHaveLength(1);
      expect(child?.submissions[0]).toMatchObject({ status: "done", unanswered: null });
      await w.crash();
    });

    it("never shows a settled submission without its final entries, whenever a commit lands mid-read", async () => {
      // Fire the run's final commit after the k-th storage read of the trace.
      for (let k = 1; k <= 12; k++) {
        const fx = await backend.fixture();
        let release: () => void = () => {};
        const gate = new Promise<void>((r) => {
          release = r;
        });
        const hold = defineTool({
          name: "hold",
          description: "Waits for the test",
          parameters: z.object({}),
          replay: "safe",
          execute: async () => {
            await gate;
            return { content: "released" };
          },
        });
        fx.provider.enqueue(fakeToolCall("hold", {}, { id: "h1" }), fakeText("final answer"));
        const w = await fx.open({ tools: [hold] });
        const conv = await w.harness.createConversation({
          tenantId: "t1",
          projectId: "p1",
          agent: { systemPrompt: "s", tools: ["hold"] },
        });
        const sub = await conv.submit({ text: "go" });
        await new Promise((r) => setTimeout(r, 50));

        let reads = 0;
        const real = w.harness.session;
        const reader = new Proxy(real.reader, {
          get(target, prop, recv) {
            const fn = Reflect.get(target, prop, recv);
            if (typeof fn !== "function") return fn;
            return async (...args: unknown[]) => {
              const out = await fn.apply(target, args);
              if (++reads === k) {
                release();
                await sub.wait();
              }
              return out;
            };
          },
        });
        const session = new Proxy(real, {
          get(target, prop) {
            if (prop === "reader") return reader;
            const v = Reflect.get(target, prop, target);
            return typeof v === "function" ? v.bind(target) : v;
          },
        });
        const trace = await buildTrace(session, conv.id);
        release();
        await sub.wait();
        const settled = trace.submissions.some((s) => s.status === "done");
        const answered = trace.entries.some(
          (e) => e.kind === "assistant" && JSON.stringify(e.payload).includes("final answer"),
        );
        if (settled) expect(answered, `commit after read ${k}`).toBe(true);
        await w.crash();
      }
    });

    it("rejects an unknown conversation and a malformed cursor", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({});
      await expect(buildTrace(w.harness.session, "conv_missing")).rejects.toThrow();
      const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
      await expect(
        buildTrace(w.harness.session, conv.id, { cursor: "not-a-cursor" }),
      ).rejects.toThrow(/cursor/);
      await w.crash();
    });
  });
}
