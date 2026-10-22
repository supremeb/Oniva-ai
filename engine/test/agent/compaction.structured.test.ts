/**
 * Structured delta compaction (U14): the pure pieces. Section parsing and
 * repair, the verbatim goal section across compactions, the todo re-attach,
 * the archive rendering, the old prompt's golden form, and how compaction
 * composes with the Tier 1 clearing watermark.
 */
import { describe, expect, it } from "vitest";
import {
  COMPACTION_SYSTEM_PROMPT,
  completeStructuredSummary,
  openTodoItems,
  parseSummarySections,
  planCompaction,
  renderArchive,
  renderStructuredTranscript,
  renderTranscript,
  STRUCTURED_COMPACTION_PROMPT,
  STRUCTURED_HEADINGS,
  splitSummaryTrailer,
  withTrailer,
} from "../../src/agent/compaction.js";
import { buildContext, SUMMARY_PREFIX } from "../../src/agent/context-builder.js";
import { applyClearing, clearStub, planClearing } from "../../src/agent/context-clearing.js";
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
const usage = { inputTokens: 10, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
function assistant(text: string, calls: Array<{ id: string; name: string; args?: object }> = []) {
  return entry("assistant", {
    runId: "r",
    round: 0,
    usage,
    message: {
      role: "assistant",
      provider: "fake",
      model: "fake-1",
      blocks: [
        ...(text ? [{ type: "text", text }] : []),
        ...calls.map((c) => ({
          type: "tool_call",
          id: c.id,
          name: c.name,
          arguments: c.args ?? {},
        })),
      ],
    },
  });
}
const result = (id: string, content: string, toolName = "t", details: unknown = null) =>
  entry("tool_result", {
    runId: "r",
    toolCallId: id,
    toolName,
    status: "ok",
    content,
    isError: false,
    details,
    terminate: false,
    attempts: 1,
  });

const sections = (over: Partial<Record<(typeof STRUCTURED_HEADINGS)[number], string>> = {}) =>
  STRUCTURED_HEADINGS.map((h) => `## ${h}\n${over[h] ?? `body of ${h}`}`).join("\n\n");

describe("structured prompt", () => {
  it("names the six sections in order and tells the model to edit, not rewrite", () => {
    expect(STRUCTURED_HEADINGS).toEqual([
      "Goal and constraints",
      "Decisions",
      "Facts and IDs",
      "Files and artifacts",
      "Open tasks",
      "Errors and dead ends",
    ]);
    let at = -1;
    for (const h of STRUCTURED_HEADINGS) {
      const next = STRUCTURED_COMPACTION_PROMPT.indexOf(`## ${h}`);
      expect(next).toBeGreaterThan(at);
      at = next;
    }
    expect(STRUCTURED_COMPACTION_PROMPT).toMatch(/edit/i);
    expect(STRUCTURED_COMPACTION_PROMPT).toMatch(/verbatim/i);
  });

  it("gives the previous summary's sections apart from the new transcript", () => {
    const entries = [
      user("a".repeat(400)),
      assistant("b".repeat(400)),
      user("c".repeat(400)),
      assistant("d"),
    ];
    const plan = planCompaction(entries, 10);
    expect(plan).not.toBeNull();
    const first = renderStructuredTranscript({ ...(plan as NonNullable<typeof plan>) });
    expect(first).not.toContain("<previous_summary");
    expect(first).toContain("<conversation>");

    const prev = `${sections({ "Goal and constraints": "GOAL-X" })}\n\nFull earlier transcript: conversation_history/k.md\n\nOpen todo (as of the summary):\n- [ ] old`;
    const second = renderStructuredTranscript({
      ...(plan as NonNullable<typeof plan>),
      previousSummary: prev,
    });
    expect(second).toContain("<previous_summary_sections>");
    expect(second).toContain("GOAL-X");
    // The appended trailer is not shown to the model: it is re-derived.
    expect(second).not.toContain("conversation_history/k.md");
    expect(second).not.toContain("Open todo");
    expect(second.indexOf("<previous_summary_sections>")).toBeLessThan(
      second.indexOf("<conversation>"),
    );
  });
});

describe("section parsing and repair", () => {
  it("parses the sections and splits the trailer off", () => {
    const text = `${sections()}\n\nFull earlier transcript: conversation_history/a.md\nFull earlier transcript: conversation_history/b.md\n\nOpen todo (as of the summary):\n- [ ] one\n- [~] two`;
    const split = splitSummaryTrailer(text);
    expect(split.archives).toEqual(["conversation_history/a.md", "conversation_history/b.md"]);
    expect(split.todo).toEqual(["- [ ] one", "- [~] two"]);
    const parsed = parseSummarySections(split.body);
    expect([...parsed.keys()]).toEqual([...STRUCTURED_HEADINGS]);
    expect(parsed.get("Decisions")).toBe("body of Decisions");
  });

  it("accepts a complete summary and reports nothing missing", () => {
    const out = completeStructuredSummary(sections(), null);
    expect(out.missing).toEqual([]);
    expect(out.summary).toBe(sections());
  });

  it("restores a missing section verbatim from the previous summary", () => {
    const prev = sections({ Decisions: "DECIDED-A", "Open tasks": "TASK-A" });
    const model = sections({ "Facts and IDs": "FACT-NEW" })
      .replace(/## Decisions\n.*\n\n/, "")
      .replace(/## Open tasks\n.*\n\n/, "");
    expect(model).not.toContain("## Decisions");
    const out = completeStructuredSummary(model, prev);
    expect(out.missing).toEqual(["Decisions", "Open tasks"]);
    const parsed = parseSummarySections(out.summary);
    expect(parsed.get("Decisions")).toBe("DECIDED-A");
    expect(parsed.get("Open tasks")).toBe("TASK-A");
    expect(parsed.get("Facts and IDs")).toBe("FACT-NEW");
    expect([...parsed.keys()]).toEqual([...STRUCTURED_HEADINGS]);
  });

  it("marks a missing section empty when there is no previous summary", () => {
    const out = completeStructuredSummary("## Decisions\nonly this", null);
    expect(out.missing).toHaveLength(5);
    const parsed = parseSummarySections(out.summary);
    expect([...parsed.keys()]).toEqual([...STRUCTURED_HEADINGS]);
    expect(parsed.get("Decisions")).toBe("only this");
    expect(parsed.get("Goal and constraints")).toBe("(nothing recorded)");
  });

  it("keeps the goal section byte-for-byte across three compactions, extending only", () => {
    const goal0 = "- user: answer in French\n- user: never touch prod";
    let summary = completeStructuredSummary(
      sections({ "Goal and constraints": goal0 }),
      null,
    ).summary;
    // Compaction 2: the model rewrites the goal section; the old text must survive.
    summary = completeStructuredSummary(
      sections({ "Goal and constraints": "User wants French and no prod changes." }),
      summary,
    ).summary;
    // Compaction 3: the model keeps it and adds a new instruction.
    const goal2 = parseSummarySections(summary).get("Goal and constraints") as string;
    summary = completeStructuredSummary(
      sections({ "Goal and constraints": `${goal2}\n- user: keep answers short` }),
      summary,
    ).summary;
    // Compaction 4: the goal section is dropped altogether.
    summary = completeStructuredSummary("## Decisions\nx", summary).summary;
    const goal = parseSummarySections(summary).get("Goal and constraints") as string;
    expect(goal.startsWith(goal0)).toBe(true);
    expect(goal).toContain("- user: keep answers short");
    expect(goal).toContain("User wants French and no prod changes.");
  });

  it("appends the archive paths and the open todo as a trailer", () => {
    const body = sections();
    const text = withTrailer(body, {
      archives: ["conversation_history/a.md", "conversation_history/b.md"],
      todo: ["- [ ] ship it"],
    });
    expect(text).toBe(
      `${body}\n\nFull earlier transcript: conversation_history/a.md\nFull earlier transcript: conversation_history/b.md\n\nOpen todo (as of the summary):\n- [ ] ship it`,
    );
    expect(withTrailer(body, { archives: [], todo: [] })).toBe(body);
  });
});

describe("open todo re-attach", () => {
  const todoCall = (id: string, args: object) => [
    assistant("", [{ id, name: "update_todo", args }]),
    result(id, "ok", "update_todo"),
  ];

  it("folds update_todo calls in order and keeps only open items", () => {
    const entries = [
      user("go"),
      ...todoCall("c1", { add: ["write tests", "fix bug", "ship"] }),
      ...todoCall("c2", { completed: ["Write tests"] }),
      ...todoCall("c3", { add: ["announce"] }),
    ];
    const plan = planCompaction([...entries, user("next")], 1);
    expect(openTodoItems(plan?.summarized ?? [], null)).toEqual([
      "- [ ] fix bug",
      "- [ ] ship",
      "- [ ] announce",
    ]);
  });

  it("has no todo when the range has no update_todo call", () => {
    const entries = [user("go"), assistant("hi"), user("next")];
    const plan = planCompaction(entries, 1);
    expect(openTodoItems(plan?.summarized ?? [], null)).toEqual([]);
  });

  it("starts from the previous summary's open todo", () => {
    const prev = withTrailer(sections(), {
      archives: [],
      todo: ["- [ ] carried", "- [ ] done soon"],
    });
    const entries = [user("go"), ...todoCall("c1", { completed: ["done soon"], add: ["fresh"] })];
    const plan = planCompaction([...entries, user("next")], 1);
    expect(openTodoItems(plan?.summarized ?? [], prev)).toEqual(["- [ ] carried", "- [ ] fresh"]);
  });

  it("treats a call with a full list as a snapshot", () => {
    const entries = [
      user("go"),
      ...todoCall("c1", { add: ["stale"] }),
      ...todoCall("c2", {
        todos: [
          { content: "one", status: "in_progress" },
          { content: "two", status: "completed" },
          { content: "three", status: "pending" },
        ],
      }),
    ];
    const plan = planCompaction([...entries, user("next")], 1);
    expect(openTodoItems(plan?.summarized ?? [], null)).toEqual(["- [~] one", "- [ ] three"]);
  });

  it("ignores a partial reply's calls (they never ran)", () => {
    const partial = entry("assistant", {
      runId: "r",
      round: 0,
      usage,
      partial: true,
      message: {
        role: "assistant",
        provider: "fake",
        model: "fake-1",
        blocks: [
          { type: "tool_call", id: "p", name: "update_todo", arguments: { add: ["ghost"] } },
        ],
      },
    });
    const plan = planCompaction([user("go"), partial, user("next")], 1);
    expect(openTodoItems(plan?.summarized ?? [], null)).toEqual([]);
  });
});

describe("archive rendering", () => {
  it("renders every summarized entry without the per-item clip", () => {
    const big = "z".repeat(20_000);
    const entries = [
      user("question"),
      assistant("", [{ id: "c1", name: "t", args: { q: 1 } }]),
      result("c1", big),
      assistant("answer"),
      user("next"),
    ];
    const plan = planCompaction(entries, 1);
    const archive = renderArchive(plan as NonNullable<typeof plan>);
    expect(archive).toContain("question");
    expect(archive).toContain(big);
    expect(archive).toContain("answer");
    // The prompt's transcript still clips the same result.
    expect(renderTranscript(plan as NonNullable<typeof plan>)).not.toContain(big);
  });
});

describe("old prompt (structuredSummary off) is unchanged", () => {
  it("keeps the system prompt text", () => {
    expect(COMPACTION_SYSTEM_PROMPT).toBe(
      [
        "You compress the earlier part of a conversation between a user and an AI assistant that uses tools.",
        "Write a summary the assistant can continue from without the original messages.",
        "Keep: the user's goals and instructions, decisions made, facts learned, names, identifiers, paths and numbers that may matter later, tool calls made and what they returned, and any work still open.",
        "Drop pleasantries and repetition. If a previous summary is given, fold it in.",
        "Reply with the summary only.",
      ].join("\n"),
    );
  });

  it("keeps the transcript form", () => {
    const entries = [
      user("hello"),
      assistant("hi", [{ id: "c1", name: "t", args: { a: 1 } }]),
      result("c1", "out"),
      assistant("done"),
      user("more"),
    ];
    const plan = planCompaction(entries, 1);
    expect(
      renderTranscript({ ...(plan as NonNullable<typeof plan>), previousSummary: "PREV" }),
    ).toBe(
      [
        "<previous_summary>\nPREV\n</previous_summary>",
        '<conversation>\n[user]\nhello\n\n[assistant]\nhi\n\n[assistant called tool t]\n{"a":1}\n\n[result of t]\nout\n\n[assistant]\ndone\n</conversation>',
        "Summarize the conversation above.",
      ].join("\n\n"),
    );
  });
});

describe("compaction and the clearing watermark compose", () => {
  const settings = {
    clearTriggerTokens: 1,
    clearAtLeastTokens: 1,
    keepToolResults: 1,
    excludeTools: [] as string[],
  };
  const system = entry("system", { text: "sys", tools: [], fingerprint: "f" });

  it("a watermark inside the summarized range is irrelevant after the compaction", () => {
    const u1 = user("q1");
    const a1 = assistant("", [{ id: "c1", name: "t" }]);
    const r1 = result("c1", "R1".repeat(100));
    const a2 = assistant("", [{ id: "c2", name: "t" }]);
    const r2 = result("c2", "R2".repeat(100));
    const u2 = user("q2");
    const a3 = assistant("", [{ id: "c3", name: "t" }]);
    const r3 = result("c3", "R3".repeat(100));
    const u3 = user("q3");
    const before = [system, u1, a1, r1, a2, r2, u2, a3, r3, u3];

    // Clearing advanced to r2 (r1 and r2 are stale).
    const state = { clearedThroughEntryId: r2.id, clearedTokens: 100 };
    const cleared = buildContext(before, applyClearing(settings, state));
    expect(JSON.stringify(cleared.messages)).toContain("[cleared: t result");

    // Compaction summarizes up to u2... and keeps u2 onward verbatim.
    // (r1 and r2, the cleared results, are in the summarized range.)
    const head = entry("compaction", { summary: "S", firstKeptEntryId: u2.id });
    const after = [...before, head];

    const ctx = buildContext(after, applyClearing(settings, state));
    const text = JSON.stringify(ctx.messages);
    expect(ctx.messages[1]).toEqual({ role: "user", content: `${SUMMARY_PREFIX}S` });
    // The old watermark does not stub anything that is still in context.
    expect(text).not.toContain("[cleared:");
    expect(text).toContain("R3R3");
    // And the plan after the head starts after the old watermark's effect.
    const next = planClearing(after, { ...settings, keepToolResults: 0 }, 10, state);
    expect(next?.watermark).toBe(r3.id);
  });

  it("a watermark among the kept entries still stubs them; summarized ones are gone", () => {
    const u1 = user("q1");
    const a1 = assistant("", [{ id: "c1", name: "t" }]);
    const r1 = result("c1", "R1".repeat(100));
    const u2 = user("q2");
    const a2 = assistant("", [{ id: "c2", name: "t" }]);
    const r2 = result("c2", "R2".repeat(100));
    const u3 = user("q3");
    const before = [system, u1, a1, r1, u2, a2, r2, u3];
    const state = { clearedThroughEntryId: r2.id, clearedTokens: 100 };
    const head = entry("compaction", { summary: "S", firstKeptEntryId: u2.id });
    const ctx = buildContext([...before, head], applyClearing(settings, state));
    const text = JSON.stringify(ctx.messages);
    expect(text).toContain(clearStub(r2.payload as never).slice(0, 20));
    expect(text).not.toContain("R1R1");
  });
});
