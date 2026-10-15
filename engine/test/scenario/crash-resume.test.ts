/**
 * AE1 / AE2: a worker is SIGKILLed while a gateway tool runs; a replacement
 * takes the lease and resumes the run.
 */
import { describe, expect, it } from "vitest";
import type { ToolResultPayload } from "../../src/agent/context-builder.js";
import type { ModelScript } from "../support/scripted-model.js";
import { createConversation, payloads, submit, until, useScenario } from "./support.js";

const sc = useScenario();

const AGENT = { tools: ["write_once", "write_idem"] };

const SCRIPT: ModelScript = {
  rules: [
    {
      when: { role: "user", includes: "WRITE_ONCE" },
      respond: { steps: [{ type: "tool_call", name: "write_once", arguments: { note: "a" } }] },
    },
    {
      when: { role: "user", includes: "WRITE_IDEM" },
      respond: { steps: [{ type: "tool_call", name: "write_idem", arguments: { note: "b" } }] },
    },
    { when: { role: "tool_result" }, respond: { steps: [{ type: "text", text: "finished" }] } },
  ],
};

/** Start a worker, submit `text`, and SIGKILL the worker once `tool` reaches the gateway. */
async function killDuring(sessionId: string, tool: string, text: string) {
  await sc.script(SCRIPT);
  const gate = sc.gateway.gate(tool);
  const a = sc.worker(sessionId);
  const url = await a.serving();
  const conversationId = await createConversation(url, AGENT);
  const sub = await submit(url, conversationId, { text, requestId: "r1" });
  expect(sub.status).toBe(202);
  const call = await gate.entered;
  a.kill("SIGKILL");
  await a.exited;
  return { gate, call, conversationId, submissionId: sub.body.submissionId as string };
}

describe("AE1/AE2 crash resume", () => {
  it("AE1: a non-replay-safe write killed after its outcome was recorded resolves to that outcome", async () => {
    const gw = sc.gateway.gw;
    const before = gw.effects("write_once");
    const { gate, call, conversationId, submissionId } = await killDuring(
      "ae1-recorded",
      "write_once",
      "WRITE_ONCE please",
    );
    // The effect completes after the worker died: the gateway records the outcome.
    gate.release({ content: "stored outcome 42", details: { stored: true } });
    await until(() => gw.outcome(call.idempotencyKey), "the gateway to record the outcome");

    const b = sc.worker("ae1-recorded");
    await b.serving();
    const ins = await sc.inspect("ae1-recorded");
    const settled = await ins.settled(submissionId);
    expect(settled.status).toBe("done");
    const results = payloads<ToolResultPayload>(await ins.entries(conversationId), "tool_result");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      status: "ok",
      content: "stored outcome 42",
      isError: false,
    });
    expect(gw.effects("write_once") - before).toBe(1);
    expect(
      gw.invokes("write_once").filter((r) => (r.body as { runId?: string }).runId === call.runId),
    ).toHaveLength(1);
  });

  it("AE1: a non-replay-safe write killed without a recorded outcome is reported interrupted", async () => {
    const gw = sc.gateway.gw;
    const before = gw.effects("write_once");
    const { gate, call, conversationId, submissionId } = await killDuring(
      "ae1-none",
      "write_once",
      "WRITE_ONCE again",
    );
    gate.fail(); // the effect ran but its outcome was never recorded
    const b = sc.worker("ae1-none");
    await b.serving();
    const ins = await sc.inspect("ae1-none");
    const settled = await ins.settled(submissionId);
    expect(settled.status).toBe("done");
    const results = payloads<ToolResultPayload>(await ins.entries(conversationId), "tool_result");
    expect(results).toHaveLength(1);
    expect(results[0]?.status).toBe("interrupted");
    expect(results[0]?.isError).toBe(true);
    expect(JSON.stringify(results[0]?.content)).toMatch(/interrupted/i);
    expect(gw.outcome(call.idempotencyKey)).toBeUndefined();
    expect(gw.effects("write_once") - before).toBe(1);
    expect(
      gw.invokes("write_once").filter((r) => (r.body as { runId?: string }).runId === call.runId),
    ).toHaveLength(1);
  });

  it("AE2: a replay-safe write is re-executed with the same idempotency key", async () => {
    const gw = sc.gateway.gw;
    const before = gw.effects("write_idem");
    const { gate, call, conversationId, submissionId } = await killDuring(
      "ae2",
      "write_idem",
      "WRITE_IDEM please",
    );
    gate.fail();
    const b = sc.worker("ae2");
    await b.serving();
    const ins = await sc.inspect("ae2");
    const settled = await ins.settled(submissionId);
    expect(settled.status).toBe("done");
    expect((settled.result as { text?: string }).text).toBe("finished");
    const results = payloads<ToolResultPayload>(await ins.entries(conversationId), "tool_result");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ status: "ok", attempts: 2 });
    expect(gw.effects("write_idem") - before).toBe(2);
    const keys = gw
      .invokes("write_idem")
      .filter((r) => (r.body as { runId?: string }).runId === call.runId)
      .map((r) => r.headers["idempotency-key"]);
    expect(keys).toEqual([call.idempotencyKey, call.idempotencyKey]);
  });
});
