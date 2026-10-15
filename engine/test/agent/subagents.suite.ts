/**
 * Subagents (U10, R13, AE5), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { buildContext } from "../../src/agent/context-builder.js";
import type { Harness } from "../../src/agent/harness.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import { agentConfigDoc } from "../../src/agent/prompt.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord, TaskRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, allowAll, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

const AGENT = { systemPrompt: "parent prompt", tools: ["research"], metadata: { team: "a" } };

/** A replay-safe tool that blocks until aborted (or the worker stops). */
const blockTool = () =>
  defineTool({
    name: "wait",
    description: "Waits",
    parameters: z.object({}),
    replay: "safe",
    execute: async (_a, api) => untilAborted(api.signal),
  });

const quickWait = () =>
  defineTool({
    name: "wait",
    description: "Waits",
    parameters: z.object({}),
    replay: "safe",
    execute: async () => ({ content: "waited" }),
  });

async function allConversations(h: Harness): Promise<string[]> {
  const page = await h.session.reader.listConversations({ limit: 100 });
  return page.items.map((c) => c.id);
}

function childOf(h: Harness, parentId: string): string | undefined {
  const tool = h.runtime
    .tasks({ conversationId: parentId })
    .find((t) => t.kind === "agent.tool" && t.input && (t.input as Any).call.name === "research");
  return (tool?.checkpoint as Any)?.subagent?.conversationId;
}

