/**
 * Structured delta compaction (U14), run against every storage backend: the
 * sectioned prompt, repair from the previous summary, the archive object, the
 * re-attached todo, and the unchanged plain path.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  COMPACTION_SYSTEM_PROMPT,
  parseSummarySections,
  STRUCTURED_COMPACTION_PROMPT,
  STRUCTURED_HEADINGS,
} from "../../src/agent/compaction.js";
import type { Conversation } from "../../src/agent/conversation.js";
import type { Harness } from "../../src/agent/harness.js";
import type { AfterCompactionEvent } from "../../src/agent/hooks.js";
import type { OffloadPutRequest, OffloadStore } from "../../src/agent/offload.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import { LeaseLost } from "../../src/core/errors.js";
import { type FakeScript, fakeError, fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, type AgentFixture, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const payload = (e: EntryRecord): Any => e.payload as Any;

const SUMMARY_MODEL = "fake-summary";
const OVER = { usage: { inputTokens: 950, outputTokens: 1 } };

const COMPACTION = {
  contextWindow: 1000,
  reserveTokens: 100,
  backgroundMargin: 200,
  keepRecentTokens: 50,
};

const policy: HostPolicy = {
  resolveModelRequest: (info) => ({
    ok: true,
    provider: "fake",
    model: info.purpose === "compaction" ? SUMMARY_MODEL : "fake-1",
    apiKey: "test-key",
    maxTokens: 1024,
  }),
  authorizeTool: () => ({ decision: "allow" }),
};

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

const summaryRequests = (fx: AgentFixture) =>
  fx.provider.requests.filter((r) => r.model === SUMMARY_MODEL);

/** An in-memory {@link OffloadStore}: idempotent by `runId:toolCallId`, like the real one. */
function memoryStore(failWith?: () => Error) {
  const objects = new Map<string, { path: string; text: string }>();
  const puts: OffloadPutRequest[] = [];
  const store: OffloadStore = {
    async put(req) {
      puts.push(req);
      if (failWith) throw failWith();
      const key = `${req.runId}:${req.toolCallId}`;
      let o = objects.get(key);
      if (!o) {
        const dir = req.kind === "history" ? "conversation_history" : "tool_results";
        o = {
          path: `${dir}/${req.toolCallId}.${req.kind === "history" ? "md" : "txt"}`,
          text: req.text,
        };
        objects.set(key, o);
      }
      return { path: o.path, bytes: Buffer.byteLength(o.text, "utf8") };
    },
  };
  return { store, objects, puts };
}

const todoTool = defineTool({
  name: "update_todo",
  description: "Update the todo list",
  parameters: z.object({
    add: z.array(z.string()).optional(),
    completed: z.array(z.string()).optional(),
  }),
  replay: "safe",
  execute: async () => ({ content: "ok" }),
});

async function setup(
  h: Harness,
  compaction: Record<string, unknown>,
  tools: string[] = [],
): Promise<Conversation> {
  return h.createConversation({
    tenantId: "t1",
    projectId: "p1",
    agent: { systemPrompt: "sys", tools, compaction: { ...COMPACTION, ...compaction } },
  });
}

const sections = (over: Record<string, string> = {}) =>
  STRUCTURED_HEADINGS.map((h) => `## ${h}\n${over[h] ?? `body of ${h}`}`).join("\n\n");

/** Run one turn that ends over the blocking threshold, then a second that compacts. */
async function twoTurns(conv: Conversation, first = "question one", second = "question two") {
  await (await conv.submit({ text: first })).wait();
  return (await conv.submit({ text: second })).wait();
}

