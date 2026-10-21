/**
 * Tier 1 view-time clearing (U13), run against every storage backend: when
 * the watermark advances, what the model then sees, cache-stable prefixes,
 * replay after a crash, and turning the setting off and on.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { contextDoc } from "../../src/agent/context-clearing.js";
import { type FakeScript, fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { Message, ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, allowAll, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

/** 2000 characters: 500 estimated tokens. */
const BODY = "x".repeat(2000);
const USAGE = { usage: { inputTokens: 1000, outputTokens: 1 } };

/** Trigger 500; a clear needs 1200 tokens (three results of 500) to be worth it. */
const CLEARING = {
  clearTriggerTokens: 500,
  clearAtLeastTokens: 1200,
  keepToolResults: 2,
  excludeTools: ["keep"],
};

const stub = (tool: string) => `[cleared: ${tool} result, ~500 tokens. Re-run the tool.]`;

const bigTool = (name: string) =>
  defineTool({
    name,
    description: name,
    parameters: z.object({}),
    replay: "safe",
    execute: async () => ({ content: BODY }),
  });

const call = (name: string, id: string): FakeScript => ({
  ...fakeToolCall(name, {}, { id }),
  ...USAGE,
});

const results = (r: ModelRequest): Message[] => r.messages.filter((m) => m.role === "tool_result");
const content = (m: Message): string => (m as Any).content as string;

export function defineContextClearingSuite(backend: AgentBackend): void {
  describe(`context clearing (${backend.name})`, () => {
    const open = async (compaction: Record<string, unknown> = CLEARING) => {
      const fx = await backend.fixture();
      const w = await fx.open({
        policy: allowAll,
        tools: [bigTool("big"), bigTool("keep")],
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "sys", tools: ["big", "keep"], compaction },
      });
      return { fx, w, conv };
    };

    it("below the trigger, or above it but under clearAtLeastTokens: no change, no document write", async () => {
      const low = await open({ ...CLEARING, clearTriggerTokens: 1_000_000 });
      low.fx.provider.enqueue(
        ...[1, 2, 3, 4, 5].map((n) => call("big", `c${n}`)),
        fakeText("done"),
      );
      await (await low.conv.submit({ text: "go" })).wait();
      let last = low.fx.provider.requests.at(-1) as ModelRequest;
      expect(results(last).map(content)).toEqual(Array(5).fill(BODY));
      expect((await low.w.harness.session.document(contextDoc, low.conv.id)).clearedTokens).toBe(0);
      await low.w.crash();

      const small = await open({ ...CLEARING, clearAtLeastTokens: 100_000 });
      small.fx.provider.enqueue(
        ...[1, 2, 3, 4, 5].map((n) => call("big", `c${n}`)),
        fakeText("done"),
      );
      await (await small.conv.submit({ text: "go" })).wait();
      last = small.fx.provider.requests.at(-1) as ModelRequest;
      expect(results(last).map(content)).toEqual(Array(5).fill(BODY));
      const doc = await small.w.harness.session.document(contextDoc, small.conv.id);
      expect(doc).toEqual({ clearedThroughEntryId: null, clearedTokens: 0 });
      await small.w.crash();
    });

    it("above both: the watermark is set, the newest results and excluded tools stay, calls keep their results", async () => {
      const { fx, w, conv } = await open();
      fx.provider.enqueue(
        call("big", "c1"),
        call("keep", "c2"),
        call("big", "c3"),
        call("big", "c4"),
        call("big", "c5"),
        call("big", "c6"),
        fakeText("done"),
      );
      await (await conv.submit({ text: "go" })).wait();

      const last = fx.provider.requests.at(-1) as ModelRequest;
      const res = results(last);
      expect(res.map((m) => (m as Any).toolCallId)).toEqual(["c1", "c2", "c3", "c4", "c5", "c6"]);
      expect(res.map(content)).toEqual([
        stub("big"),
        BODY, // keep: excluded
        stub("big"),
        stub("big"),
        BODY, // newest two stay verbatim
        BODY,
      ]);
      // Every assistant tool call is followed by its result.
      const calls = last.messages.flatMap((m) =>
        m.role === "assistant" ? m.blocks.filter((b) => b.type === "tool_call") : [],
      );
      expect(calls.map((b) => (b as Any).id)).toEqual(["c1", "c2", "c3", "c4", "c5", "c6"]);

      const entries = await conv.entries();
      const c4 = entries.find((e) => (e.payload as Any).toolCallId === "c4") as EntryRecord;
      const doc = await w.harness.session.document(contextDoc, conv.id);
      expect(doc).toEqual({ clearedThroughEntryId: c4.id, clearedTokens: 1500 });
      // Stored entries are untouched.
      expect((c4.payload as Any).content).toBe(BODY);
      await w.crash();
    });

    it("two requests without a new advance have byte-identical message prefixes", async () => {
      const { fx, w, conv } = await open();
      fx.provider.enqueue(
        ...[1, 2, 3, 4, 5].map((n) => call("big", `c${n}`)),
        fakeText("first done"),
        fakeText("second done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      await (await conv.submit({ text: "again" })).wait();
      const [a, b] = fx.provider.requests.slice(-2) as [ModelRequest, ModelRequest];
      expect(JSON.stringify(b.messages.slice(0, a.messages.length))).toBe(
        JSON.stringify(a.messages),
      );
      await w.crash();
    });

    it("a rerun of request after the watermark commit does not advance it again", async () => {
      const { fx, w, conv } = await open();
      fx.provider.enqueue(...[1, 2, 3, 4, 5].map((n) => call("big", `c${n}`)), {
        steps: [
          { type: "delay", ms: 60_000 },
          { type: "text", text: "never" },
        ],
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(() => fx.provider.requests.length === 6);
      const before = await w.harness.session.document(contextDoc, conv.id);
      expect(before.clearedThroughEntryId).not.toBeNull();
      await w.crash();

      fx.provider.enqueue(fakeText("after crash"));
      const w2 = await fx.open({
        policy: allowAll,
        tools: [bigTool("big"), bigTool("keep")],
      });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      expect(await w2.harness.session.document(contextDoc, conv.id)).toEqual(before);
      const first = fx.provider.requests[5] as ModelRequest;
      const retry = fx.provider.requests.at(-1) as ModelRequest;
      expect(retry.messages).toEqual(first.messages);
      await w2.crash();
    });

    it("disabling the setting restores verbatim results; re-enabling resumes from the stored watermark", async () => {
      const { fx, w, conv } = await open();
      fx.provider.enqueue(
        ...[1, 2, 3, 4, 5].map((n) => call("big", `c${n}`)),
        fakeText("one"),
        fakeText("two"),
        fakeText("three"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const stored = await w.harness.session.document(contextDoc, conv.id);
      expect(stored.clearedThroughEntryId).not.toBeNull();

      await conv.configure({ compaction: { ...CLEARING, clearTriggerTokens: 0 } });
      await (await conv.submit({ text: "off" })).wait();
      let last = fx.provider.requests.at(-1) as ModelRequest;
      expect(results(last).map(content)).toEqual(Array(5).fill(BODY));

      await conv.configure({ compaction: CLEARING });
      await (await conv.submit({ text: "on" })).wait();
      last = fx.provider.requests.at(-1) as ModelRequest;
      expect(results(last).map(content)).toEqual([
        stub("big"),
        stub("big"),
        stub("big"),
        BODY,
        BODY,
      ]);
      expect(await w.harness.session.document(contextDoc, conv.id)).toEqual(stored);
      await w.crash();
    });
  });
}
