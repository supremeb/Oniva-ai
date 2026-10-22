/**
 * Typed subagent results (U19, R16, KTD13), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { agentResultDoc, createCompleteTaskTool } from "../../src/agent/complete-task.js";
import { agentConfigDoc } from "../../src/agent/prompt.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import type { JsonObject } from "../../src/core/json.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { type AgentBackend, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

const AGENT = { systemPrompt: "parent prompt", tools: ["research"], metadata: {} };
const SCHEMA: JsonObject = {
  type: "object",
  required: ["verdict", "score"],
  properties: { verdict: { type: "string", enum: ["pass", "fail"] }, score: { type: "integer" } },
};
const GOOD = { verdict: "pass", score: 3 };

const typed = (schema: JsonObject | null = SCHEMA) =>
  createSubagentTool({
    name: "research",
    systemPrompt: "child prompt",
    tools: [],
    ...(schema === null ? {} : { outputSchema: schema }),
  });

export function defineCompleteTaskSuite(backend: AgentBackend): void {
  describe(`typed subagent results (${backend.name})`, () => {
    const setup = async (research = typed()) => {
      const fx = await backend.fixture();
      const w = await fx.open({ tools: [research, createCompleteTaskTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      return { fx, w, conv };
    };
    const childSubs = async (w: Any, childId: string) =>
      (await w.harness.session.reader.listSubmissions(childId, { limit: 10 })).items;

    it("a valid result reaches the parent exactly; the child ends via terminate with no extra round", async () => {
      const { fx, w, conv } = await setup();
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeToolCall("complete_task", { result: GOOD }, { id: "k1" }),
        fakeText("parent done"),
      );
      const settled = await (await conv.submit({ text: "go" })).wait();
      expect(settled.status).toBe("done");
      const [r] = results(await conv.entries());
      expect(r.status).toBe("ok");
      expect(JSON.parse(r.content)).toEqual(GOOD);
      expect(r.details.result).toEqual(GOOD);
      expect(r.details.nudged).toBeUndefined();
      expect(fx.provider.requests).toHaveLength(3);

      const childId = r.details.conversationId as string;
      const childAgent = await w.harness.session.document(agentConfigDoc, childId);
      expect(childAgent.tools).toEqual(["complete_task"]);
      expect(childAgent.metadata.outputSchema).toEqual(SCHEMA);
      expect(childAgent.systemPrompt).toContain("child prompt");
      expect(childAgent.systemPrompt).toContain("complete_task");
      expect(childAgent.systemPrompt).toContain(JSON.stringify(SCHEMA));
      expect(await w.harness.session.document(agentResultDoc, childId)).toEqual({ result: GOOD });
      const subs = await childSubs(w, childId);
      expect(subs).toHaveLength(1);
      expect(((subs[0] as Any).result as Any).terminated).toBe(true);
      // The parent's own config is untouched.
      expect((await w.harness.session.document(agentConfigDoc, conv.id)).metadata).toEqual({});
      await w.crash();
    });

    it("an invalid result returns violations to the child, which retries successfully", async () => {
      const { fx, w, conv } = await setup();
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeToolCall("complete_task", { result: { verdict: "maybe" } }, { id: "k1" }),
        fakeToolCall("complete_task", { result: GOOD }, { id: "k2" }),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r.details.result).toEqual(GOOD);
      const childId = r.details.conversationId as string;
      const childResults = results(await w.harness.conversation(childId).entries());
      expect(childResults).toHaveLength(2);
      expect(childResults[0]).toMatchObject({ status: "error", isError: true, terminate: false });
      expect(childResults[0].content).toContain("must be one of");
      expect(childResults[0].content).toContain('missing required property "score"');
      expect(childResults[1]).toMatchObject({ status: "ok", terminate: true });
      await w.crash();
    });

    it("text only: exactly one follow-up is submitted and its result is used", async () => {
      const { fx, w, conv } = await setup();
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeText("it passes, score 3"),
        fakeToolCall("complete_task", { result: GOOD }, { id: "k1" }),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r.status).toBe("ok");
      expect(r.details).toMatchObject({ result: GOOD, nudged: true });
      const childId = r.details.conversationId as string;
      const subs = await childSubs(w, childId);
      expect(subs).toHaveLength(2);
      const toolTask = w.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t: Any) => t.kind === "agent.tool") as Any;
      expect(subs.map((s: Any) => s.requestId)).toEqual([
        `subagent:${toolTask.id}`,
        `subagent:${toolTask.id}:complete`,
      ]);
      expect(subs[1]?.input).toMatchObject({ whenBusy: "followUp" });
      expect(toolTask.checkpoint.nudge.submissionId).toBe(subs[1]?.id);
      await w.crash();
    });

    it("text only twice: an error result with the raw text", async () => {
      const { fx, w, conv } = await setup();
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeText("first prose"),
        fakeText("second prose"),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r).toMatchObject({ status: "error", isError: true });
      expect(r.details).toMatchObject({
        rawText: "second prose",
        nudged: true,
        reason: "no_result",
      });
      const childId = r.details.conversationId as string;
      expect(await childSubs(w, childId)).toHaveLength(2);
      await w.crash();
    });

    it("a per-run outputSchema override in the parent's metadata types the child", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ tools: [typed(null), createCompleteTaskTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { ...AGENT, metadata: { subagents: { research: { outputSchema: SCHEMA } } } },
      });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeToolCall("complete_task", { result: GOOD }, { id: "k1" }),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      expect(results(await conv.entries())[0].details.result).toEqual(GOOD);
      await w.crash();
    });

    it("an invalid override schema fails the call and creates no child", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ tools: [typed(null), createCompleteTaskTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { ...AGENT, metadata: { subagents: { research: { outputSchema: { $ref: "x" } } } } },
      });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r.status).toBe("error");
      expect(r.content).toContain("unsupported keyword");
      expect((await w.harness.session.reader.listConversations({ limit: 10 })).items).toHaveLength(
        1,
      );
      await w.crash();
    });

    it("an unsupported keyword in the tool's outputSchema is rejected at spec time", () => {
      expect(() => typed({ type: "object", additionalProperties: false })).toThrow(
        /unsupported keyword/,
      );
    });

    it("without outputSchema nothing changes: text answer, no complete_task, no follow-up", async () => {
      const { fx, w, conv } = await setup(typed(null));
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeText("plain answer"),
        fakeText("parent done"),
      );
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r).toMatchObject({ status: "ok", content: "plain answer" });
      expect(Object.keys(r.details).sort()).toEqual(
        ["conversationId", "depth", "status", "submissionId"].sort(),
      );
      const childId = r.details.conversationId as string;
      const childAgent = await w.harness.session.document(agentConfigDoc, childId);
      expect(childAgent.tools).toEqual([]);
      expect(childAgent.systemPrompt).toBe("child prompt");
      expect(childAgent.metadata).toEqual({});
      expect(await childSubs(w, childId)).toHaveLength(1);
      expect(await w.harness.session.document(agentResultDoc, childId)).toBeNull();
      await w.crash();
    });

    it("complete_task in an untyped conversation is a tool error", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ tools: [createCompleteTaskTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { ...AGENT, tools: ["complete_task"] },
      });
      fx.provider.enqueue(
        fakeToolCall("complete_task", { result: 1 }, { id: "k1" }),
        fakeText("ok"),
      );
      await (await conv.submit({ text: "go" })).wait();
      expect(results(await conv.entries())[0]).toMatchObject({ status: "error", isError: true });
      await w.crash();
    });

    it("a crash while the parent waits on the follow-up resumes and uses the result", async () => {
      const { fx, w, conv } = await setup();
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "judge" }, { id: "c1" }),
        fakeText("prose"),
        // The follow-up round hangs on the manual clock until the crash.
        { steps: [{ type: "delay", ms: 3_600_000 }] },
      );
      const sub = await conv.submit({ text: "go" });
      const toolTask = () =>
        w.harness.runtime
          .tasks({ conversationId: conv.id })
          .find((t) => t.kind === "agent.tool") as Any;
      await waitFor(
        () => toolTask()?.checkpoint?.nudge !== undefined && toolTask().status === "waiting",
      );
      // Wait until the follow-up round has actually pulled the hanging step:
      // crashing earlier leaves it queued for the next worker, which would
      // then hang on it instead of using the complete_task call below.
      await waitFor(() => fx.provider.requests.length === 3);
      const nudgeId = toolTask().checkpoint.nudge.submissionId as string;
      const childId = toolTask().checkpoint.subagent.conversationId as string;
      await w.crash();

      fx.provider.enqueue(
        fakeToolCall("complete_task", { result: GOOD }, { id: "k1" }),
        fakeText("parent done"),
      );
      const w2 = await fx.open({ tools: [typed(), createCompleteTaskTool()] });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      const [r] = results(await w2.harness.conversation(conv.id).entries());
      expect(r.details).toMatchObject({ result: GOOD, nudged: true, conversationId: childId });
      const subs = await childSubs(w2, childId);
      expect(subs.map((s: Any) => s.id)).toContain(nudgeId);
      expect(subs).toHaveLength(2);
      expect(
        w2.harness.runtime
          .tasks({ conversationId: conv.id })
          .filter((t) => t.kind === "agent.tool"),
      ).toHaveLength(1);
      await w2.crash();
    });
  });
}
