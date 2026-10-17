/**
 * The questions suite (U10), run against every storage backend: the native
 * `ask_user` tool parks a durable question and the answer resumes the run.
 */
import { describe, expect, it } from "vitest";
import { askUserTool } from "../../src/agent/ask-user-tool.js";
import { QuestionRejected } from "../../src/agent/questions.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { type AgentBackend, type AgentWorker, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to entry payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

const DAY = 86_400_000;
const AGENT = { systemPrompt: "s", tools: ["ask_user"] };
const OPTIONS = [
  { id: "blue", label: "Blue" },
  { id: "green", label: "Green", description: "the calm one" },
];
const STORED_OPTIONS = [
  { id: "blue", label: "Blue", description: null },
  { id: "green", label: "Green", description: "the calm one" },
];
const ASK = { question: "Which colour?", options: OPTIONS };

const toolTask = (w: AgentWorker) => w.harness.runtime.tasks().find((t) => t.kind === "agent.tool");

async function pendingQuestion(w: AgentWorker, conversationId: string) {
  await waitFor(async () => {
    const items = await w.harness.questions(conversationId);
    return items.some((i) => i.status === "pending");
  });
  await w.harness.waitForIdle();
  const items = await w.harness.questions(conversationId);
  return items.find((i) => i.status === "pending") as (typeof items)[number];
}

async function rejection(p: Promise<unknown>): Promise<QuestionRejected> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(QuestionRejected);
  return err as QuestionRejected;
}