export function defineSubagentSuite(backend: AgentBackend): void {
  describe(`subagents (${backend.name})`, () => {
    it("the parent calls the subagent, the child answers and the parent receives the answer", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({
        name: "research",
        description: "Research a question",
        systemPrompt: "child prompt",
        tools: ["wait"],
        model: "small",
      });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "find x" }, { id: "c1" }),
        fakeText("x is 42"),
        fakeText("The answer is 42."),
      );
      const w = await fx.open({ tools: [research, quickWait()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const settled = await (await conv.submit({ text: "what is x?" })).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("The answer is 42.");

      const [result] = results(await conv.entries());
      expect(result.status).toBe("ok");
      expect(result.content).toBe("x is 42");
      const childId = result.details.conversationId as string;
      expect(typeof childId).toBe("string");
      expect(childId).not.toBe(conv.id);

      const toolTask = w.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t) => t.kind === "agent.tool") as TaskRecord;
      const child = await w.harness.conversation(childId).info();
      expect(child).toMatchObject({ tenantId: "t1", projectId: "p1", ownerTaskId: toolTask.id });
      expect(await w.harness.session.document(agentConfigDoc, childId)).toMatchObject({
        systemPrompt: "child prompt",
        tools: ["wait"],
        model: "small",
        thinking: null,
        depth: 1,
        metadata: { team: "a" },
      });
      const subs = await w.harness.session.reader.listSubmissions(childId, { limit: 10 });
      expect(subs.items).toHaveLength(1);
      expect(subs.items[0]).toMatchObject({
        requestId: `subagent:${toolTask.id}`,
        status: "done",
      });
      expect(result.details.submissionId).toBe(subs.items[0]?.id);
      // The child saw its own prompt and the delegated task.
      const childRequest = fx.provider.requests[1] as Any;
      expect(childRequest.messages[0]).toEqual({ role: "system", text: "child prompt" });
      expect(childRequest.messages[1]).toEqual({ role: "user", content: "find x" });
      // The parent saw the child's answer as the tool result.
      const last = fx.provider.requests[2] as Any;
      expect(last.messages.at(-1)).toMatchObject({ role: "tool_result", content: "x is 42" });
      await w.harness.waitForIdle();
      expect(w.harness.runtime.tasks().every((t) => t.status === "terminal")).toBe(true);
      await w.crash();
    });

    it("AE5: aborting the parent mid-child-run aborts every child task before the parent is idle", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeToolCall("wait", {}, { id: "k1" }),
      );
      const w = await fx.open({ tools: [research, blockTool()] });
      const order: Array<{ id: string; conversationId: string; outcome: string }> = [];
      const stop = w.harness.session.subscribeCommits((event) => {
        for (const t of event.batch.replaceTasks ?? []) {
          if (t.status === "terminal") {
            order.push({
              id: t.id,
              conversationId: t.conversationId,
              outcome: t.outcome?.type ?? "",
            });
          }
        }
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(() =>
        w.harness.runtime
          .tasks()
          .some(
            (t) =>
              t.kind === "agent.tool" &&
              (t.input as Any).call.name === "wait" &&
              t.phase === "execute",
          ),
      );
      const childId = childOf(w.harness, conv.id) as string;
      expect(childId).toBeDefined();

      await conv.abort("user_abort");
      // The parent reports idle: everything of the child is already terminal and aborted.
      expect(conv.busy()).toBe(false);
      const childTasks = w.harness.runtime.tasks({ conversationId: childId });
      expect(childTasks.length).toBeGreaterThanOrEqual(2);
      for (const t of childTasks) {
        expect(t.status).toBe("terminal");
        expect(t.outcome?.type).toBe("aborted");
      }
      const parentTasks = w.harness.runtime.tasks({ conversationId: conv.id });
      expect(parentTasks.every((t) => t.status === "terminal")).toBe(true);
      // Bottom-up: the child's tasks became terminal before any parent task.
      const lastChild = Math.max(
        ...order.flatMap((o, i) => (o.conversationId === childId ? [i] : [])),
      );
      const firstParent = Math.min(
        ...order.flatMap((o, i) => (o.conversationId === conv.id ? [i] : [])),
      );
      expect(lastChild).toBeLessThan(firstParent);
      expect((await sub.wait()).status).toBe("unanswered");
      // The child's input is settled too.
      await waitFor(async () => {
        const subs = await w.harness.session.reader.listSubmissions(childId, { limit: 10 });
        return subs.items[0]?.status === "unanswered";
      });
      stop();
      await w.crash();
    });

    it("AE5: aborting the parent's run through the runtime cascades the same way", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeToolCall("wait", {}, { id: "k1" }),
      );
      const w = await fx.open({ tools: [research, blockTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(() =>
        w.harness.runtime
          .tasks()
          .some((t) => (t.input as Any)?.call?.name === "wait" && t.phase === "execute"),
      );
      const childId = childOf(w.harness, conv.id) as string;
      const root = w.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t) => t.kind === "agent.generation") as TaskRecord;
      await w.harness.runtime.abort(root.id, "owner_abort");
      const settled = await sub.wait();
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).reason).toBe("aborted");
      for (const t of w.harness.runtime.tasks({ conversationId: childId })) {
        expect(t.status).toBe("terminal");
        expect(t.outcome).toMatchObject({ type: "aborted" });
      }
      await w.crash();
    });

    it("a crash during the child run followed by a reopen reuses the same child and submission", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeToolCall("wait", {}, { id: "k1" }),
      );
      const w1 = await fx.open({ tools: [research, blockTool()] });
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(() =>
        w1.harness.runtime
          .tasks()
          .some((t) => (t.input as Any)?.call?.name === "wait" && t.phase === "execute"),
      );
      const childId = childOf(w1.harness, conv.id) as string;
      const before = await w1.harness.session.reader.listSubmissions(childId, { limit: 10 });
      expect(before.items).toHaveLength(1);
      const parentTool = w1.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t) => t.kind === "agent.tool") as TaskRecord;
      expect(parentTool.status).toBe("waiting");
      await w1.crash();

      fx.provider.enqueue(fakeText("dug"), fakeText("parent done"));
      const w2 = await fx.open({ tools: [research, quickWait()] });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("parent done");
      expect(await allConversations(w2.harness)).toHaveLength(2);
      const after = await w2.harness.session.reader.listSubmissions(childId, { limit: 10 });
      expect(after.items.map((s) => s.id)).toEqual(before.items.map((s) => s.id));
      expect(after.items[0]?.status).toBe("done");
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      expect(result).toMatchObject({ status: "ok", content: "dug" });
      expect(result.details.conversationId).toBe(childId);
      expect(
        w2.harness.runtime
          .tasks({ conversationId: conv.id })
          .filter((t) => t.kind === "agent.tool"),
      ).toHaveLength(1);
      // The restart cut a child tool call off: the root (not the child) gets
      // exactly one display-only marker, which the model never sees.
      const rootEntries = await w2.harness.conversation(conv.id).entries();
      const markers = rootEntries.filter((e) => e.kind === "run_resumed");
      expect(markers).toHaveLength(1);
      const runId = w2.harness.runtime
        .tasks({ conversationId: conv.id })
        .find((t) => t.kind === "agent.generation" && t.parentTaskId === null)?.id;
      expect(markers[0]?.payload).toMatchObject({ runId });
      expect(
        (await w2.harness.conversation(childId).entries()).some((e) => e.kind === "run_resumed"),
      ).toBe(false);
      const ctx = buildContext(rootEntries);
      expect(JSON.stringify(ctx.messages)).not.toContain("run_resumed");
      expect(ctx.messages).toEqual(
        buildContext(rootEntries.filter((e) => e.kind !== "run_resumed")).messages,
      );
      await w2.crash();
    });

    it("a second crash and reopen mid-run adds one more marker; a clean run adds none", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["wait"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "dig" }, { id: "c1" }),
        fakeToolCall("wait", {}, { id: "k1" }),
      );
      const w1 = await fx.open({ tools: [research, blockTool()] });
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      const inWait = (w: typeof w1) =>
        w.harness.runtime
          .tasks()
          .some((t) => (t.input as Any)?.call?.name === "wait" && t.phase === "execute");
      await waitFor(() => inWait(w1));
      await w1.crash();
      const w2 = await fx.open({ tools: [research, blockTool()] });
      await waitFor(() => inWait(w2));
      await w2.crash();
      fx.provider.enqueue(fakeText("dug"), fakeText("parent done"));
      const w3 = await fx.open({ tools: [research, quickWait()] });
      expect((await w3.harness.submission(sub.id).wait()).status).toBe("done");
      const entries = await w3.harness.conversation(conv.id).entries();
      expect(entries.filter((e) => e.kind === "run_resumed")).toHaveLength(2);
      await w3.crash();

      const fx2 = await backend.fixture();
      fx2.provider.enqueue(fakeText("hi"));
      const c = await fx2.open({ tools: [research, quickWait()] });
      const conv2 = await c.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv2.submit({ text: "hello" })).wait();
      expect(
        (await c.harness.conversation(conv2.id).entries()).some((e) => e.kind === "run_resumed"),
      ).toBe(false);
      await c.crash();
    });

    it("a child waiting on an approval leaves zero running tasks in the session", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["deploy"] });
      const log: string[] = [];
      const deploy = defineTool({
        name: "deploy",
        description: "Deploy",
        parameters: z.object({ service: z.string() }),
        execute: async (args) => {
          log.push(args.service);
          return { content: `deployed ${args.service}` };
        },
      });
      const policy: HostPolicy = {
        resolveModelRequest: allowAll.resolveModelRequest,
        authorizeTool: (req) =>
          req.toolName === "deploy"
            ? { decision: "requireApproval", reason: "needs a human" }
            : { decision: "allow" },
      };
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "deploy api" }, { id: "c1" }),
        fakeToolCall("deploy", { service: "api" }, { id: "d1" }),
      );
      const options = () => ({ tools: [research, deploy], policy });
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "ship it" });
      let childId = "";
      await waitFor(async () => {
        childId = childOf(w1.harness, conv.id) ?? "";
        if (!childId) return false;
        return (await w1.harness.approvals(childId)).some((i) => i.status === "pending");
      });
      await w1.harness.waitForIdle();
      expect(w1.harness.runtime.tasks({ status: "running" })).toEqual([]);
      expect(w1.harness.runtime.tasks().filter((t) => t.status === "waiting").length).toBe(4);
      await w1.crash();

      fx.provider.enqueue(fakeText("api deployed"), fakeText("shipped"));
      const w2 = await fx.open(options());
      await w2.harness.waitForIdle();
      expect(w2.harness.runtime.tasks({ status: "running" })).toEqual([]);
      const [item] = await w2.harness.approvals(childId);
      await w2.harness.decideApproval({
        conversationId: childId,
        toolTaskId: item?.toolTaskId as string,
        decision: "approve",
        principal: "user:alice",
      });
      const settled = await w2.harness.submission(sub.id).wait();
      expect((settled.result as Any).text).toBe("shipped");
      expect(log).toEqual(["api"]);
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      expect(result).toMatchObject({ status: "ok", content: "api deployed" });
      await w2.crash();
    });

    it("a subagent call that needs approval spawns its child only once approved", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: [] });
      const policy: HostPolicy = {
        resolveModelRequest: allowAll.resolveModelRequest,
        authorizeTool: () => ({ decision: "requireApproval", reason: "delegation" }),
      };
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "look" }, { id: "c1" }),
        fakeText("looked"),
        fakeText("done"),
      );
      const w = await fx.open({ tools: [research], policy });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(async () => (await w.harness.approvals(conv.id)).length === 1);
      await w.harness.waitForIdle();
      expect(await allConversations(w.harness)).toHaveLength(1);
      const [item] = await w.harness.approvals(conv.id);
      await w.harness.decideApproval({
        conversationId: conv.id,
        toolTaskId: item?.toolTaskId as string,
        decision: "approve",
        principal: "user:alice",
      });
      expect(((await sub.wait()).result as Any).text).toBe("done");
      expect(await allConversations(w.harness)).toHaveLength(2);
      const [result] = results(await conv.entries());
      expect(result).toMatchObject({
        status: "ok",
        content: "looked",
        approval: { status: "approved", principal: "user:alice" },
      });
      await w.crash();
    });

    it("a spawn beyond maxDepth (default 1) yields limit_exceeded and creates no conversation", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research" });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "go deeper" }, { id: "c1" }),
        fakeToolCall("research", { prompt: "deeper still" }, { id: "c2" }),
        fakeText("could not nest"),
        fakeText("parent done"),
      );
      const w = await fx.open({ tools: [research] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const settled = await (await conv.submit({ text: "go" })).wait();
      expect((settled.result as Any).text).toBe("parent done");
      const ids = await allConversations(w.harness);
      expect(ids).toHaveLength(2);
      const childId = ids.find((id) => id !== conv.id) as string;
      // The child inherited the parent's tools, including the subagent tool.
      expect((await w.harness.session.document(agentConfigDoc, childId)).tools).toEqual([
        "research",
      ]);
      const [nested] = results(await w.harness.conversation(childId).entries());
      expect(nested.status).toBe("limit_exceeded");
      expect(nested.isError).toBe(true);
      expect(nested.details).toMatchObject({ depth: 2, maxDepth: 1 });
      const [outer] = results(await conv.entries());
      expect(outer).toMatchObject({ status: "ok", content: "could not nest" });
      await w.crash();
    });

    it("the engine's maxSubagentDepth caps a tool's larger maxDepth", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", maxDepth: 5 });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "a" }, { id: "c1" }),
        fakeToolCall("research", { prompt: "b" }, { id: "c2" }),
        fakeToolCall("research", { prompt: "c" }, { id: "c3" }),
        fakeText("grandchild stopped"),
        fakeText("child done"),
        fakeText("parent done"),
      );
      const w = await fx.open({ tools: [research], limits: { maxSubagentDepth: 2 } });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const settled = await (await conv.submit({ text: "go" })).wait();
      expect((settled.result as Any).text).toBe("parent done");
      const ids = await allConversations(w.harness);
      expect(ids).toHaveLength(3);
      const depths = await Promise.all(
        ids.map(async (id) => (await w.harness.session.document(agentConfigDoc, id)).depth),
      );
      expect([...depths].sort()).toEqual([0, 1, 2]);
      const grandchild = ids[depths.indexOf(2)] as string;
      const [r] = results(await w.harness.conversation(grandchild).entries());
      expect(r).toMatchObject({ status: "limit_exceeded", details: { depth: 3, maxDepth: 2 } });
      await w.crash();
    });
  });
}
