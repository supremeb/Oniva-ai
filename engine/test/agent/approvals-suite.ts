/**
 * The approvals suite (U9), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { ApprovalRejected } from "../../src/agent/approvals.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, type AgentWorker, allowAll, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to entry payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);

const MINUTE = 60_000;
const AGENT = { systemPrompt: "s", tools: ["deploy"] };

function deployTool(log: string[]) {
  return defineTool({
    name: "deploy",
    description: "Deploy a service",
    parameters: z.object({ service: z.string() }),
    execute: async (args) => {
      log.push(`deploy:${args.service}`);
      return { content: `deployed ${args.service}` };
    },
  });
}

/** A policy requiring approval; `expiresIn` is relative to the time of the call. */
function approvalPolicy(now: () => number, expiresIn?: number): HostPolicy {
  return {
    resolveModelRequest: allowAll.resolveModelRequest,
    authorizeTool: () => ({
      decision: "requireApproval",
      reason: "deploys need a human",
      ...(expiresIn === undefined ? {} : { expiresAt: now() + expiresIn }),
    }),
  };
}

const toolTask = (w: AgentWorker) => w.harness.runtime.tasks().find((t) => t.kind === "agent.tool");

async function pendingApproval(w: AgentWorker, conversationId: string) {
  await waitFor(async () => {
    const items = await w.harness.approvals(conversationId);
    return items.some((i) => i.status === "pending");
  });
  await w.harness.waitForIdle();
  const items = await w.harness.approvals(conversationId);
  return items.find((i) => i.status === "pending") as (typeof items)[number];
}

async function rejection(p: Promise<unknown>): Promise<ApprovalRejected> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(ApprovalRejected);
  return err as ApprovalRejected;
}

