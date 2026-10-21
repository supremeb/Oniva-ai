import { describe, expect, it } from "vitest";
import { buildContext, ENTRY_KINDS } from "../../src/agent/context-builder.js";
import {
  applyClearing,
  type ContextState,
  clearStub,
  planClearing,
} from "../../src/agent/context-clearing.js";
import type { AssistantBlock } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";

let n = 0;
const entry = (kind: string, payload: unknown): EntryRecord => ({
  id: `e${++n}`,
  conversationId: "c",
  taskId: null,
  kind,
  payload: payload as EntryRecord["payload"],
  createdAt: n,
});
const usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
const user = (text: string) => entry(ENTRY_KINDS.user, { submissionId: "s", content: text });
const assistant = (blocks: AssistantBlock[]) =>
  entry(ENTRY_KINDS.assistant, {
    runId: "r",
    round: 1,
    usage,
    message: { role: "assistant", provider: "p", model: "m", blocks, stopReason: "tool_use" },
  });
const result = (id: string, tool: string, chars: number, details: unknown = null) =>
  entry(ENTRY_KINDS.toolResult, {
    runId: "r",
    toolCallId: id,
    toolName: tool,
    status: "ok",
    content: "y".repeat(chars),
    isError: false,
    details,
    terminate: false,
    attempts: 1,
  });
const call = (id: string, tool: string) =>
  assistant([{ type: "tool_call", id, name: tool, arguments: {} }]);

/** user, then one call/result pair of 400 characters (100 tokens) per tool. */
function transcript(tools: string[], details: Record<number, unknown> = {}): EntryRecord[] {
  const entries: EntryRecord[] = [user("go")];
  tools.forEach((t, i) => {
    entries.push(call(`c${i}`, t), result(`c${i}`, t, 400, details[i] ?? null));
  });
  return entries;
}

const NONE: ContextState = { clearedThroughEntryId: null, clearedTokens: 0 };
const settings = {
  clearTriggerTokens: 100,
  clearAtLeastTokens: 200,
  keepToolResults: 2,
  excludeTools: [] as string[],
};
const STUB_A = "[cleared: a result, ~100 tokens. Re-run the tool.]";
const BODY = "y".repeat(400);

describe("clearStub", () => {
  it("has the fixed text, and names the saved path when the result was offloaded", () => {
    const plain = transcript(["web"])[2] as EntryRecord;
    expect(clearStub(plain.payload as never)).toBe(
      "[cleared: web result, ~100 tokens. Re-run the tool.]",
    );
    const saved = transcript(["web"], {
      0: { offloaded: { path: "tool_results/c0.txt", bytes: 400 } },
    })[2] as EntryRecord;
    expect(clearStub(saved.payload as never)).toBe(
      "[cleared: web result, ~100 tokens. Re-run the tool, or read tool_results/c0.txt if it was saved.]",
    );
  });

  it("ignores a malformed details.offloaded", () => {
    for (const details of [{ offloaded: "p" }, { offloaded: { path: 3 } }, [1], "x", null]) {
      const e = transcript(["web"], { 0: details })[2] as EntryRecord;
      expect(clearStub(e.payload as never)).toBe(
        "[cleared: web result, ~100 tokens. Re-run the tool.]",
      );
    }
  });
});

