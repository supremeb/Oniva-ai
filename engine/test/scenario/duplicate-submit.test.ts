/**
 * AE6: the same request id submitted twice, including across a worker
 * restart, returns one submission and stores one user entry.
 */
import { describe, expect, it } from "vitest";
import type { LiveState } from "../../src/agent/progress.js";
import type { ModelScript } from "../support/scripted-model.js";
import { AUTH, call, createConversation, submit, until, useScenario } from "./support.js";

const sc = useScenario();

const slow: ModelScript = {
  rules: [
    {
      when: { role: "user", includes: "HELLO" },
      respond: {
        steps: [
          { type: "text", text: "partial " },
          { type: "delay", ms: 600_000 },
          { type: "text", text: "never" },
        ],
      },
    },
  ],
};

const fast: ModelScript = {
  rules: [{ when: { role: "user" }, respond: { steps: [{ type: "text", text: "hello back" }] } }],
};

describe("AE6 duplicate submit", () => {
  it("returns the same submission across a restart, with one user entry", async () => {
    await sc.script(slow);
    const a = sc.worker("ae6");
    const urlA = await a.serving();
    const conversationId = await createConversation(urlA);
    const body = { text: "HELLO there", requestId: "dup-1" };
    const first = await submit(urlA, conversationId, body);
    const again = await submit(urlA, conversationId, body);
    expect(first.status).toBe(202);
    expect(again.body.submissionId).toBe(first.body.submissionId);

    // Kill the worker mid-reply (partial output committed).
    const ins = await sc.inspect("ae6");
    await until(async () => {
      const live = await ins.doc<LiveState>("agent.live", conversationId);
      return live?.message ? JSON.stringify(live.message).includes("partial") : false;
    }, "partial output");
    a.kill("SIGKILL");
    await a.exited;

    await sc.script(fast);
    const b = sc.worker("ae6");
    const urlB = await b.serving();
    const afterRestart = await submit(urlB, conversationId, body);
    expect(afterRestart.status).toBe(202);
    expect(afterRestart.body.submissionId).toBe(first.body.submissionId);
    // The Idempotency-Key header stands in for a missing requestId.
    const byHeader = await call(urlB, "POST", `/v1/conversations/${conversationId}/submissions`, {
      headers: { ...AUTH, "idempotency-key": "dup-1" },
      body: { text: "HELLO there" },
    });
    expect(byHeader.body.submissionId).toBe(first.body.submissionId);

    const settled = await ins.settled(first.body.submissionId);
    expect(settled.status).toBe("done");
    expect((settled.result as { text?: string }).text).toBe("hello back");
    const entries = await ins.entries(conversationId);
    expect(entries.filter((e) => e.kind === "user")).toHaveLength(1);
    expect(entries.filter((e) => e.kind === "assistant")).toHaveLength(1);
    expect(await ins.submissions(conversationId)).toHaveLength(1);
  });
});
