/**
 * The engine shape Deep Work relies on (plan 2026-10-15-002, U2 verification):
 * ten sibling subagent calls run concurrently under the worker pool's
 * `concurrency` (16) and survive a kill, aborting the root reaches every child
 * before the root is terminal, and a child's gateway calls carry the root
 * conversation id. Several subagent tools come from `ENGINE_SUBAGENT_TOOLS`.
 */
import { describe, expect, it } from "vitest";
import type { ModelScript } from "../support/scripted-model.js";
import { AUTH, call, createConversation, submit, until, useScenario } from "./support.js";

const sc = useScenario();

const WORKERS = 10;
const ENV = { ENGINE_SUBAGENT_TOOLS: "research_worker, reflector" };
const AGENT = { tools: ["research_worker", "reflector", "slow_read"] };

const SCRIPT: ModelScript = {
  rules: [
    {
      when: { role: "user", includes: "FANOUT" },
      respond: {
        steps: Array.from({ length: WORKERS }, (_, i) => ({
          type: "tool_call" as const,
          name: "research_worker",
          arguments: { prompt: `CHILD ${i}` },
        })),
      },
    },
    {
      when: { role: "user", includes: "CHILD" },
      respond: { steps: [{ type: "tool_call", name: "slow_read", arguments: { note: "c" } }] },
    },
    {
      when: { role: "user", includes: "ROOT" },
      respond: { steps: [{ type: "tool_call", name: "slow_read", arguments: { note: "r" } }] },
    },
    {
      when: { role: "tool_result", tool: "slow_read" },
      respond: { steps: [{ type: "text", text: "learned" }] },
    },
    {
      when: { role: "tool_result", tool: "research_worker" },
      respond: { steps: [{ type: "text", text: "report ready" }] },
    },
  ],
};

const gates = () => Array.from({ length: WORKERS }, () => sc.gateway.gate("slow_read"));

describe("deep work shape", () => {
  it("ten parallel subagent calls run concurrently and finish after a kill and restart, with ten children", async () => {
    await sc.script(SCRIPT);
    const held = gates();
    const a = sc.worker("dw-kill", ENV);
    const url = await a.serving();
    const parentId = await createConversation(url, AGENT);
    const sub = await submit(url, parentId, { text: "FANOUT", requestId: "r1" });
    expect(sub.status).toBe(202);
    // All ten children are inside their tool call at the same time.
    const entered = await Promise.all(held.map((g) => g.entered));
    expect(new Set(entered.map((e) => e.idempotencyKey)).size).toBe(WORKERS);

    const ins = await sc.inspect("dw-kill");
    const childrenBefore = (await ins.conversations()).filter((c) => c.ownerTaskId !== null);
    expect(childrenBefore).toHaveLength(WORKERS);

    // Every call carries the root conversation id and its own as engineConversationId.
    const bodies = sc.gateway.gw
      .invokes("slow_read")
      .map((r) => r.body as { conversationId: string; engineConversationId: string });
    expect(bodies.length).toBeGreaterThanOrEqual(WORKERS);
    for (const b of bodies) expect(b.conversationId).toBe(parentId);
    expect(new Set(bodies.map((b) => b.engineConversationId))).toEqual(
      new Set(childrenBefore.map((c) => c.id)),
    );

    a.kill("SIGKILL");
    await a.exited;
    const b = sc.worker("dw-kill", ENV);
    await b.serving();
    const settled = await ins.settled(sub.body.submissionId as string);
    expect(settled.status).toBe("done");
    expect((settled.result as { text?: string }).text).toBe("report ready");

    const childrenAfter = (await ins.conversations()).filter((c) => c.ownerTaskId !== null);
    expect(childrenAfter.map((c) => c.id).sort()).toEqual(childrenBefore.map((c) => c.id).sort());
    for (const c of childrenAfter) expect(await ins.submissions(c.id)).toHaveLength(1);
  });

  it("aborting the root with ten children in flight ends every child task before the root", async () => {
    await sc.script(SCRIPT);
    const held = gates();
    const w = sc.worker("dw-abort", ENV);
    const url = await w.serving();
    const parentId = await createConversation(url, AGENT);
    await submit(url, parentId, { text: "FANOUT", requestId: "r1" });
    await Promise.all(held.map((g) => g.entered));

    const aborted = await call(url, "POST", `/v1/conversations/${parentId}/abort`, {
      headers: AUTH,
      body: { reason: "user pressed stop" },
    });
    expect(aborted.status).toBe(200);

    const ins = await sc.inspect("dw-abort");
    const children = (await ins.conversations()).filter((c) => c.ownerTaskId !== null);
    expect(children).toHaveLength(WORKERS);
    const root = (await ins.tasks(parentId)).filter((t) => t.parentTaskId === null);
    expect(root).toHaveLength(1);
    let lastChild = 0;
    for (const c of children) {
      for (const t of await ins.tasks(c.id)) {
        expect(t.status).toBe("terminal");
        expect(t.outcome?.type).toBe("aborted");
        lastChild = Math.max(lastChild, t.seq);
      }
      // Nothing the child writes lands after the root's terminal commit.
      for (const e of await ins.entries(c.id)) {
        expect((e as { seq: number }).seq).toBeLessThan(root[0]?.seq ?? 0);
      }
    }
    expect(lastChild).toBeLessThan(root[0]?.seq ?? 0);
    expect(root[0]?.status).toBe("terminal");
  });

  it("a top-level call carries its own id as both conversation ids", async () => {
    await sc.script(SCRIPT);
    const w = sc.worker("dw-root", ENV);
    const url = await w.serving();
    const parentId = await createConversation(url, AGENT);
    const before = sc.gateway.gw.invokes("slow_read").length;
    const sub = await submit(url, parentId, { text: "ROOT call", requestId: "r1" });
    const ins = await sc.inspect("dw-root");
    await ins.settled(sub.body.submissionId as string);
    const mine = await until(() => {
      const all = sc.gateway.gw.invokes("slow_read").slice(before);
      return all.length > 0 ? all : null;
    }, "the root's gateway call");
    const body = mine.at(-1)?.body as { conversationId: string; engineConversationId: string };
    expect(body.conversationId).toBe(parentId);
    expect(body.engineConversationId).toBe(parentId);
  });
});
