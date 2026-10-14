/**
 * Compaction (U11), run against every storage backend: the blocking and
 * background thresholds, the context-overflow retry, failure handling and
 * crash recovery.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { COMPACTION_TASK_KIND } from "../../src/agent/compaction.js";
import { SUMMARY_PREFIX } from "../../src/agent/context-builder.js";
import type { Conversation } from "../../src/agent/conversation.js";
import type { Harness } from "../../src/agent/harness.js";
import type { HostPolicy, ModelPurpose } from "../../src/agent/policy.js";
import { usageDoc } from "../../src/agent/usage.js";
import {
  type FakeScript,
  fakeContextOverflow,
  fakeError,
  fakeText,
  fakeToolCall,
} from "../../src/models/fake.js";
import type { ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, type AgentFixture, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const payload = (e: EntryRecord): Any => e.payload as Any;
const kinds = (entries: EntryRecord[]) => entries.map((e) => e.kind);

const SUMMARY_MODEL = "fake-summary";

/**
 * Window 1000, reserve 100, margin 200: background compaction above 700
 * estimated tokens, blocking at 900.
 */
const COMPACTION = {
  contextWindow: 1000,
  reserveTokens: 100,
  backgroundMargin: 200,
  keepRecentTokens: 50,
};

function policy(purposes: ModelPurpose[] = []): HostPolicy {
  return {
    resolveModelRequest: (info) => {
      purposes.push(info.purpose);
      return {
        ok: true,
        provider: "fake",
        model: info.purpose === "compaction" ? SUMMARY_MODEL : "fake-1",
        apiKey: "test-key",
        maxTokens: 1024,
      };
    },
    authorizeTool: () => ({ decision: "allow" }),
  };
}

/**
 * The fake provider serves requests in arrival order; a background
 * compaction races the generation, so every queued script dispatches on the
 * request's model to one of two queues.
 */
function scripts(fx: AgentFixture) {
  const replies: FakeScript[] = [];
  const summaries: FakeScript[] = [];
  const pick = (r: ModelRequest) => {
    const s = (r.model === SUMMARY_MODEL ? summaries : replies).shift();
    if (!s) return fakeError("provider", `no ${r.model} script left`);
    return typeof s === "function" ? s(r) : s;
  };
  return {
    reply(...s: FakeScript[]) {
      replies.push(...s);
      for (const _ of s) fx.provider.enqueue(pick);
    },
    summary(...s: FakeScript[]) {
      summaries.push(...s);
      for (const _ of s) fx.provider.enqueue(pick);
    },
  };
}

const genRequests = (fx: AgentFixture) =>
  fx.provider.requests.filter((r) => r.model !== SUMMARY_MODEL);
const summaryRequests = (fx: AgentFixture) =>
  fx.provider.requests.filter((r) => r.model === SUMMARY_MODEL);

function gateTool() {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => {
    release = r;
  });
  const tool = defineTool({
    name: "wait",
    description: "Wait for the gate",
    parameters: z.object({}),
    replay: "safe",
    execute: async (_args, api) => {
      await Promise.race([gate, untilAborted(api.signal)]);
      return { content: "gate opened" };
    },
  });
  return { tool, open: () => release() };
}

async function setup(h: Harness, tools: string[] = []): Promise<Conversation> {
  return h.createConversation({
    tenantId: "t1",
    projectId: "p1",
    agent: { systemPrompt: "sys", tools, compaction: COMPACTION },
  });
}

const compactionTasks = (h: Harness) =>
  h.runtime.tasks().filter((t) => t.kind === COMPACTION_TASK_KIND);

