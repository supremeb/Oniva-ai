/**
 * Submissions and the inbox (U8): idempotent submits and busy handling, run
 * against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { Conversation } from "../../src/agent/conversation.js";
import type { Harness } from "../../src/agent/harness.js";
import { ConversationBusy } from "../../src/core/errors.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const payload = (e: EntryRecord): Any => e.payload as Any;
const kinds = (entries: EntryRecord[]) => entries.map((e) => e.kind);

/** A replay-safe tool that blocks until `open()` (or its abort). */
function gateTool() {
  let release: () => void = () => {};
  let opened = false;
  let started = 0;
  const gate = new Promise<void>((r) => {
    release = () => {
      opened = true;
      r();
    };
  });
  const tool = defineTool({
    name: "wait",
    description: "Wait for the gate",
    parameters: z.object({}),
    replay: "safe",
    execute: async (_args, api) => {
      started++;
      if (!opened) await Promise.race([gate, untilAborted(api.signal)]);
      return { content: "gate opened" };
    },
  });
  return {
    tool,
    open: () => release(),
    started: () => started,
  };
}

const AGENT = { systemPrompt: "s", tools: ["wait"] };

async function setup(h: Harness): Promise<Conversation> {
  return h.createConversation({ tenantId: "t1", projectId: "p1", agent: AGENT });
}

/** Wait until a tool task of the conversation is executing. */
async function toolRunning(h: Harness, n = 1): Promise<void> {
  await waitFor(
    () =>
      h.runtime.tasks().filter((t) => t.kind === "agent.tool" && t.phase === "execute").length >= n,
  );
}

