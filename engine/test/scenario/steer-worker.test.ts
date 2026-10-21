/**
 * U21: a `steer_worker` call reaches a multi-round child at its next round
 * boundary (real worker process, scripted model, gated gateway tool).
 */
import { describe, expect, it } from "vitest";
import { STEER_PREFIX } from "../../src/agent/steer-worker-tool.js";
import { createConversation, submit, useScenario } from "./support.js";

const sc = useScenario();

describe("steer_worker", () => {
  it("places the orchestrator's message into the child's next request, after its tool round", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "PARENT" },
          respond: {
            steps: [
              {
                type: "tool_call",
                name: "delegate",
                arguments: { prompt: "CHILD: read", label: "w1" },
              },
              {
                type: "tool_call",
                name: "steer_worker",
                arguments: { label: "w1", message: "STEER-9 go left" },
              },
            ],
          },
        },
        {
          when: { role: "user", includes: "CHILD" },
          respond: { steps: [{ type: "tool_call", name: "slow_read", arguments: { note: "c" } }] },
        },
        {
          when: { role: "user", includes: "STEER-9" },
          respond: { steps: [{ type: "text", text: "child saw the steer" }] },
        },
        {
          when: { role: "tool_result", tool: "slow_read" },
          respond: { steps: [{ type: "text", text: "child missed the steer" }] },
        },
        {
          when: { role: "tool_result" },
          respond: { steps: [{ type: "text", text: "parent done" }] },
        },
      ],
    });
    const gate = sc.gateway.gate("slow_read");
    const w = sc.worker("steer-worker", {
      ENGINE_SUBAGENT_TOOL: "delegate",
      ENGINE_STEER_WORKER: "1",
    });
    const url = await w.serving();
    const parentId = await createConversation(url, {
      tools: ["delegate", "steer_worker", "slow_read"],
    });
    const sub = await submit(url, parentId, { text: "PARENT task", requestId: "r1" });
    await gate.entered; // the child is mid-round

    const ins = await sc.inspect("steer-worker");
    const child = (await ins.conversations()).find((c) => c.ownerTaskId !== null);
    const childId = child?.id as string;
    // The steer is queued in the child before its round ends.
    const queued = async () =>
      ((await ins.doc<{ queue: Array<{ mode: string }> }>("agent.inbox", childId))?.queue ?? [])
        .length;
    for (let i = 0; i < 200 && (await queued()) === 0; i++) {
      await new Promise((r) => setTimeout(r, 25));
    }
    expect(await queued()).toBe(1);
    gate.release({ content: "read result" });

    const settled = await ins.settled(sub.body.submissionId);
    expect(settled.status).toBe("done");
    const entries = await ins.entries(childId);
    const kinds = entries.filter((e) => e.kind !== "system").map((e) => e.kind);
    expect(kinds).toEqual(["user", "assistant", "tool_result", "user", "assistant"]);
    const steerEntry = entries.filter((e) => e.kind === "user")[1];
    expect(JSON.stringify(steerEntry?.payload)).toContain(`${STEER_PREFIX}STEER-9 go left`);
    const parentResults = (await ins.entries(parentId)).filter((e) => e.kind === "tool_result");
    expect(parentResults.map((e) => (e.payload as { content?: string }).content)).toContain(
      "child saw the steer",
    );
  });
});