export function defineCompactionSuite(backend: AgentBackend): void {
  describe(`compaction (${backend.name})`, () => {
    it("past the blocking threshold the next request waits for a compaction and starts at it", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const purposes: ModelPurpose[] = [];
      s.reply(fakeText("first answer", { usage: { inputTokens: 950, outputTokens: 1 } }));
      s.reply(fakeText("second answer"));
      s.summary(fakeText("SUMMARY-1", { usage: { inputTokens: 40, outputTokens: 7 } }));
      const w = await fx.open({ policy: policy(purposes) });
      const conv = await setup(w.harness);

      expect((await (await conv.submit({ text: "question one" })).wait()).status).toBe("done");
      expect(compactionTasks(w.harness)).toHaveLength(0);

      const settled = await (await conv.submit({ text: "question two" })).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("second answer");

      // The summary request came before the second generation request.
      expect(fx.provider.requests.map((r) => r.model)).toEqual(["fake-1", SUMMARY_MODEL, "fake-1"]);
      expect(purposes).toEqual(["generation", "compaction", "generation"]);
      const summaryInput = JSON.stringify(summaryRequests(fx)[0]?.messages);
      expect(summaryInput).toContain("question one");
      expect(summaryInput).toContain("first answer");

      const entries = await conv.entries();
      expect(kinds(entries)).toEqual([
        "user",
        "system",
        "assistant",
        "user",
        "compaction",
        "assistant",
      ]);
      const compaction = payload(entries[4] as EntryRecord);
      expect(compaction.summary).toBe("SUMMARY-1");
      expect(compaction.firstKeptEntryId).toBe((entries[3] as EntryRecord).id);

      // The second request's context starts at the compaction entry.
      const second = genRequests(fx)[1] as ModelRequest;
      expect(second.messages).toEqual([
        { role: "system", text: "sys" },
        { role: "user", content: `${SUMMARY_PREFIX}SUMMARY-1` },
        { role: "user", content: "question two" },
      ]);

      // Summary spend counts toward usage, under its own purpose.
      const usage = await w.harness.session.document(usageDoc, conv.id);
      expect(usage.requests).toBe(3);
      expect(usage.byModel[`fake/${SUMMARY_MODEL}`]?.inputTokens).toBe(40);
      expect(usage.byPurpose.compaction?.requests).toBe(1);
      expect(usage.byPurpose.generation?.requests).toBe(2);
      expect(w.harness.runtime.tasks().every((t) => t.status === "terminal")).toBe(true);
      await w.crash();
    });

    it("a background compaction runs while answering continues; its summary lands at the next turn", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", { usage: { inputTokens: 750, outputTokens: 1 } }));
      s.reply(fakeText("second answer", { usage: { inputTokens: 760, outputTokens: 1 } }));
      s.reply(fakeText("third answer"));
      s.summary({
        steps: [
          { type: "delay", ms: 5_000 },
          { type: "text", text: "SUMMARY-BG" },
        ],
      });
      const w = await fx.open({ policy: policy() });
      const conv = await setup(w.harness);

      await (await conv.submit({ text: "question one" })).wait();
      const second = await (await conv.submit({ text: "question two" })).wait();
      // Answered while the summary request is still in flight.
      expect((second.result as Any).text).toBe("second answer");
      const [task] = compactionTasks(w.harness);
      expect(task?.status).not.toBe("terminal");
      expect(kinds(await conv.entries())).not.toContain("compaction");

      await waitFor(() => summaryRequests(fx).length === 1);
      await fx.clock.advanceAsync(5_000);
      await waitFor(() => w.harness.runtime.task(task?.id as string)?.status === "terminal");
      expect(w.harness.runtime.task(task?.id as string)?.outcome?.type).toBe("done");
      // Done, but not placed until the next turn boundary.
      expect(kinds(await conv.entries())).not.toContain("compaction");

      const third = await (await conv.submit({ text: "question three" })).wait();
      expect((third.result as Any).text).toBe("third answer");
      const entries = await conv.entries();
      expect(kinds(entries).slice(-3)).toEqual(["user", "compaction", "assistant"]);
      const last = genRequests(fx).at(-1) as ModelRequest;
      expect(last.messages[1]).toEqual({ role: "user", content: `${SUMMARY_PREFIX}SUMMARY-BG` });
      expect(last.messages.at(-1)).toEqual({ role: "user", content: "question three" });
      // One compaction only: the placed summary brought the estimate down.
      expect(compactionTasks(w.harness)).toHaveLength(1);
      await w.crash();
    });

    it("a background summary finished during a tool round is placed before the next request of the run", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const g = gateTool();
      s.reply(fakeText("first answer", { usage: { inputTokens: 750, outputTokens: 1 } }));
      s.reply(fakeToolCall("wait", {}, { id: "c1" }));
      s.reply(fakeText("final"));
      s.summary({
        steps: [
          { type: "delay", ms: 1_000 },
          { type: "text", text: "SUMMARY-MID" },
        ],
      });
      const w = await fx.open({ policy: policy(), tools: [g.tool] });
      const conv = await setup(w.harness, ["wait"]);

      await (await conv.submit({ text: "question one" })).wait();
      const sub = await conv.submit({ text: "question two" });
      await waitFor(() =>
        w.harness.runtime.tasks().some((t) => t.kind === "agent.tool" && t.phase === "execute"),
      );
      await waitFor(() => summaryRequests(fx).length === 1);
      await fx.clock.advanceAsync(1_000);
      await waitFor(() => compactionTasks(w.harness).every((t) => t.status === "terminal"));
      expect(kinds(await conv.entries())).not.toContain("compaction");

      g.open();
      const settled = await sub.wait();
      expect((settled.result as Any).text).toBe("final");
      const entries = await conv.entries();
      expect(kinds(entries).slice(-4)).toEqual([
        "assistant",
        "tool_result",
        "compaction",
        "assistant",
      ]);
      const last = genRequests(fx).at(-1) as ModelRequest;
      expect(last.messages[1]).toEqual({ role: "user", content: `${SUMMARY_PREFIX}SUMMARY-MID` });
      // The tool call stays with its result.
      expect(last.messages.slice(-2).map((m) => m.role)).toEqual(["assistant", "tool_result"]);
      await w.crash();
    });

    it("one context overflow causes one compaction and one retry", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer"), fakeContextOverflow(), fakeText("retried answer"));
      s.summary(fakeText("SUMMARY-OVF"));
      const w = await fx.open({ policy: policy() });
      const conv = await setup(w.harness);

      await (await conv.submit({ text: "question one" })).wait();
      const settled = await (await conv.submit({ text: "question two" })).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("retried answer");
      expect(fx.provider.requests.map((r) => r.model)).toEqual([
        "fake-1",
        "fake-1",
        SUMMARY_MODEL,
        "fake-1",
      ]);
      const retry = genRequests(fx)[2] as ModelRequest;
      expect(retry.messages[1]).toEqual({ role: "user", content: `${SUMMARY_PREFIX}SUMMARY-OVF` });
      const entries = await conv.entries();
      expect(kinds(entries)).not.toContain("run_error");
      expect(kinds(entries).filter((k) => k === "compaction")).toHaveLength(1);
      await w.crash();
    });

    it("a second overflow fails the run with model_error", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer"), fakeContextOverflow(), fakeContextOverflow());
      s.summary(fakeText("SUMMARY-OVF"));
      const w = await fx.open({ policy: policy() });
      const conv = await setup(w.harness);

      await (await conv.submit({ text: "question one" })).wait();
      const settled = await (await conv.submit({ text: "question two" })).wait();
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).reason).toBe("model_error");
      expect((settled.result as Any).error.kind).toBe("context_overflow");
      // No second compaction, no third request.
      expect(fx.provider.requests.map((r) => r.model)).toEqual([
        "fake-1",
        "fake-1",
        SUMMARY_MODEL,
        "fake-1",
      ]);
      const entries = await conv.entries();
      expect(kinds(entries).filter((k) => k === "compaction")).toHaveLength(1);
      expect(kinds(entries).at(-1)).toBe("run_error");
      await w.crash();
    });

    it("a compaction failure at the blocking threshold settles the input unanswered with its error", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", { usage: { inputTokens: 950, outputTokens: 1 } }));
      s.summary(fakeError("overloaded", "summarizer overloaded"));
      const w = await fx.open({ policy: policy() });
      const conv = await setup(w.harness);

      await (await conv.submit({ text: "question one" })).wait();
      const settled = await (await conv.submit({ text: "question two" })).wait();
      expect(settled.status).toBe("unanswered");
      const result = settled.result as Any;
      expect(result.reason).toBe("compaction_failed");
      expect(result.message).toContain("summarizer overloaded");
      expect(result.error.kind).toBe("overloaded");
      // The generation never sent its request.
      expect(genRequests(fx)).toHaveLength(1);
      const entries = await conv.entries();
      expect(kinds(entries)).not.toContain("compaction");
      expect(kinds(entries).at(-1)).toBe("run_error");

      // The conversation recovers: the next input compacts and is answered.
      s.summary(fakeText("SUMMARY-2"));
      s.reply(fakeText("third answer"));
      const third = await (await conv.submit({ text: "question three" })).wait();
      expect((third.result as Any).text).toBe("third answer");
      expect(kinds(await conv.entries())).toContain("compaction");
      await w.crash();
    });

    it("a compaction cut off by a crash is re-run after reopen and the waiting request resumes", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", { usage: { inputTokens: 950, outputTokens: 1 } }));
      s.summary({
        steps: [
          { type: "delay", ms: 60_000 },
          { type: "text", text: "never" },
        ],
      });
      const w1 = await fx.open({ policy: policy() });
      const conv1 = await setup(w1.harness);
      await (await conv1.submit({ text: "question one" })).wait();
      const sub = await conv1.submit({ text: "question two" });
      await waitFor(() => summaryRequests(fx).length === 1);
      await w1.crash();

      s.summary(fakeText("SUMMARY-AFTER-CRASH"));
      s.reply(fakeText("second answer"));
      const w2 = await fx.open({ policy: policy() });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("second answer");
      const entries = await w2.harness.conversation(conv1.id).entries();
      const compactions = entries.filter((e) => e.kind === "compaction");
      expect(compactions.map((e) => payload(e).summary)).toEqual(["SUMMARY-AFTER-CRASH"]);
      expect(summaryRequests(fx)).toHaveLength(2);
      await w2.crash();
    });

    it("does nothing below the thresholds or when disabled", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(
        fakeText("first answer", { usage: { inputTokens: 950, outputTokens: 1 } }),
        fakeText("second answer"),
      );
      const w = await fx.open({ policy: policy() });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "sys", compaction: { ...COMPACTION, enabled: false } },
      });
      await (await conv.submit({ text: "question one" })).wait();
      expect((await (await conv.submit({ text: "question two" })).wait()).status).toBe("done");
      expect(summaryRequests(fx)).toHaveLength(0);
      expect(compactionTasks(w.harness)).toHaveLength(0);
      await w.crash();
    });
  });
}