export function defineInboxSuite(backend: AgentBackend): void {
  describe(`submissions & inbox (${backend.name})`, () => {
    it("AE6: a duplicate requestId returns the same submission, before and after a reopen", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeText("hi"));
      const g = gateTool();
      const w1 = await fx.open({ tools: [g.tool] });
      const conv = await setup(w1.harness);
      const a = await conv.submit({ text: "hello", requestId: "r1" });
      const b = await conv.submit({ text: "hello", requestId: "r1" });
      expect(b.id).toBe(a.id);
      expect((await a.wait()).status).toBe("done");
      const c = await conv.submit({ text: "hello", requestId: "r1", whenBusy: "reject" });
      expect(c.id).toBe(a.id);
      await w1.crash();

      const w2 = await fx.open({ tools: [g.tool] });
      const conv2 = w2.harness.conversation(conv.id);
      const d = await conv2.submit({ text: "hello", requestId: "r1" });
      expect(d.id).toBe(a.id);
      expect((await d.wait()).status).toBe("done");
      const entries = await conv2.entries();
      expect(kinds(entries).filter((k) => k === "user")).toHaveLength(1);
      expect(fx.provider.requests).toHaveLength(1);
      const subs = await w2.harness.session.reader.listSubmissions(conv.id);
      expect(subs.items).toHaveLength(1);
      await w2.crash();
    });

    it("AE6: a duplicate requestId while busy (queued) returns the same submission", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }), fakeText("one"), fakeText("two"));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const first = await conv.submit({ text: "go" });
      await toolRunning(w.harness);
      const q1 = await conv.submit({ text: "next", requestId: "q" });
      const q2 = await conv.submit({ text: "next", requestId: "q", whenBusy: "steer" });
      expect(q2.id).toBe(q1.id);
      g.open();
      expect((await first.wait()).status).toBe("done");
      expect(((await q1.wait()).result as Any).text).toBe("two");
      expect(kinds(await conv.entries()).filter((k) => k === "user")).toHaveLength(2);
      await w.crash();
    });

    it("AE4: a steer during a tool round lands after the results and before the next request, same run", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }), fakeText("steered answer"));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const main = await conv.submit({ text: "do it" });
      await toolRunning(w.harness);
      const steer = await conv.submit({ text: "use blue", whenBusy: "steer" });
      expect((await steer.record())?.status).toBe("pending");
      // Not yet in the transcript: it waits for the round boundary.
      expect(kinds(await conv.entries())).toEqual(["user", "system", "assistant"]);
      g.open();

      const settled = await main.wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("steered answer");
      const steerSettled = await steer.wait();
      expect(steerSettled.status).toBe("done");
      expect((steerSettled.result as Any).text).toBe("steered answer");

      const entries = await conv.entries();
      expect(kinds(entries)).toEqual([
        "user",
        "system",
        "assistant",
        "tool_result",
        "user",
        "assistant",
      ]);
      expect(payload(entries[4] as EntryRecord)).toEqual({
        submissionId: steer.id,
        content: "use blue",
      });
      const runIds = entries.filter((e) => e.kind === "assistant").map((e) => payload(e).runId);
      expect(new Set(runIds).size).toBe(1);
      const second = fx.provider.requests[1] as ModelRequest;
      expect(second.messages.map((m) => m.role)).toEqual([
        "system",
        "user",
        "assistant",
        "tool_result",
        "user",
      ]);
      expect(fx.provider.requests).toHaveLength(2);
      expect(conv.busy()).toBe(false);
      await w.crash();
    });

    it("a steer to a conversation that is no longer busy starts a run", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }), fakeText("first"));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const main = await conv.submit({ text: "go" });
      await toolRunning(w.harness);
      // Make the second request answer without tools, then steer after the boundary.
      g.open();
      await main.wait();
      fx.provider.enqueue(fakeText("second"));
      const s = await conv.submit({ text: "late", whenBusy: "steer" });
      expect(((await s.wait()).result as Any).text).toBe("second");
      await w.crash();
    });

    it("a follow-up submitted while busy starts a second run after the first answers", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("wait", {}, { id: "c1" }),
        fakeText("first"),
        fakeText("second"),
      );
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const main = await conv.submit({ text: "one" });
      await toolRunning(w.harness);
      const follow = await conv.submit({ text: "two" });
      expect((await follow.record())?.status).toBe("pending");
      expect(kinds(await conv.entries())).toEqual(["user", "system", "assistant"]);
      g.open();
      expect(((await main.wait()).result as Any).text).toBe("first");
      expect(((await follow.wait()).result as Any).text).toBe("second");

      const entries = await conv.entries();
      expect(kinds(entries)).toEqual([
        "user",
        "system",
        "assistant",
        "tool_result",
        "assistant",
        "user",
        "assistant",
      ]);
      const runIds = entries.filter((e) => e.kind === "assistant").map((e) => payload(e).runId);
      expect(runIds[0]).toBe(runIds[1]);
      expect(runIds[2]).not.toBe(runIds[0]);
      expect(fx.provider.requests).toHaveLength(3);
      expect(conv.busy()).toBe(false);
      await w.crash();
    });

    it("reject while busy throws ConversationBusy and stores nothing", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }), fakeText("done"));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const main = await conv.submit({ text: "one", whenBusy: "reject" });
      await toolRunning(w.harness);
      const before = await conv.entries();
      await expect(
        conv.submit({ text: "two", whenBusy: "reject", requestId: "x" }),
      ).rejects.toBeInstanceOf(ConversationBusy);
      expect(await conv.entries()).toEqual(before);
      const subs = await w.harness.session.reader.listSubmissions(conv.id);
      expect(subs.items.map((s) => s.id)).toEqual([main.id]);
      expect(await w.harness.session.reader.submissionByRequestId(conv.id, "x")).toBeNull();
      g.open();
      await main.wait();
      await w.crash();
    });

    it("a display-only write is appended immediately, without a run, idle or busy", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }), fakeText("done"));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const note = await conv.submit({ text: "fyi", type: "write" });
      const noteSettled = await note.wait();
      expect(noteSettled.status).toBe("done");
      expect(conv.busy()).toBe(false);
      expect(kinds(await conv.entries())).toEqual(["display"]);
      expect(fx.provider.requests).toHaveLength(0);

      const main = await conv.submit({ text: "go" });
      await toolRunning(w.harness);
      const busyNote = await conv.submit({ text: "still here", type: "write", whenBusy: "reject" });
      expect((await busyNote.wait()).status).toBe("done");
      expect(kinds(await conv.entries())).toEqual([
        "display",
        "user",
        "system",
        "assistant",
        "display",
      ]);
      const dup = await conv.submit({ text: "fyi", type: "write", requestId: note.id });
      expect(dup.id).toBe(note.id);
      g.open();
      await main.wait();
      // Display-only entries never reach the model.
      const second = fx.provider.requests[1] as ModelRequest;
      expect(second.messages.map((m) => m.role)).toEqual([
        "system",
        "user",
        "assistant",
        "tool_result",
      ]);
      expect(kinds(await conv.entries()).filter((k) => k === "display")).toHaveLength(2);
      await w.crash();
    });

    it("abort() while busy withdraws queued inputs and leaves the conversation idle", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }));
      const g = gateTool();
      const w = await fx.open({ tools: [g.tool] });
      const conv = await setup(w.harness);
      const main = await conv.submit({ text: "one" });
      await toolRunning(w.harness);
      const follow = await conv.submit({ text: "two" });
      const steer = await conv.submit({ text: "three", whenBusy: "steer" });
      await conv.abort();

      expect(conv.busy()).toBe(false);
      expect(
        w.harness.runtime.tasks({ conversationId: conv.id }).every((t) => t.status === "terminal"),
      ).toBe(true);
      const m = await main.wait();
      expect(m.status).toBe("unanswered");
      expect((m.result as Any).reason).toBe("aborted");
      for (const s of [follow, steer]) {
        const r = await s.wait();
        expect(r.status).toBe("unanswered");
        expect((r.result as Any).reason).toBe("withdrawn");
      }
      expect(kinds(await conv.entries()).filter((k) => k === "user")).toHaveLength(1);

      // The conversation takes new input afterwards.
      fx.provider.enqueue(fakeText("fresh"));
      const again = await conv.submit({ text: "again", whenBusy: "reject" });
      expect(((await again.wait()).result as Any).text).toBe("fresh");
      await w.crash();
    });

    it("abort() on an idle conversation is a no-op", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ tools: [gateTool().tool] });
      const conv = await setup(w.harness);
      await conv.abort();
      expect(conv.busy()).toBe(false);
      await w.crash();
    });

    it("queued follow-ups and steers survive a crash and are processed after reopen", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("wait", {}, { id: "c1" }));
      const g1 = gateTool();
      const w1 = await fx.open({ tools: [g1.tool] });
      const conv = await setup(w1.harness);
      const main = await conv.submit({ text: "one" });
      await toolRunning(w1.harness);
      const steer = await conv.submit({ text: "steer me", whenBusy: "steer" });
      const follow = await conv.submit({ text: "two", whenBusy: "followUp" });
      await w1.crash();

      fx.provider.enqueue(fakeText("first"), fakeText("second"));
      const g2 = gateTool();
      g2.open();
      const w2 = await fx.open({ tools: [g2.tool] });
      const h = w2.harness;
      expect(((await h.submission(main.id).wait()).result as Any).text).toBe("first");
      expect(((await h.submission(steer.id).wait()).result as Any).text).toBe("first");
      expect(((await h.submission(follow.id).wait()).result as Any).text).toBe("second");
      const entries = await h.conversation(conv.id).entries();
      expect(kinds(entries)).toEqual([
        "user",
        "system",
        "assistant",
        "tool_result",
        "user",
        "assistant",
        "user",
        "assistant",
      ]);
      expect(payload(entries[4] as EntryRecord).submissionId).toBe(steer.id);
      expect(payload(entries[6] as EntryRecord).submissionId).toBe(follow.id);
      await waitFor(() => !h.conversation(conv.id).busy());
      await w2.crash();
    });
  });
}
