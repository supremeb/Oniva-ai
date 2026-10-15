import { describe, expect, it } from "vitest";
import { buildContext, ENTRY_KINDS } from "../../src/agent/context-builder.js";
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
const assistant = (blocks: AssistantBlock[], partial: boolean) =>
  entry(ENTRY_KINDS.assistant, {
    runId: "r",
    round: 1,
    usage,
    ...(partial ? { partial: true } : {}),
    message: { role: "assistant", provider: "p", model: "m", blocks, stopReason: "error" },
  });
const user = (text: string) => entry(ENTRY_KINDS.user, { submissionId: "s", content: text });

describe("context of a partial assistant entry", () => {
  it("keeps text, drops incomplete tool calls and thinking, and leaves no open call", () => {
    const { messages } = buildContext([
      user("hi"),
      assistant(
        [
          { type: "thinking", thinking: "hmm" },
          { type: "text", text: "Let me check" },
          { type: "tool_call", id: "t1", name: "read", arguments: {} },
        ],
        true,
      ),
      entry(ENTRY_KINDS.runError, { runId: "r", error: { kind: "overloaded", message: "x" } }),
      user("again"),
    ]);
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant", "user"]);
    expect(messages[1]).toMatchObject({ blocks: [{ type: "text", text: "Let me check" }] });
  });

  it("skips a partial entry with no text", () => {
    const { messages } = buildContext([
      user("hi"),
      assistant([{ type: "tool_call", id: "t1", name: "read", arguments: {} }], true),
      user("again"),
    ]);
    expect(messages.map((m) => m.role)).toEqual(["user", "user"]);
  });

  it("leaves a complete assistant entry untouched", () => {
    const { messages } = buildContext([
      user("hi"),
      assistant([{ type: "tool_call", id: "t1", name: "read", arguments: {} }], false),
    ]);
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant", "tool_result"]);
  });
});