export function defineStructuredCompactionSuite(backend: AgentBackend): void {
  describe(`structured compaction (${backend.name})`, () => {
    it("uses the sectioned prompt, gives previous sections apart, and accepts a complete summary", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", OVER), fakeText("second answer", OVER), fakeText("third"));
      s.summary(fakeText(sections()), fakeText(sections({ Decisions: "D2" })));
      const w = await fx.open({ policy });
      const conv = await setup(w.harness, { structuredSummary: true });

      expect((await twoTurns(conv)).status).toBe("done");
      const first = summaryRequests(fx)[0] as ModelRequest;
      expect(first.messages[0]).toEqual({ role: "system", text: STRUCTURED_COMPACTION_PROMPT });
      expect(JSON.stringify(first.messages[1])).not.toContain("previous_summary_sections");

      // A second compaction sees the first one's sections apart from the transcript.
      expect((await (await conv.submit({ text: "question three" })).wait()).status).toBe("done");
      const second = summaryRequests(fx)[1] as ModelRequest;
      const user = (second.messages[1] as { content: string }).content;
      expect(user.indexOf("<previous_summary_sections>")).toBeGreaterThanOrEqual(0);
      expect(user.indexOf("<previous_summary_sections>")).toBeLessThan(
        user.indexOf("<conversation>"),
      );

      const compactions = (await conv.entries()).filter((e) => e.kind === "compaction");
      expect(compactions.length).toBe(2);
      expect(payload(compactions[0] as EntryRecord).summary).toBe(sections());
      expect(
        parseSummarySections(payload(compactions[1] as EntryRecord).summary).get("Decisions"),
      ).toBe("D2");
      await w.crash();
    });

    it("repairs missing sections from the previous summary and reports the quality", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const events: AfterCompactionEvent[] = [];
      const reports: Array<{ during: string }> = [];
      s.reply(fakeText("first answer", OVER), fakeText("second answer", OVER), fakeText("third"));
      s.summary(
        fakeText(sections({ "Goal and constraints": "GOAL-1", Decisions: "KEEP-ME" })),
        fakeText("## Facts and IDs\nonly facts"),
      );
      const w = await fx.open({
        policy,
        hooks: { afterCompaction: (e) => void events.push(e) },
        onError: (_e, info) => void reports.push(info),
      });
      const conv = await setup(w.harness, { structuredSummary: true });
      await twoTurns(conv);
      await (await conv.submit({ text: "question three" })).wait();

      const compactions = (await conv.entries()).filter((e) => e.kind === "compaction");
      const repaired = parseSummarySections(payload(compactions[1] as EntryRecord).summary);
      expect(repaired.get("Goal and constraints")).toBe("GOAL-1");
      expect(repaired.get("Decisions")).toBe("KEEP-ME");
      expect(repaired.get("Facts and IDs")).toBe("only facts");

      expect(events[0]?.quality?.missingHeadings).toEqual([]);
      expect(events[1]?.quality?.missingHeadings).toContain("Decisions");
      expect(events[1]?.quality?.summarizedChars).toBeGreaterThan(0);
      expect(reports.filter((r) => r.during === "compaction_quality")).toHaveLength(1);
      await w.crash();
    });

    it("keeps the goal section byte-for-byte over three compactions", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const goal = "- user: answer in French\n- user: never touch prod";
      s.reply(fakeText("a1", OVER), fakeText("a2", OVER), fakeText("a3", OVER), fakeText("a4"));
      s.summary(
        fakeText(sections({ "Goal and constraints": goal })),
        fakeText(sections({ "Goal and constraints": "reworded goal" })),
        fakeText(sections({ "Goal and constraints": "reworded again" })),
      );
      const w = await fx.open({ policy });
      const conv = await setup(w.harness, { structuredSummary: true });
      await twoTurns(conv);
      await (await conv.submit({ text: "question three" })).wait();
      await (await conv.submit({ text: "question four" })).wait();

      const compactions = (await conv.entries()).filter((e) => e.kind === "compaction");
      expect(compactions).toHaveLength(3);
      for (const c of compactions) {
        const g = parseSummarySections(payload(c).summary).get("Goal and constraints") as string;
        expect(g.startsWith(goal)).toBe(true);
      }
      await w.crash();
    });

    it("archives the summarized range, names the path in the summary and re-attaches the open todo", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const mem = memoryStore();
      s.reply(
        fakeToolCall("update_todo", { add: ["write tests", "ship"] }, { id: "c1" }),
        fakeText("first answer", OVER),
        fakeText("second answer"),
      );
      s.summary(fakeText(sections()));
      const w = await fx.open({ policy, tools: [todoTool], offload: mem.store });
      const conv = await setup(w.harness, { structuredSummary: true, archiveSummarized: true }, [
        "update_todo",
      ]);
      expect((await twoTurns(conv)).status).toBe("done");

      const [compaction] = (await conv.entries()).filter((e) => e.kind === "compaction");
      const summary = payload(compaction as EntryRecord).summary as string;
      expect(mem.puts).toHaveLength(1);
      const put = mem.puts[0] as OffloadPutRequest;
      expect(put.kind).toBe("history");
      expect(put.toolCallId).toBe(compaction?.taskId);
      const path = `conversation_history/${put.toolCallId}.md`;
      expect(summary).toContain(`Full earlier transcript: ${path}`);
      expect(summary).toContain("Open todo (as of the summary):\n- [ ] write tests\n- [ ] ship");
      // The archive holds the summarized entries, including the unclipped tool call.
      expect(put.text).toContain("question one");
      expect(put.text).toContain("[assistant called tool update_todo]");
      expect(put.text).toContain("first answer");
      await w.crash();
    });

    it("with no store there is no archive line and nothing fails; with no todo no todo block", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", OVER), fakeText("second answer"));
      s.summary(fakeText(sections()));
      const w = await fx.open({ policy });
      const conv = await setup(w.harness, { structuredSummary: true, archiveSummarized: true });
      expect((await twoTurns(conv)).status).toBe("done");
      const [compaction] = (await conv.entries()).filter((e) => e.kind === "compaction");
      expect(payload(compaction as EntryRecord).summary).toBe(sections());
      await w.crash();
    });

    it("a failing archive store is reported and the summary is still placed", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const mem = memoryStore(() => new Error("store down"));
      const reports: Array<{ during: string }> = [];
      s.reply(fakeText("first answer", OVER), fakeText("second answer"));
      s.summary(fakeText(sections()));
      const w = await fx.open({
        policy,
        offload: mem.store,
        onError: (_e, info) => void reports.push(info),
      });
      const conv = await setup(w.harness, { structuredSummary: true, archiveSummarized: true });
      expect((await twoTurns(conv)).status).toBe("done");
      const [compaction] = (await conv.entries()).filter((e) => e.kind === "compaction");
      expect(payload(compaction as EntryRecord).summary).toBe(sections());
      expect(reports.some((r) => r.during === "offload")).toBe(true);
      await w.crash();
    });

    it("a lost lease while archiving is not swallowed", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const mem = memoryStore(() => new LeaseLost("sess", 1));
      s.reply(fakeText("first answer", OVER));
      s.summary(fakeText(sections()));
      const w = await fx.open({ policy, offload: mem.store });
      const conv = await setup(w.harness, { structuredSummary: true, archiveSummarized: true });
      await (await conv.submit({ text: "question one" })).wait();
      await conv.submit({ text: "question two" });
      await waitFor(() => mem.puts.length > 0);
      expect((await conv.entries()).some((e) => e.kind === "compaction")).toBe(false);
      await w.crash();
    });

    it("the plain prompt stays the default and the old path adds nothing to the summary", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      const events: AfterCompactionEvent[] = [];
      s.reply(fakeToolCall("update_todo", { add: ["x"] }, { id: "c1" }));
      s.reply(fakeText("first answer", OVER), fakeText("second answer"));
      s.summary(fakeText("PLAIN SUMMARY"));
      const w = await fx.open({
        policy,
        tools: [todoTool],
        hooks: { afterCompaction: (e) => void events.push(e) },
      });
      const conv = await setup(w.harness, {}, ["update_todo"]);
      expect((await twoTurns(conv)).status).toBe("done");
      const req = summaryRequests(fx)[0] as ModelRequest;
      expect(req.messages[0]).toEqual({ role: "system", text: COMPACTION_SYSTEM_PROMPT });
      expect((req.messages[1] as { content: string }).content).toContain(
        "<previous_summary>".slice(0, 0),
      );
      expect(
        (req.messages[1] as { content: string }).content.endsWith(
          "Summarize the conversation above.",
        ),
      ).toBe(true);
      const [compaction] = (await conv.entries()).filter((e) => e.kind === "compaction");
      expect(payload(compaction as EntryRecord).summary).toBe("PLAIN SUMMARY");
      expect(events[0]).not.toHaveProperty("quality");
      await w.crash();
    });

    it("a failed structured summary request settles compaction_failed as before", async () => {
      const fx = await backend.fixture();
      const s = scripts(fx);
      s.reply(fakeText("first answer", OVER));
      s.summary(fakeError("overloaded", "summarizer overloaded"));
      const w = await fx.open({ policy });
      const conv = await setup(w.harness, { structuredSummary: true, archiveSummarized: true });
      const settled = await twoTurns(conv);
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).reason).toBe("compaction_failed");
      expect((await conv.entries()).some((e) => e.kind === "compaction")).toBe(false);
      await w.crash();
    });
  });
}