export function defineQuestionsSuite(backend: AgentBackend): void {
  describe(`questions (${backend.name})`, () => {
    it("parks waiting on `answer` with a pending item; a valid option resumes the run with exactly one result", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("Blue it is."));
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "pick a colour" });
      const item = await pendingQuestion(w, conv.id);
      const task = toolTask(w);
      expect(item).toMatchObject({
        toolTaskId: task?.id,
        toolCallId: "c1",
        question: "Which colour?",
        options: STORED_OPTIONS,
        multiSelect: false,
        status: "pending",
        answer: null,
        requestedAt: fx.clock.now(),
        expiresAt: fx.clock.now() + 7 * DAY,
      });
      expect(item.id).toMatch(/^ques_/);
      expect(task?.status).toBe("waiting");
      expect(task?.waiting?.signal).toBe("answer");
      expect(w.harness.runtime.tasks({ status: "running" })).toEqual([]);

      const answered = await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["green"] },
        principal: "user:alice",
      });
      expect(answered.status).toBe("answered");
      expect(answered.answer).toEqual({
        optionIds: ["green"],
        text: null,
        principal: "user:alice",
        answeredAt: fx.clock.now(),
      });

      const settled = await sub.wait();
      expect(settled.status).toBe("done");
      const rs = results(await conv.entries());
      expect(rs).toHaveLength(1);
      expect(rs[0]).toMatchObject({ status: "ok", toolName: "ask_user", isError: false });
      expect(rs[0].content).toBe("Green");
      // The model's next turn contains the answer.
      const second = fx.provider.requests[1];
      expect(JSON.stringify(second?.messages.at(-1))).toContain("Green");
      await w.crash();
    });

    it("free text is returned as given", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("ask_user", { question: "Anything else?" }, { id: "c1" }),
        fakeText("Noted."),
      );
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { text: "use the staging db" },
        principal: "user:alice",
      });
      expect((await sub.wait()).status).toBe("done");
      expect(results(await conv.entries())[0].content).toBe("use the staging db");
      await w.crash();
    });

    it("answering twice rejects the second as already_answered", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["blue"] },
        principal: "user:alice",
      });
      const again = await rejection(
        w.harness.answerQuestion({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          answer: { optionIds: ["green"] },
          principal: "user:mallory",
        }),
      );
      expect(again.reason).toBe("already_answered");
      await sub.wait();
      expect(results(await conv.entries())).toHaveLength(1);
      expect((await w.harness.questions(conv.id))[0]?.answer?.principal).toBe("user:alice");
      await w.crash();
    });

    it("an answer naming another conversation's task, or an unknown task, is not_owned", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const other = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      const wrong = await rejection(
        w.harness.answerQuestion({
          conversationId: other.id,
          toolTaskId: item.toolTaskId,
          answer: { optionIds: ["blue"] },
          principal: "user:eve",
        }),
      );
      expect(wrong.reason).toBe("not_owned");
      const unknown = await rejection(
        w.harness.answerQuestion({
          conversationId: conv.id,
          toolTaskId: "task_unknown",
          answer: { optionIds: ["blue"] },
          principal: "user:eve",
        }),
      );
      expect(unknown.reason).toBe("not_owned");
      expect((await w.harness.questions(conv.id))[0]?.status).toBe("pending");
      expect(await w.harness.questions(other.id)).toEqual([]);
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["blue"] },
        principal: "user:alice",
      });
      await sub.wait();
      await w.crash();
    });

    it("invalid or oversized answers are rejected `invalid` and nothing is committed", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      const base = { conversationId: conv.id, toolTaskId: item.toolTaskId };
      const bad: Any[] = [
        { ...base, answer: {}, principal: "user:a" },
        { ...base, answer: { optionIds: [] }, principal: "user:a" },
        { ...base, answer: { optionIds: ["red"] }, principal: "user:a" },
        { ...base, answer: { optionIds: ["blue", "green"] }, principal: "user:a" },
        { ...base, answer: { optionIds: ["blue", "blue"] }, principal: "user:a" },
        { ...base, answer: { text: "free text not allowed with options" }, principal: "user:a" },
        { ...base, answer: { optionIds: "blue" }, principal: "user:a" },
        { ...base, answer: null, principal: "user:a" },
        { ...base, answer: { optionIds: ["blue"] }, principal: "" },
        { ...base, answer: { optionIds: ["blue"], extra: 1 }, principal: "user:a" },
      ];
      for (const input of bad) {
        expect((await rejection(w.harness.answerQuestion(input))).reason).toBe("invalid");
      }
      // Nothing changed.
      expect((await w.harness.questions(conv.id))[0]?.status).toBe("pending");
      expect(toolTask(w)?.status).toBe("waiting");
      expect(results(await conv.entries())).toEqual([]);

      await w.harness.answerQuestion({
        ...base,
        answer: { optionIds: ["blue"] },
        principal: "user:a",
      });
      await sub.wait();
      await w.crash();
    });

    it("an oversized free-text answer is rejected invalid", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("ask_user", { question: "Why?" }, { id: "c1" }),
        fakeText("ok"),
      );
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      const big = await rejection(
        w.harness.answerQuestion({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          answer: { text: "x".repeat(100_000) },
          principal: "user:a",
        }),
      );
      expect(big.reason).toBe("invalid");
      expect((await w.harness.questions(conv.id))[0]?.status).toBe("pending");
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { text: "short" },
        principal: "user:a",
      });
      await sub.wait();
      await w.crash();
    });

    it("multiSelect accepts several options and returns their labels", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("ask_user", { ...ASK, multiSelect: true }, { id: "c1" }),
        fakeText("ok"),
      );
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["blue", "green"] },
        principal: "user:a",
      });
      await sub.wait();
      expect(results(await conv.entries())[0].content).toBe("Blue, Green");
      await w.crash();
    });

    it("expiry before an answer yields question_expired, the run continues and later answers are rejected", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("Moving on."));
      const w = await fx.open({ tools: [askUserTool], questionTtlMs: 30 * 60_000 });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      expect(item.expiresAt).toBe(item.requestedAt + 30 * 60_000);
      expect(toolTask(w)?.waiting?.until).toBe(item.expiresAt);

      await fx.clock.advanceAsync(31 * 60_000);
      const settled = await sub.wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("Moving on.");
      const [result] = results(await conv.entries());
      expect(result.status).toBe("question_expired");
      expect(result.isError).toBe(true);
      expect((await w.harness.questions(conv.id))[0]?.status).toBe("expired");
      const late = await rejection(
        w.harness.answerQuestion({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          answer: { optionIds: ["blue"] },
          principal: "user:alice",
        }),
      );
      expect(late.reason).toBe("expired");
      await w.crash();
    });

    it("questionTtlMs 0 means no expiry", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [askUserTool], questionTtlMs: 0 });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      expect(item.expiresAt).toBeNull();
      expect(toolTask(w)?.waiting?.until ?? null).toBeNull();
      await w.crash();
    });

    it("restart while waiting: the item is still pending and answering works", async () => {
      const fx = await backend.fixture();
      const options = () => ({ tools: [askUserTool] });
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }));
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w1, conv.id);
      await w1.crash();

      fx.clock.advance(2 * DAY);
      fx.provider.enqueue(fakeText("Thanks."));
      const w2 = await fx.open(options());
      await w2.harness.waitForIdle();
      expect(w2.harness.runtime.tasks({ status: "running" })).toEqual([]);
      expect((await w2.harness.questions(conv.id))[0]?.status).toBe("pending");
      await w2.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["blue"] },
        principal: "user:alice",
      });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      const rs = results(await w2.harness.conversation(conv.id).entries());
      expect(rs).toHaveLength(1);
      expect(rs[0].content).toBe("Blue");
      await w2.crash();
    });

    it("aborting the run while waiting closes the item cancelled", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }));
      const w = await fx.open({ tools: [askUserTool] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      await w.harness.runtime.abortConversation(conv.id, "user");
      await sub.wait();
      await w.harness.waitForIdle();
      expect((await w.harness.questions(conv.id))[0]?.status).toBe("cancelled");
      const late = await rejection(
        w.harness.answerQuestion({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          answer: { optionIds: ["blue"] },
          principal: "user:alice",
        }),
      );
      expect(late.reason).toBe("already_answered");
      await w.crash();
    });

    it("a question call goes through authorizeTool first; a block means no question is created", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const seen: string[] = [];
      const w = await fx.open({
        tools: [askUserTool],
        policy: {
          resolveModelRequest: (await import("./support.js")).allowAll.resolveModelRequest,
          authorizeTool: (req) => {
            seen.push(req.toolName);
            return { decision: "block", reason: "nope" };
          },
        },
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "go" })).wait();
      expect(seen).toEqual(["ask_user"]);
      expect(await w.harness.questions(conv.id)).toEqual([]);
      expect(results(await conv.entries())[0].status).toBe("blocked");
      await w.crash();
    });

    it("an answered ask_user does not taint the conversation", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "c1" }), fakeText("ok"));
      const taints: boolean[] = [];
      const w = await fx.open({
        tools: [askUserTool],
        policy: {
          resolveModelRequest: (await import("./support.js")).allowAll.resolveModelRequest,
          authorizeTool: (req) => {
            taints.push(req.taint.tainted);
            return { decision: "allow" };
          },
        },
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const item = await pendingQuestion(w, conv.id);
      await w.harness.answerQuestion({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        answer: { optionIds: ["blue"] },
        principal: "user:a",
      });
      await sub.wait();
      expect(taints).toEqual([false]);
      await w.crash();
    });
  });
}