describe("buildContext with a watermark", () => {
  it("is byte-identical to today without a watermark (golden)", () => {
    const entries = transcript(["a", "b", "c"]);
    const plain = buildContext(entries);
    expect(buildContext(entries, {})).toEqual(plain);
    expect(buildContext(entries, { watermark: null })).toEqual(plain);
    expect(buildContext(entries, { watermark: "missing" })).toEqual(plain);
    const names = ["a", "b", "c"];
    expect(JSON.stringify(plain.messages)).toBe(
      JSON.stringify([
        { role: "user", content: "go" },
        ...names.flatMap((name, i) => [
          {
            role: "assistant",
            provider: "p",
            model: "m",
            blocks: [{ type: "tool_call", id: `c${i}`, name, arguments: {} }],
            stopReason: "tool_use",
          },
          {
            role: "tool_result",
            toolCallId: `c${i}`,
            toolName: name,
            content: BODY,
            isError: false,
          },
        ]),
      ]),
    );
  });

  it("stubs results at or before the watermark, except excluded tools, and keeps calls", () => {
    const entries = transcript(["a", "keep", "a", "a"]);
    const wm = entries[6]?.id ?? null; // result of c2
    const { messages } = buildContext(entries, { watermark: wm, excludeTools: ["keep"] });
    const res = messages.filter((m) => m.role === "tool_result") as Array<{ content: string }>;
    expect(res.map((m) => m.content)).toEqual([STUB_A, BODY, STUB_A, BODY]);
    expect(messages.filter((m) => m.role === "assistant")).toHaveLength(4);
  });

  it("a compaction head after the watermark starts the context at the head, with no stub for entries outside it", () => {
    const entries = transcript(["a", "a", "a"]);
    const wm = entries[2]?.id ?? null;
    entries.push(
      entry(ENTRY_KINDS.compaction, { summary: "S", firstKeptEntryId: null }),
      call("c9", "a"),
      result("c9", "a", 400),
    );
    const { messages, headEntryId } = buildContext(entries, { watermark: wm });
    expect(headEntryId).not.toBeNull();
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant", "tool_result"]);
    expect((messages[2] as { content: string }).content).toBe(BODY);
  });
});

describe("planClearing", () => {
  const entries = transcript(["a", "a", "a", "a", "a"]); // 5 results of 100 tokens

  it("plans nothing when off, below the trigger, or when too little would be freed", () => {
    expect(planClearing(entries, { ...settings, clearTriggerTokens: 0 }, 9999, NONE)).toBeNull();
    expect(planClearing(entries, settings, 99, NONE)).toBeNull();
    expect(planClearing(entries, { ...settings, clearAtLeastTokens: 301 }, 500, NONE)).toBeNull();
  });

  it("keeps the newest keepToolResults and takes the newest candidate as the watermark", () => {
    const plan = planClearing(entries, settings, 500, NONE);
    expect(plan).toEqual({ watermark: entries[6]?.id, freedTokens: 300 });
  });

  it("skips excluded tools and results already behind the watermark; never moves backward", () => {
    const mixed = transcript(["a", "keep", "a", "a", "a"]);
    const s = { ...settings, excludeTools: ["keep"] };
    const plan = planClearing(mixed, s, 500, NONE);
    expect(plan).toEqual({ watermark: mixed[6]?.id, freedTokens: 200 });
    const state: ContextState = {
      clearedThroughEntryId: plan?.watermark ?? null,
      clearedTokens: 200,
    };
    expect(planClearing(mixed, s, 500, state)).toBeNull();
    const ahead: ContextState = { clearedThroughEntryId: entries[8]?.id ?? null, clearedTokens: 0 };
    expect(planClearing(entries, settings, 500, ahead)).toBeNull();
  });

  it("counts only results that are in context", () => {
    const withHead = [
      ...entries,
      entry(ENTRY_KINDS.compaction, { summary: "S", firstKeptEntryId: null }),
      call("n1", "a"),
      result("n1", "a", 400),
    ];
    expect(planClearing(withHead, { ...settings, clearAtLeastTokens: 1 }, 500, NONE)).toBeNull();
  });
});

describe("applyClearing", () => {
  it("yields no options when disabled or nothing is cleared", () => {
    const on = { clearTriggerTokens: 5, excludeTools: ["k"] };
    const state: ContextState = { clearedThroughEntryId: "e1", clearedTokens: 1 };
    expect(applyClearing({ ...on, clearTriggerTokens: 0 }, state)).toEqual({});
    expect(applyClearing(on, NONE)).toEqual({});
    expect(applyClearing(on, state)).toEqual({ watermark: "e1", excludeTools: ["k"] });
  });
});
