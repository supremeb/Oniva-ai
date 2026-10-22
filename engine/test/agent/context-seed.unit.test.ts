import { describe, expect, it } from "vitest";
import {
  composeChildPrompt,
  FORK_BLOCK_MAX_TOKENS,
  FORK_HEADER,
  FORK_RECENT_ENTRIES,
  renderForkBlock,
} from "../../src/agent/context-seed.js";
import type { EntryRecord } from "../../src/storage/types.js";

let n = 0;
const entry = (kind: string, payload: unknown): EntryRecord =>
  ({
    id: `e${++n}`,
    conversationId: "c",
    taskId: null,
    kind,
    payload,
    createdAt: 0,
  }) as unknown as EntryRecord;
const user = (text: string) => entry("user", { submissionId: "s", content: text });
const assistant = (text: string) =>
  entry("assistant", { runId: "r", round: 0, message: { blocks: [{ type: "text", text }] } });
const result = (name: string, body: string) =>
  entry("tool_result", {
    runId: "r",
    toolCallId: "t",
    toolName: name,
    status: "ok",
    content: body,
    isError: false,
    details: null,
    terminate: false,
    attempts: 1,
  });

describe("composeChildPrompt", () => {
  it("is the prompt itself without any part", () => {
    expect(composeChildPrompt("do it", {})).toBe("do it");
    expect(composeChildPrompt("do it", { preamble: "", brief: "", files: [], fork: null })).toBe(
      "do it",
    );
  });
});

describe("renderForkBlock", () => {
  it("is null with nothing to tell", () => {
    expect(renderForkBlock([])).toBeNull();
  });

  it("keeps the newest compaction summary and the last N entries, bodies elided", () => {
    const entries = [
      user("old question"),
      entry("compaction", {
        summary: "## Goal\nStudy markets\n\nFull earlier transcript: /x/archive.md",
        firstKeptEntryId: null,
      }),
      ...Array.from({ length: FORK_RECENT_ENTRIES + 3 }, (_, i) => user(`turn ${i}`)),
      result("probe", "BODY-TEXT"),
    ];
    const block = renderForkBlock(entries) as string;
    expect(block.startsWith(FORK_HEADER)).toBe(true);
    expect(block).toContain("Study markets");
    expect(block).not.toContain("archive.md");
    expect(block).not.toContain("old question");
    expect(block).not.toContain("BODY-TEXT");
    expect(block).toContain("returned a result to the parent (9 characters, not copied)");
    expect(block.match(/The user said/g)).toHaveLength(FORK_RECENT_ENTRIES - 1);
    expect(block).toContain("turn 8");
  });

  it("is bounded by the token cap", () => {
    const entries = Array.from({ length: 40 }, () => assistant("y".repeat(50_000)));
    const block = renderForkBlock(entries, { recent: 40 }) as string;
    expect(block.length).toBeLessThanOrEqual(FORK_BLOCK_MAX_TOKENS * 4);
  });

  it("flags a tainted parent", () => {
    expect(renderForkBlock([user("hi")], { tainted: true })).toContain("does not trust");
    expect(renderForkBlock([user("hi")])).not.toContain("does not trust");
  });
});
