/**
 * Compaction planning (U11): the token estimate, thresholds and cut point,
 * as pure functions over entries.
 */
import { describe, expect, it } from "vitest";
import {
  compactionThresholds,
  estimateContextTokens,
  planCompaction,
  resolveCompactionSettings,
} from "../../src/agent/compaction.js";
import type { JsonValue } from "../../src/core/json.js";
import type { EntryRecord } from "../../src/storage/types.js";

let seq = 0;
function entry(kind: string, payload: unknown): EntryRecord {
  seq++;
  return {
    id: `e${String(seq).padStart(4, "0")}`,
    conversationId: "c",
    taskId: null,
    kind,
    payload: payload as JsonValue,
    createdAt: seq,
  };
}
const user = (text: string) => entry("user", { submissionId: "s", content: text });
const usage = (inputTokens: number) => ({
  inputTokens,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
});
function assistant(text: string, calls: string[] = [], input = 10): EntryRecord {
  return entry("assistant", {
    runId: "r",
    round: 0,
    usage: usage(input),
    message: {
      role: "assistant",
      provider: "fake",
      model: "fake-1",
      blocks: [
        ...(text ? [{ type: "text", text }] : []),
        ...calls.map((id) => ({ type: "tool_call", id, name: "t", arguments: {} })),
      ],
    },
  });
}
const result = (id: string, content: string) =>
  entry("tool_result", {
    runId: "r",
    toolCallId: id,
    toolName: "t",
    status: "ok",
    content,
    isError: false,
    details: null,
    terminate: false,
    attempts: 1,
  });
const system = () => entry("system", { text: "sys", tools: [], fingerprint: "f" });

describe("compaction planning", () => {
  it("estimates the last reported usage plus a character heuristic for newer entries", () => {
    const entries = [
      system(),
      user("x".repeat(40)),
      assistant("ok", [], 500),
      user("y".repeat(80)),
    ];
    expect(estimateContextTokens(entries)).toEqual({ tokens: 500 + 20, model: "fake-1" });
  });

  it("estimates by characters alone when no reply was reported since the newest compaction", () => {
    const entries = [
      system(),
      user("x".repeat(400)),
      assistant("ok", [], 5_000),
      entry("compaction", { summary: "s".repeat(40), firstKeptEntryId: null }),
      user("y".repeat(40)),
    ];
    const est = estimateContextTokens(entries);
    // The usage before the compaction is stale; the estimate covers the
    // system text, the summary (with its prefix) and the new input.
    expect(est.tokens).toBeLessThan(100);
    expect(est.tokens).toBeGreaterThan(20);
  });

  it("derives the thresholds from the window", () => {
    const s = resolveCompactionSettings({
      contextWindow: 1000,
      reserveTokens: 100,
      backgroundMargin: 200,
    });
    expect(compactionThresholds(s, null)).toEqual({ window: 1000, blocking: 900, background: 700 });
    const byModel = resolveCompactionSettings({ contextWindows: { "fake-1": 2000 } });
    expect(compactionThresholds(byModel, "fake-1").window).toBe(2000);
    expect(() => resolveCompactionSettings({ reserveTokens: -1 })).toThrow(RangeError);
  });

  it("keeps about keepRecentTokens verbatim and summarizes the rest", () => {
    const entries = [
      system(),
      user("a".repeat(400)), // 100 tokens
      assistant("b".repeat(400)),
      user("c".repeat(400)),
      assistant("d".repeat(400)),
      user("e".repeat(40)),
    ];
    const plan = planCompaction(entries, 150);
    // Walking back: e (10) + d (100) + c (100) >= 150 at c.
    expect(plan?.firstKeptEntryId).toBe(entries[3]?.id);
    expect(plan?.summarized.map((e) => e.id)).toEqual([entries[1]?.id, entries[2]?.id]);
    expect(plan?.previousSummary).toBeNull();
  });

  it("never cuts between a tool call and its result", () => {
    const entries = [
      user("start"),
      assistant("", ["c1", "c2"]),
      result("c1", "r".repeat(400)),
      result("c2", "r".repeat(400)),
      assistant("done"),
    ];
    // Enough tokens are reached inside the results; the cut moves back to the call.
    const plan = planCompaction(entries, 50);
    expect(plan?.firstKeptEntryId).toBe(entries[1]?.id);
    for (const keep of [1, 50, 150, 250, 10_000]) {
      const p = planCompaction(entries, keep);
      if (p === null) continue;
      const at = entries.findIndex((e) => e.id === p.firstKeptEntryId);
      expect(["user", "assistant"]).toContain(entries[at]?.kind);
      expect(at === 2 || at === 3).toBe(false);
    }
  });

  it("continues from the previous compaction and returns null when there is nothing to summarize", () => {
    const kept = user("kept");
    const entries = [
      user("old"),
      assistant("old reply"),
      kept,
      entry("compaction", { summary: "S1", firstKeptEntryId: kept.id }),
      assistant("new reply"),
      user("newest"),
    ];
    const plan = planCompaction(entries, 1);
    expect(plan?.previousSummary).toBe("S1");
    expect(plan?.summarized.map((e) => e.id)).toEqual([kept.id, entries[4]?.id]);
    expect(plan?.firstKeptEntryId).toBe(entries[5]?.id);
    expect(planCompaction([system(), user("only")], 1)).toBeNull();
  });
});
