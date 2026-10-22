/**
 * Subagent context passing and fork mode (U20, R17), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { FORK_HEADER } from "../../src/agent/context-seed.js";
import { agentConfigDoc } from "../../src/agent/prompt.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { rootConversationId, taintDoc } from "../../src/agent/taint.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, allowAll } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const probe = () =>
  defineTool({
    name: "probe",
    description: "Reads something",
    parameters: z.object({}),
    replay: "safe",
    execute: () => ({ content: "SECRET-BODY-123" }),
  });

const firstUser = (request: Any): string =>
  (request.messages as Any[]).find((m) => m.role === "user").content as string;

export function defineSubagentContextSuite(backend: AgentBackend): void {
  describe(`subagent context (${backend.name})`, () => {
    it("an isolated child with no context sees exactly the prompt", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research" });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "find x" }, { id: "c1" }),
        fakeText("x"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { tools: ["research"] },
      });
      await (await conv.submit({ text: "go" })).wait();
      expect(firstUser(fx.provider.requests[1])).toBe("find x");
      await w.crash();
    });

    it("contextPreamble, brief and files come in order before the task", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research" });
      fx.provider.enqueue(
        fakeToolCall(
          "research",
          {
            prompt: "find x",
            context: { brief: "others cover B and C", files: ["notes/a.md", "notes/b.md"] },
          },
          { id: "c1" },
        ),
        fakeText("x"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: {
          tools: ["research"],
          metadata: { subagents: { research: { contextPreamble: "You are worker A." } } },
        },
      });
      await (await conv.submit({ text: "go" })).wait();
      const text = firstUser(fx.provider.requests[1]);
      const at = (s: string) => text.indexOf(s);
      expect(at("You are worker A.")).toBe(0);
      expect(at("others cover B and C")).toBeGreaterThan(at("You are worker A."));
      expect(at("notes/a.md")).toBeGreaterThan(at("others cover B and C"));
      expect(at("notes/b.md")).toBeGreaterThan(at("notes/a.md"));
      expect(at("find x")).toBeGreaterThan(at("notes/b.md"));
      expect(text).not.toContain(FORK_HEADER);
      await w.crash();
    });

    it("fork: the child's first entry narrates the parent's turns with result bodies elided", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", mode: "fork" });
      fx.provider.enqueue(
        fakeText("Hi, I can help with the market study."),
        fakeToolCall("probe", {}, { id: "p1" }),
        fakeToolCall("research", { prompt: "close the gap on pricing" }, { id: "c1" }),
        fakeText("gap closed"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research, probe()], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { tools: ["research", "probe"] },
      });
      await (await conv.submit({ text: "hello, study the market" })).wait();
      await (await conv.submit({ text: "now fill the pricing gap" })).wait();
      const text = firstUser(fx.provider.requests[3]);
      expect(text.startsWith(FORK_HEADER)).toBe(true);
      expect(text).toContain("The user said: hello, study the market");
      expect(text).toContain("The parent replied: Hi, I can help with the market study.");
      expect(text).toContain("The parent called the tool probe.");
      expect(text).toContain("The tool probe returned a result to the parent (15 characters");
      expect(text).not.toContain("SECRET-BODY-123");
      expect(text.endsWith("[Task]\nclose the gap on pricing")).toBe(true);
      // Depth and recovery are unaffected: one child, depth 1.
      const convs = (await w.harness.session.reader.listConversations({ limit: 10 })).items;
      expect(convs).toHaveLength(2);
      const child = convs.find((c) => c.id !== conv.id);
      expect((await w.harness.session.document(agentConfigDoc, child?.id as string)).depth).toBe(1);
      await w.crash();
    });

    it("fork via the metadata override; other tools stay isolated", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research" });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeText("ok"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { tools: ["research"], metadata: { subagents: { research: { mode: "fork" } } } },
      });
      await (await conv.submit({ text: "go on" })).wait();
      expect(firstUser(fx.provider.requests[1])).toContain(FORK_HEADER);
      await w.crash();
    });

    it("a tainted parent gives a tainted fork: the same root taint, flagged in the block", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", mode: "fork" });
      fx.provider.enqueue(
        fakeToolCall("probe", {}, { id: "p1" }),
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeText("ok"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research, probe()], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { tools: ["research", "probe"] },
      });
      await (await conv.submit({ text: "go" })).wait();
      expect(firstUser(fx.provider.requests[2])).toContain("does not trust");
      const convs = (await w.harness.session.reader.listConversations({ limit: 10 })).items;
      const child = convs.find((c) => c.id !== conv.id);
      expect(await rootConversationId(w.harness.session.reader, child as Any)).toBe(conv.id);
      expect((await w.harness.session.document(taintDoc, conv.id)).tainted).toBe(true);
      await w.crash();
    });

    it("an oversize brief is rejected as invalid_arguments and creates no child", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research" });
      fx.provider.enqueue(
        fakeToolCall(
          "research",
          { prompt: "dig", context: { brief: "x".repeat(4001) } },
          { id: "c1" },
        ),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research], policy: allowAll });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { tools: ["research"] },
      });
      await (await conv.submit({ text: "go" })).wait();
      const result = (await conv.entries()).find((e) => e.kind === "tool_result")?.payload as Any;
      expect(result.status).toBe("invalid_arguments");
      expect((await w.harness.session.reader.listConversations({ limit: 10 })).items).toHaveLength(
        1,
      );
      await w.crash();
    });
  });
}
