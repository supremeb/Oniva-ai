/**
 * AE4: a steer submitted over HTTP while a tool round is blocked lands after
 * that round and before the next model request, in the same run.
 */
import { describe, expect, it } from "vitest";
import type {
  AssistantPayload,
  ToolResultPayload,
  UserPayload,
} from "../../src/agent/context-builder.js";
import { SseClient } from "../server/support.js";
import { AUTH, createConversation, submit, useScenario } from "./support.js";

const sc = useScenario();

describe("AE4 steer while busy", () => {
  it("places an HTTP steer into the running run at the next tool-round boundary", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "START" },
          respond: { steps: [{ type: "tool_call", name: "slow_read", arguments: { note: "s" } }] },
        },
        // The next request ends with the steer: answer it.
        {
          when: { role: "user", includes: "STEER-1" },
          respond: { steps: [{ type: "text", text: "steered answer" }] },
        },
        // Reached only if the steer missed the round.
        {
          when: { role: "tool_result" },
          respond: { steps: [{ type: "text", text: "unsteered" }] },
        },
      ],
    });
    const gate = sc.gateway.gate("slow_read");
    const w = sc.worker("ae4");
    const url = await w.serving();
    const conversationId = await createConversation(url, { tools: ["slow_read"] });
    const sse = await SseClient.open(`${url}/v1/conversations/${conversationId}/watch`, AUTH);
    expect(sse.status).toBe(200);

    const first = await submit(url, conversationId, { text: "START work", requestId: "r1" });
    await gate.entered;
    const steer = await submit(url, conversationId, {
      text: "STEER-1 change course",
      requestId: "r2",
      whenBusy: "steer",
    });
    expect(steer.status).toBe(202);
    expect(steer.body.submissionId).not.toBe(first.body.submissionId);
    gate.release({ content: "read result" });

    const ins = await sc.inspect("ae4");
    const a = await ins.settled(first.body.submissionId);
    const b = await ins.settled(steer.body.submissionId);
    expect(a.status).toBe("done");
    expect(b.status).toBe("done");
    // Both settle with the same run's answer.
    expect((a.result as { text?: string }).text).toBe("steered answer");
    expect((b.result as { text?: string }).text).toBe("steered answer");

    const entries = await ins.entries(conversationId);
    const kinds = entries.filter((e) => e.kind !== "system").map((e) => e.kind);
    expect(kinds).toEqual(["user", "assistant", "tool_result", "user", "assistant"]);
    const users = entries.filter((e) => e.kind === "user").map((e) => e.payload as UserPayload);
    expect(users.map((u) => u.submissionId)).toEqual([
      first.body.submissionId,
      steer.body.submissionId,
    ]);
    // One run: both assistant replies and the tool result carry the same run id.
    const runIds = new Set(
      entries
        .filter((e) => e.kind === "assistant" || e.kind === "tool_result")
        .map((e) => (e.payload as unknown as AssistantPayload | ToolResultPayload).runId),
    );
    expect(runIds.size).toBe(1);
    const rounds = entries
      .filter((e) => e.kind === "assistant")
      .map((e) => (e.payload as unknown as AssistantPayload).round);
    expect(rounds).toEqual([0, 1]);

    // The watch stream saw the steer arrive while the run was busy.
    const frames = await sse.frames(300);
    expect(frames.length).toBeGreaterThan(0);
    sse.close();
  });
});