export function defineApprovalSuite(backend: AgentBackend): void {
  describe(`approvals (${backend.name})`, () => {
    it("AE3: after 61 minutes and a reopen, an approval runs the tool once and records the principal", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      const options = () => ({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now()),
      });
      fx.provider.enqueue(fakeToolCall("deploy", { service: "api" }, { id: "c1" }));
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy the api" });
      const item = await pendingApproval(w1, conv.id);
      const task = toolTask(w1);
      expect(item).toMatchObject({
        toolTaskId: task?.id,
        toolCallId: "c1",
        toolName: "deploy",
        arguments: { service: "api" },
        reason: "deploys need a human",
        requestedAt: fx.clock.now(),
        expiresAt: null,
        status: "pending",
        decision: null,
      });
      expect(item.id).toMatch(/^appr_/);
      // The wait holds no running task.
      expect(w1.harness.runtime.tasks({ status: "running" })).toEqual([]);
      expect(task?.status).toBe("waiting");
      expect(task?.waiting?.signal).toBe("approval");
      expect(log).toEqual([]);
      await w1.crash();

      fx.clock.advance(61 * MINUTE);
      fx.provider.enqueue(fakeText("Deployed."));
      const w2 = await fx.open(options());
      await w2.harness.waitForIdle();
      expect(w2.harness.runtime.tasks({ status: "running" })).toEqual([]);
      expect((await w2.harness.approvals(conv.id))[0]?.status).toBe("pending");

      const decided = await w2.harness.decideApproval({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        decision: "approve",
        principal: "user:alice",
        note: "go ahead",
      });
      expect(decided.status).toBe("approved");
      expect(decided.decision).toEqual({
        decision: "approve",
        principal: "user:alice",
        note: "go ahead",
        decidedAt: fx.clock.now(),
      });

      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("Deployed.");
      expect(log).toEqual(["deploy:api"]);
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      expect(result.status).toBe("ok");
      expect(result.content).toBe("deployed api");
      expect(result.attempts).toBe(1);
      expect(result.approval).toEqual({
        approvalId: item.id,
        status: "approved",
        principal: "user:alice",
        note: "go ahead",
        decidedAt: fx.clock.now(),
      });

      const again = await rejection(
        w2.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "deny",
          principal: "user:mallory",
        }),
      );
      expect(again.reason).toBe("already_decided");
      expect(log).toEqual(["deploy:api"]);
      expect((await w2.harness.approvals(conv.id))[0]?.decision?.principal).toBe("user:alice");
      await w2.crash();
    });

    it("a denial yields a denied result and the model continues", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(
        fakeToolCall("deploy", { service: "db" }, { id: "c1" }),
        fakeText("Understood, not deploying."),
      );
      const w = await fx.open({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now()),
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy the db" });
      const item = await pendingApproval(w, conv.id);
      await w.harness.decideApproval({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        decision: "deny",
        principal: "user:bob",
        note: "not during business hours",
      });
      const settled = await sub.wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("Understood, not deploying.");
      const [result] = results(await conv.entries());
      expect(result.status).toBe("denied");
      expect(result.isError).toBe(true);
      expect(result.attempts).toBe(0);
      expect(result.content).toContain("not during business hours");
      expect(result.approval).toMatchObject({ status: "denied", principal: "user:bob" });
      expect(log).toEqual([]);
      // The model saw the denial.
      const second = fx.provider.requests[1];
      expect(second?.messages.at(-1)?.role).toBe("tool_result");
      expect((await w.harness.approvals(conv.id))[0]?.status).toBe("denied");
      await w.crash();
    });

    it("expiry at 30 minutes with the clock at 31 yields approval_expired and rejects later decisions", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(
        fakeToolCall("deploy", { service: "api" }, { id: "c1" }),
        fakeText("It expired."),
      );
      const w = await fx.open({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now(), 30 * MINUTE),
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy" });
      const item = await pendingApproval(w, conv.id);
      expect(item.expiresAt).toBe(item.requestedAt + 30 * MINUTE);
      expect(toolTask(w)?.waiting?.until).toBe(item.expiresAt);

      await fx.clock.advanceAsync(31 * MINUTE);
      expect((await sub.wait()).status).toBe("done");
      const [result] = results(await conv.entries());
      expect(result.status).toBe("approval_expired");
      expect(result.isError).toBe(true);
      expect(result.approval).toMatchObject({ status: "expired", principal: null });
      expect((await w.harness.approvals(conv.id))[0]?.status).toBe("expired");

      const late = await rejection(
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "approve",
          principal: "user:alice",
        }),
      );
      expect(late.reason).toBe("expired");
      expect(log).toEqual([]);
      await w.crash();
    });

    it("an approval that expired while no worker ran expires on reopen; a decision in between is rejected", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      const options = () => ({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now(), 30 * MINUTE),
      });
      fx.provider.enqueue(fakeToolCall("deploy", { service: "api" }, { id: "c1" }));
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy" });
      const item = await pendingApproval(w1, conv.id);
      await w1.crash();

      fx.clock.advance(90 * MINUTE);
      fx.provider.enqueue(fakeText("Expired."));
      const w2 = await fx.open(options());
      const late = await rejection(
        w2.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "approve",
          principal: "user:alice",
        }),
      );
      expect(["expired", "not_waiting"]).toContain(late.reason);
      expect((await w2.harness.submission(sub.id).wait()).status).toBe("done");
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      expect(result.status).toBe("approval_expired");
      expect(log).toEqual([]);
      await w2.crash();
    });

    it("a decision naming a conversation that does not own the tool task is rejected", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(fakeToolCall("deploy", { service: "api" }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now()),
      });
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
      const sub = await conv.submit({ text: "deploy" });
      const item = await pendingApproval(w, conv.id);

      const wrong = await rejection(
        w.harness.decideApproval({
          conversationId: other.id,
          toolTaskId: item.toolTaskId,
          decision: "approve",
          principal: "user:eve",
        }),
      );
      expect(wrong.reason).toBe("not_owned");
      const unknown = await rejection(
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: "task_unknown",
          decision: "approve",
          principal: "user:eve",
        }),
      );
      expect(unknown.reason).toBe("not_owned");
      const anonymous = await rejection(
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "approve",
          principal: "",
        }),
      );
      expect(anonymous.reason).toBe("invalid");
      const bogus = await rejection(
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "maybe" as Any,
          principal: "user:eve",
        }),
      );
      expect(bogus.reason).toBe("invalid");

      // Nothing changed: still pending, still waiting, nothing ran.
      expect((await w.harness.approvals(conv.id))[0]?.status).toBe("pending");
      expect(await w.harness.approvals(other.id)).toEqual([]);
      expect(toolTask(w)?.status).toBe("waiting");
      expect(log).toEqual([]);

      await w.harness.decideApproval({
        conversationId: conv.id,
        toolTaskId: item.toolTaskId,
        decision: "approve",
        principal: "user:alice",
      });
      expect((await sub.wait()).status).toBe("done");
      expect(log).toEqual(["deploy:api"]);
      await w.crash();
    });

    it("parallel calls are decided independently and each approved call executes exactly once", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(
        {
          steps: [
            { type: "tool_call", id: "c1", name: "deploy", arguments: { service: "a" } },
            { type: "tool_call", id: "c2", name: "deploy", arguments: { service: "b" } },
          ],
        },
        fakeText("One of two."),
      );
      const w = await fx.open({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now()),
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy both" });
      await waitFor(async () => (await w.harness.approvals(conv.id)).length === 2);
      await w.harness.waitForIdle();
      expect(w.harness.runtime.tasks({ status: "running" })).toEqual([]);
      const items = await w.harness.approvals(conv.id);
      const byCall = Object.fromEntries(items.map((i) => [i.toolCallId, i]));
      const [d1, d2] = await Promise.all([
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: byCall.c1?.toolTaskId as string,
          decision: "approve",
          principal: "user:alice",
        }),
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: byCall.c2?.toolTaskId as string,
          decision: "deny",
          principal: "user:alice",
        }),
      ]);
      expect([d1.status, d2.status]).toEqual(["approved", "denied"]);
      expect((await sub.wait()).status).toBe("done");
      const rs = results(await conv.entries()).sort((a, b) =>
        a.toolCallId.localeCompare(b.toolCallId),
      );
      expect(rs.map((r) => r.status)).toEqual(["ok", "denied"]);
      expect(log).toEqual(["deploy:a"]);
      await w.crash();
    });

    it("aborting a waiting call cancels its approval and later decisions are rejected", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(fakeToolCall("deploy", { service: "api" }, { id: "c1" }));
      const w = await fx.open({
        tools: [deployTool(log)],
        policy: approvalPolicy(() => fx.clock.now()),
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "deploy" });
      const item = await pendingApproval(w, conv.id);
      await w.harness.runtime.abortConversation(conv.id, "user");
      await sub.wait();
      await w.harness.waitForIdle();
      expect((await w.harness.approvals(conv.id))[0]?.status).toBe("cancelled");
      const late = await rejection(
        w.harness.decideApproval({
          conversationId: conv.id,
          toolTaskId: item.toolTaskId,
          decision: "approve",
          principal: "user:alice",
        }),
      );
      expect(late.reason).toBe("already_decided");
      expect(log).toEqual([]);
      await w.crash();
    });
  });
}
