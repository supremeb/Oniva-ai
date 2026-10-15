/**
 * AE5: aborting a run over HTTP while its subagent works aborts the child
 * conversation's tasks before the parent reports idle.
 */
import { describe, expect, it } from "vitest";
import { AUTH, call, createConversation, submit, useScenario } from "./support.js";

const sc = useScenario();

describe("AE5 subagent abort", () => {
  it("aborts the child's tasks before the parent run ends", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "PARENT" },
          respond: {
            steps: [{ type: "tool_call", name: "delegate", arguments: { prompt: "CHILD: read" } }],
          },
        },
        {
          when: { role: "user", includes: "CHILD" },
          respond: { steps: [{ type: "tool_call", name: "slow_read", arguments: { note: "c" } }] },
        },
        { when: { role: "tool_result" }, respond: { steps: [{ type: "text", text: "done" }] } },
      ],
    });
    const gate = sc.gateway.gate("slow_read");
    const w = sc.worker("ae5", { ENGINE_SUBAGENT_TOOL: "delegate" });
    const url = await w.serving();
    const parentId = await createConversation(url, { tools: ["delegate", "slow_read"] });
    const sub = await submit(url, parentId, { text: "PARENT task", requestId: "r1" });
    await gate.entered; // the child's tool call is in flight

    const ins = await sc.inspect("ae5");
    const child = (await ins.conversations()).find((c) => c.ownerTaskId !== null);
    expect(child).toBeDefined();
    const childId = child?.id as string;
    expect((await ins.tasks(childId)).some((t) => t.status !== "terminal")).toBe(true);

    const aborted = await call(url, "POST", `/v1/conversations/${parentId}/abort`, {
      headers: AUTH,
      body: { reason: "user pressed stop" },
    });
    expect(aborted.status).toBe(200);

    // The abort answers only once the run is idle: by then every task of the
    // parent and of the child conversation has ended, without polling.
    const parentTasks = await ins.tasks(parentId);
    const childTasks = await ins.tasks(childId);
    expect(childTasks.length).toBeGreaterThan(0);
    for (const t of [...parentTasks, ...childTasks]) {
      expect(t.status).toBe("terminal");
      expect(t.outcome).toEqual({ type: "aborted", reason: "user pressed stop" });
    }

    // Commit order: the child's tasks ended before the parent's run did.
    const lastChild = Math.max(...childTasks.map((t) => t.seq));
    const parentRun = parentTasks.filter((t) => t.parentTaskId === null);
    expect(parentRun).toHaveLength(1);
    expect(lastChild).toBeLessThan(parentRun[0]?.seq ?? 0);

    // The abort settled the parent submission (and withdrew the child's).
    const settled = await ins.settled(sub.body.submissionId);
    expect(settled.status).toBe("unanswered");
    expect((settled.result as { reason?: string }).reason).toBe("aborted");
    const childSubs = await ins.submissions(childId);
    expect(childSubs.map((s) => s.status)).toEqual(["unanswered"]);
  });
});
