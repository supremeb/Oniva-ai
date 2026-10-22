/**
 * Compaction (R14): keep a conversation's context inside the model window.
 *
 * Token estimate: the usage the newest reply reported (its prompt plus its
 * output) plus a characters/4 heuristic for the entries written after it.
 * When no reply was reported since the newest `compaction` entry, the whole
 * built context is estimated by characters.
 *
 * Thresholds, from the window (`contextWindow`, else the window the policy
 * resolved for the model, else `contextWindows[model]`, else {@link DEFAULT_CONTEXT_WINDOW}):
 * - above `window − reserve − backgroundMargin`, a generation starts a
 *   background compaction task and carries on; the conversation keeps
 *   answering, and the finished summary is placed at the next turn boundary
 *   (the start of the next generation request, in the same run or the next);
 * - at `window − reserve`, the generation waits durably on a compaction task
 *   (the running one, or a new one) before it sends its request. If that
 *   compaction fails, the input settles `unanswered(compaction_failed)`.
 * - a `context_overflow` error from the provider triggers one compaction and
 *   one retry; a second overflow fails the run with `model_error`.
 *
 * The compaction task (`agent.compaction`, a root task of the conversation)
 * summarizes the entries before a cut point that keeps about
 * `keepRecentTokens` of recent context verbatim. The cut is always at a
 * `user` or `assistant` entry and never separates a tool call from its result.
 * Its summary request goes through `HostPolicy.resolveModelRequest` with
 * purpose `compaction` and its spend is counted in the usage document. The
 * result waits in the `agent.compaction` document until a generation appends
 * it as a `compaction` entry ({@link CompactionPayload}), which becomes the
 * context head. A crash re-runs the task from the start; nothing is placed
 * twice because placing clears the document in the same commit.
 */
import { z } from "zod";
import type { Context } from "../core/context.js";
import { LeaseLost } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import { modelError } from "../models/stream.js";
import type { Message, ModelError, ModelInput, TextContent, Usage } from "../models/types.js";
import { contextTokens } from "../models/usage.js";
import { defineDoc } from "../session/documents.js";
import type { Transaction } from "../session/transaction.js";
import type { EntryRecord, TaskRecord } from "../storage/types.js";
import {
  type Committed,
  defineTask,
  done,
  type TaskDefinition,
  type TaskOps,
  type TaskScope,
  type TaskState,
} from "../tasks/define.js";
import type { ToolConversation } from "../tools/registry.js";
import {
  assistantText,
  buildContext,
  type CompactionPayload,
  ENTRY_KINDS,
  payloadOf,
  toEntryPayload,
} from "./context-builder.js";
import type { CompactionQuality } from "./hooks.js";
import { runLane } from "./lane.js";
import {
  type CompactionOverrides,
  type CompactionSettings,
  compactionSettingsSchema,
} from "./prompt.js";
import { type AgentDeps, listAllEntries, loadConversation } from "./tool-task.js";
import { recordUsage, usageDoc } from "./usage.js";

export const COMPACTION_TASK_KIND = "agent.compaction";

/** Window assumed when neither the settings nor the model table name one. */
export const DEFAULT_CONTEXT_WINDOW = 200_000;

export const DEFAULT_COMPACTION_SETTINGS: Readonly<CompactionSettings> = Object.freeze({
  enabled: true,
  contextWindow: null,
  contextWindows: {},
  reserveTokens: 16_384,
  backgroundMargin: 16_384,
  keepRecentTokens: 20_000,
  offloadTokens: 0,
  clearTriggerTokens: 0,
  keepToolResults: 5,
  clearAtLeastTokens: 8_000,
  excludeTools: [],
  structuredSummary: false,
  archiveSummarized: false,
});

const CHARS_PER_TOKEN = 4;
/** A structured summary under this share of what it replaced is reported as suspect. */
const MIN_SUMMARY_RATIO = 0.01;
/** Longest single item (message, tool result) quoted into a summary request. */
const MAX_ITEM_CHARS = 8_000;

export const COMPACTION_SYSTEM_PROMPT = [
  "You compress the earlier part of a conversation between a user and an AI assistant that uses tools.",
  "Write a summary the assistant can continue from without the original messages.",
  "Keep: the user's goals and instructions, decisions made, facts learned, names, identifiers, paths and numbers that may matter later, tool calls made and what they returned, and any work still open.",
  "Drop pleasantries and repetition. If a previous summary is given, fold it in.",
  "Reply with the summary only.",
].join("\n");

// ---- structured summary (Tier 2) -----------------------------------------------

/** The six sections of a structured summary, in order. */
export const STRUCTURED_HEADINGS = [
  "Goal and constraints",
  "Decisions",
  "Facts and IDs",
  "Files and artifacts",
  "Open tasks",
  "Errors and dead ends",
] as const;

export type StructuredHeading = (typeof STRUCTURED_HEADINGS)[number];

const GOAL_HEADING: StructuredHeading = "Goal and constraints";
const EMPTY_SECTION = "(nothing recorded)";

/** Used when `structuredSummary` is on; the plain prompt stays the default. */
export const STRUCTURED_COMPACTION_PROMPT = [
  "You maintain a running summary of a long conversation between a user and an AI assistant that uses tools.",
  "The assistant will continue from this summary alone, so it must stay accurate and must not shrink away detail from earlier rounds.",
  "Write the summary as exactly these six sections, with these headings, in this order:",
  ...STRUCTURED_HEADINGS.map((h) => `## ${h}`),
  "What goes where:",
  "- Goal and constraints: what the user wants and every instruction or constraint they gave, quoted in their own words. Copy this section from the previous summary byte for byte (verbatim), then add new user instructions as new lines after it. Never reword, merge or drop a line of it.",
  "- Decisions: choices made and why.",
  "- Facts and IDs: facts learned, plus names, identifiers, URLs, numbers and values that may be needed again, exactly as they appeared.",
  "- Files and artifacts: paths, documents and outputs produced or read, with what each holds.",
  "- Open tasks: work still to do, and the next step.",
  "- Errors and dead ends: what failed or was ruled out, so it is not tried again.",
  "When a previous summary is given, edit its sections in place: keep each existing line unless the new messages make it wrong or finished, change only what the new messages change, and add the new material under the right heading. Do not rewrite sections from scratch and do not shorten them to save space.",
  "Leave a section with the single line (nothing recorded) when it has nothing to hold.",
  "Reply with the six sections only, no preface.",
].join("\n");

// ---- settings ---------------------------------------------------------------

/** Merge settings layers (later wins) over the defaults and validate the result. */
export function resolveCompactionSettings(
  ...layers: ReadonlyArray<CompactionOverrides | null | undefined>
): CompactionSettings {
  const out: CompactionSettings = structuredClone(
    DEFAULT_COMPACTION_SETTINGS,
  ) as CompactionSettings;
  for (const layer of layers) {
    if (!layer) continue;
    for (const [key, value] of Object.entries(layer)) {
      if (value === undefined) continue;
      if (key === "contextWindows") {
        out.contextWindows = { ...out.contextWindows, ...(value as Record<string, number>) };
      } else {
        (out as Record<string, unknown>)[key] = value;
      }
    }
  }
  const parsed = compactionSettingsSchema.safeParse(out);
  if (!parsed.success) {
    throw new RangeError(`invalid compaction settings: ${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
}

export interface CompactionThresholds {
  window: number;
  /** At or above: the next request waits for a compaction. */
  blocking: number;
  /** Above: a background compaction starts. */
  background: number;
}

export function compactionThresholds(
  settings: CompactionSettings,
  model: string | null,
  resolvedWindow?: number,
): CompactionThresholds {
  const window =
    settings.contextWindow ??
    resolvedWindow ??
    (model !== null && Object.hasOwn(settings.contextWindows, model)
      ? (settings.contextWindows[model] as number)
      : DEFAULT_CONTEXT_WINDOW);
  const blocking = Math.max(0, window - settings.reserveTokens);
  return { window, blocking, background: Math.max(0, blocking - settings.backgroundMargin) };
}

// ---- estimate ---------------------------------------------------------------

function contentChars(content: string | TextContent[]): number {
  return typeof content === "string"
    ? content.length
    : content.reduce((n, part) => n + part.text.length, 0);
}

function messageChars(m: Message): number {
  switch (m.role) {
    case "system":
      return m.text.length;
    case "user":
    case "tool_result":
      return contentChars(m.content);
    case "assistant": {
      let n = 0;
      for (const b of m.blocks) {
        if (b.type === "text") n += b.text.length;
        else if (b.type === "thinking") n += b.thinking.length;
        else n += b.name.length + JSON.stringify(b.arguments).length;
      }
      return n;
    }
  }
}

const CONTEXT_KINDS: ReadonlySet<string> = new Set([
  ENTRY_KINDS.user,
  ENTRY_KINDS.assistant,
  ENTRY_KINDS.toolResult,
]);

/** Characters an entry adds to the model context (0 for kinds the model never sees). */
export function entryChars(e: EntryRecord): number {
  switch (e.kind) {
    case ENTRY_KINDS.user:
      return contentChars(payloadOf(e, ENTRY_KINDS.user).content);
    case ENTRY_KINDS.assistant:
      return messageChars(payloadOf(e, ENTRY_KINDS.assistant).message);
    case ENTRY_KINDS.toolResult:
      return contentChars(payloadOf(e, ENTRY_KINDS.toolResult).content);
    default:
      return 0;
  }
}

const toTokens = (chars: number) => Math.ceil(chars / CHARS_PER_TOKEN);

function newestIndex(entries: readonly EntryRecord[], kind: string, after = -1): number {
  for (let i = entries.length - 1; i > after; i--) if (entries[i]?.kind === kind) return i;
  return -1;
}

/**
 * Estimated tokens of the next request's context, and the model of the
 * newest reply (for the window lookup), or null when there is none.
 */
export function estimateContextTokens(entries: readonly EntryRecord[]): {
  tokens: number;
  model: string | null;
} {
  const head = newestIndex(entries, ENTRY_KINDS.compaction);
  const last = newestIndex(entries, ENTRY_KINDS.assistant, head);
  if (last >= 0) {
    const p = payloadOf(entries[last] as EntryRecord, ENTRY_KINDS.assistant);
    let chars = 0;
    for (let i = last + 1; i < entries.length; i++) chars += entryChars(entries[i] as EntryRecord);
    return { tokens: contextTokens(p.usage) + toTokens(chars), model: p.message.model };
  }
  const built = buildContext(entries);
  const chars = built.messages.reduce((n, m) => n + messageChars(m), 0);
  const any = newestIndex(entries, ENTRY_KINDS.assistant);
  const model =
    any >= 0 ? payloadOf(entries[any] as EntryRecord, ENTRY_KINDS.assistant).message.model : null;
  return { tokens: toTokens(chars), model };
}

// ---- cut point --------------------------------------------------------------

export interface CompactionPlan {
  /** Summary of the current head, folded into the new one; null when there is no head. */
  previousSummary: string | null;
  /** Context entries to summarize, in order. */
  summarized: EntryRecord[];
  /** First entry kept verbatim. */
  firstKeptEntryId: string;
  /** The current head (newest compaction entry), or null. */
  headEntryId: string | null;
}

/**
 * Choose what to summarize: everything in the current context before the
 * newest `user`/`assistant` entry from which at least `keepRecentTokens`
 * remain (or the newest such entry when the whole context is shorter). An
 * entry is only a cut point when every tool call before it has its result
 * before it. Null when nothing can be summarized.
 */
export function planCompaction(
  entries: readonly EntryRecord[],
  keepRecentTokens: number,
): CompactionPlan | null {
  const head = newestIndex(entries, ENTRY_KINDS.compaction);
  let start = 0;
  let previousSummary: string | null = null;
  if (head >= 0) {
    const c = payloadOf(entries[head] as EntryRecord, ENTRY_KINDS.compaction);
    previousSummary = c.summary;
    const keptAt =
      c.firstKeptEntryId === null ? -1 : entries.findIndex((e) => e.id === c.firstKeptEntryId);
    start = keptAt >= 0 && keptAt < head ? keptAt : head + 1;
  }

  const resultAt = new Map<string, number>();
  for (let i = start; i < entries.length; i++) {
    const e = entries[i] as EntryRecord;
    if (e.kind !== ENTRY_KINDS.toolResult) continue;
    const id = payloadOf(e, ENTRY_KINDS.toolResult).toolCallId;
    if (!resultAt.has(id)) resultAt.set(id, i);
  }
  // valid[i]: a user/assistant entry with every earlier call's result before it.
  const valid: boolean[] = new Array(entries.length).fill(false);
  let reach = -1;
  for (let i = start; i < entries.length; i++) {
    const e = entries[i] as EntryRecord;
    const turn = e.kind === ENTRY_KINDS.user || e.kind === ENTRY_KINDS.assistant;
    valid[i] = turn && reach < i;
    if (e.kind === ENTRY_KINDS.assistant) {
      for (const b of payloadOf(e, ENTRY_KINDS.assistant).message.blocks) {
        if (b.type !== "tool_call") continue;
        const at = resultAt.get(b.id);
        if (at !== undefined && at > reach) reach = at;
      }
    }
  }

  let cut = -1;
  let fallback = -1;
  let kept = 0;
  for (let i = entries.length - 1; i > start; i--) {
    kept += toTokens(entryChars(entries[i] as EntryRecord));
    if (!valid[i]) continue;
    if (fallback < 0) fallback = i;
    if (kept >= keepRecentTokens) {
      cut = i;
      break;
    }
  }
  if (cut < 0) cut = fallback;
  if (cut < 0) return null;
  const summarized = entries.slice(start, cut).filter((e) => CONTEXT_KINDS.has(e.kind));
  if (summarized.length === 0) return null;
  return {
    previousSummary,
    summarized: summarized.map((e) => structuredClone(e)),
    firstKeptEntryId: (entries[cut] as EntryRecord).id,
    headEntryId: head >= 0 ? (entries[head] as EntryRecord).id : null,
  };
}

function clip(text: string): string {
  if (text.length <= MAX_ITEM_CHARS) return text;
  return `${text.slice(0, MAX_ITEM_CHARS)}\n[... ${text.length - MAX_ITEM_CHARS} more characters omitted]`;
}

const plain = (content: string | TextContent[]) =>
  typeof content === "string" ? content : content.map((c) => c.text).join("");

function renderEntries(entries: readonly EntryRecord[], limit: (text: string) => string): string {
  const lines: string[] = [];
  for (const e of entries) {
    if (e.kind === ENTRY_KINDS.user) {
      lines.push(`[user]\n${limit(plain(payloadOf(e, ENTRY_KINDS.user).content))}`);
    } else if (e.kind === ENTRY_KINDS.assistant) {
      const message = payloadOf(e, ENTRY_KINDS.assistant).message;
      const text = assistantText(message);
      if (text.length > 0) lines.push(`[assistant]\n${limit(text)}`);
      for (const b of message.blocks) {
        // A partial reply's tool calls were never run.
        if (b.type === "tool_call" && payloadOf(e, ENTRY_KINDS.assistant).partial !== true) {
          lines.push(`[assistant called tool ${b.name}]\n${limit(JSON.stringify(b.arguments))}`);
        }
      }
    } else {
      const r = payloadOf(e, ENTRY_KINDS.toolResult);
      const flag = r.isError ? ` (${r.status})` : "";
      lines.push(`[result of ${r.toolName}${flag}]\n${limit(plain(r.content))}`);
    }
  }
  return lines.join("\n\n");
}

/** The summary request's user message: the previous summary and the entries, as text. */
export function renderTranscript(plan: CompactionPlan): string {
  const parts: string[] = [];
  if (plan.previousSummary !== null) {
    parts.push(`<previous_summary>\n${plan.previousSummary}\n</previous_summary>`);
  }
  parts.push(`<conversation>\n${renderEntries(plan.summarized, clip)}\n</conversation>`);
  parts.push("Summarize the conversation above.");
  return parts.join("\n\n");
}

/**
 * The structured request's user message: the previous summary's sections,
 * apart from the new transcript. The trailer a summary carries (archive
 * paths, open todo) is not shown; it is derived again each time.
 */
export function renderStructuredTranscript(plan: CompactionPlan): string {
  const parts: string[] = [];
  if (plan.previousSummary !== null) {
    const { body } = splitSummaryTrailer(plan.previousSummary);
    parts.push(`<previous_summary_sections>\n${body}\n</previous_summary_sections>`);
  }
  parts.push(`<conversation>\n${renderEntries(plan.summarized, clip)}\n</conversation>`);
  parts.push(
    plan.previousSummary === null
      ? "Write the six-section summary of the conversation above."
      : "Edit the previous summary's sections in place to cover the new conversation above. Carry Goal and constraints forward byte for byte and only add new user instructions to it.",
  );
  return parts.join("\n\n");
}

/** The whole summarized range as text, with no per-item clip, for the archive. */
export function renderArchive(plan: CompactionPlan): string {
  return `${renderEntries(plan.summarized, (t) => t)}\n`;
}

// ---- structured summary: parsing, repair, trailer ----------------------------

const ARCHIVE_LINE = "Full earlier transcript: ";
const TODO_HEADER = "Open todo (as of the summary):";

/** Split off the lines compaction appends after the sections. */
export function splitSummaryTrailer(summary: string): {
  body: string;
  archives: string[];
  todo: string[];
} {
  const lines = summary.split("\n");
  const archives: string[] = [];
  const todo: string[] = [];
  let cut = lines.length;
  let inTodo = false;
  for (const [i, line] of lines.entries()) {
    if (inTodo) {
      if (/^- \[[ ~]\] /.test(line)) todo.push(line);
      continue;
    }
    if (line.startsWith(ARCHIVE_LINE)) {
      archives.push(line.slice(ARCHIVE_LINE.length).trim());
      cut = Math.min(cut, i);
    } else if (line === TODO_HEADER) {
      inTodo = true;
      cut = Math.min(cut, i);
    }
  }
  return { body: lines.slice(0, cut).join("\n").trimEnd(), archives, todo };
}

/** The summary with its trailer: archive paths, then the open todo. */
export function withTrailer(
  body: string,
  trailer: { archives: readonly string[]; todo: readonly string[] },
): string {
  let out = body;
  if (trailer.archives.length > 0) {
    out += `\n\n${trailer.archives.map((p) => `${ARCHIVE_LINE}${p}`).join("\n")}`;
  }
  if (trailer.todo.length > 0) out += `\n\n${TODO_HEADER}\n${trailer.todo.join("\n")}`;
  return out;
}

const headingOf = (line: string): StructuredHeading | null => {
  const m = /^#{1,3}\s+(.+?)\s*:?\s*$/.exec(line.trim());
  const name = m?.[1]?.toLowerCase();
  return STRUCTURED_HEADINGS.find((h) => h.toLowerCase() === name) ?? null;
};

/**
 * The sections of a summary body by heading. Text before the first known
 * heading is dropped; lines under any other heading stay with the section
 * above them.
 */
export function parseSummarySections(body: string): Map<StructuredHeading, string> {
  const out = new Map<StructuredHeading, string[]>();
  let current: string[] | null = null;
  for (const line of body.split("\n")) {
    const h = headingOf(line);
    if (h !== null) {
      current = out.get(h) ?? [];
      out.set(h, current);
    } else {
      current?.push(line);
    }
  }
  return new Map([...out].map(([h, lines]) => [h, lines.join("\n").trim()]));
}

/**
 * The model's summary, made whole: every heading present, in order, and the
 * goal section carried forward byte for byte. A missing section comes back
 * verbatim from the previous summary (or reads "(nothing recorded)" when
 * there is none), so a bad summary can only lose detail from the new
 * material. A goal section that no longer starts with the previous one keeps
 * the previous text and gains only the lines it did not have.
 */
export function completeStructuredSummary(
  raw: string,
  previousSummary: string | null,
): { summary: string; missing: StructuredHeading[] } {
  const got = parseSummarySections(splitSummaryTrailer(raw).body);
  const prev =
    previousSummary === null
      ? new Map<StructuredHeading, string>()
      : parseSummarySections(splitSummaryTrailer(previousSummary).body);
  const missing: StructuredHeading[] = [];
  const blocks: string[] = [];
  for (const h of STRUCTURED_HEADINGS) {
    let text = got.get(h);
    if (text === undefined) {
      missing.push(h);
      text = prev.get(h) ?? EMPTY_SECTION;
    } else if (h === GOAL_HEADING) {
      const old = prev.get(h);
      if (old !== undefined && old !== EMPTY_SECTION && !text.startsWith(old)) {
        const had = new Set(old.split("\n"));
        const added = text.split("\n").filter((l) => l.trim() !== "" && !had.has(l));
        text = [old, ...added].join("\n");
      }
    }
    blocks.push(`## ${h}\n${text}`);
  }
  return { summary: blocks.join("\n\n"), missing };
}

// ---- structured summary: open todo -------------------------------------------

type TodoItem = { text: string; status: "pending" | "in_progress" | "completed" };

const asObject = (v: unknown): Record<string, unknown> | null =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

function snapshotItem(v: unknown): TodoItem | null {
  if (typeof v === "string") return v.trim() ? { text: v.trim(), status: "pending" } : null;
  const o = asObject(v);
  const raw = o?.text ?? o?.content ?? o?.title;
  if (!o || typeof raw !== "string" || raw.trim() === "") return null;
  const done = o.status === "completed" || o.status === "done" || o.done === true;
  const status = done ? "completed" : o.status === "in_progress" ? "in_progress" : "pending";
  return { text: raw.trim(), status };
}

function applyTodoCall(list: TodoItem[], args: unknown): TodoItem[] {
  const a = asObject(args);
  if (!a) return list;
  const full = Array.isArray(a.todos) ? a.todos : Array.isArray(a.items) ? a.items : null;
  if (full) return full.map(snapshotItem).filter((i): i is TodoItem => i !== null);
  const next = list.map((i) => ({ ...i }));
  const done = new Set(
    (Array.isArray(a.completed) ? a.completed : [])
      .filter((c): c is string => typeof c === "string")
      .map((c) => c.toLowerCase().trim()),
  );
  for (const i of next) if (done.has(i.text.toLowerCase().trim())) i.status = "completed";
  for (const t of Array.isArray(a.add) ? a.add : []) {
    if (typeof t === "string" && t.trim() !== "") next.push({ text: t.trim(), status: "pending" });
  }
  return next;
}

/**
 * The open todo items as of the end of the summarized range, as `- [ ] text`
 * (`- [~]` for in progress) lines: the previous summary's open todo, with
 * every `update_todo` call of the range applied in order. A call carrying a
 * whole list (`todos` or `items`) replaces what came before it.
 */
export function openTodoItems(
  summarized: readonly EntryRecord[],
  previousSummary: string | null,
): string[] {
  let list: TodoItem[] = [];
  if (previousSummary !== null) {
    for (const line of splitSummaryTrailer(previousSummary).todo) {
      list.push({
        text: line.slice(6),
        status: line.startsWith("- [~] ") ? "in_progress" : "pending",
      });
    }
  }
  for (const e of summarized) {
    if (e.kind !== ENTRY_KINDS.assistant) continue;
    const p = payloadOf(e, ENTRY_KINDS.assistant);
    if (p.partial === true) continue;
    for (const b of p.message.blocks) {
      if (b.type === "tool_call" && b.name === "update_todo")
        list = applyTodoCall(list, b.arguments);
    }
  }
  return list
    .filter((i) => i.status !== "completed")
    .map((i) => `- [${i.status === "in_progress" ? "~" : " "}] ${i.text}`);
}

// ---- durable state ----------------------------------------------------------

export type CompactionState = {
  /** The live compaction task, or null. */
  running: string | null;
  /** A finished summary waiting for the next turn boundary. */
  ready: { taskId: string; summary: string; firstKeptEntryId: string } | null;
  /**
   * The head a background compaction last failed at (`""` for no head): no
   * new background attempt until the head moves. Blocking ones still try.
   */
  backgroundFailedAt: string | null;
};

const stateSchema: z.ZodType<CompactionState> = z.object({
  running: z.string().nullable(),
  ready: z
    .object({ taskId: z.string(), summary: z.string(), firstKeptEntryId: z.string() })
    .nullable(),
  backgroundFailedAt: z.string().nullable(),
});

export const compactionDoc = defineDoc<CompactionState>({
  kind: "agent.compaction",
  scope: "conversation",
  initial: { running: null, ready: null, backgroundFailedAt: null },
  schema: stateSchema,
});

export type CompactionCause = "background" | "threshold" | "overflow";

export type CompactionInput = {
  /** The run (and round) whose request started the compaction. */
  runId: string;
  round: number;
  cause: CompactionCause;
};

/** A compaction task's `done` value. */
export type CompactionValue =
  | { outcome: "compacted"; firstKeptEntryId: string; summarized: number }
  | { outcome: "skipped" }
  | { outcome: "failed"; error: ModelError };

export type CompactionTask = TaskDefinition<CompactionInput, null>;

/**
 * Append the finished summary, if one waits, as a `compaction` entry (the
 * new context head). Returns the entry id, or null when none waited.
 */
export async function placeReadyCompaction(
  tx: Transaction,
  conversationId: string,
): Promise<string | null> {
  const draft = await tx.doc(compactionDoc, conversationId);
  const ready = draft.value.ready;
  if (ready === null) return null;
  draft.update((d) => {
    d.ready = null;
  });
  const payload: CompactionPayload = {
    summary: ready.summary,
    firstKeptEntryId: ready.firstKeptEntryId,
  };
  return tx.appendEntry({
    conversationId,
    taskId: ready.taskId,
    kind: ENTRY_KINDS.compaction,
    payload: toEntryPayload(ENTRY_KINDS.compaction, payload),
  }).id;
}

const isLive = (t: TaskRecord | null) => t !== null && t.status !== "terminal";

/**
 * The live compaction task of a conversation, or null. Reads the document as
 * of `tx`.
 */
export async function runningCompaction(
  tx: Transaction,
  ops: Pick<TaskOps, "task">,
  conversationId: string,
): Promise<string | null> {
  const running = (await tx.doc(compactionDoc, conversationId)).value.running;
  return running !== null && isLive(ops.task(running)) ? running : null;
}

/**
 * Start a compaction for the conversation, or join the one running. A
 * background request starts nothing while a summary waits to be placed or
 * when the last background attempt failed at the current head. Returns the
 * task to wait on, or null when none was started.
 */
export async function ensureCompaction(
  tx: Transaction,
  ops: TaskOps,
  def: CompactionTask,
  conversationId: string,
  input: CompactionInput,
  headEntryId: string | null,
): Promise<string | null> {
  const draft = await tx.doc(compactionDoc, conversationId);
  const { running, ready, backgroundFailedAt } = draft.value;
  if (running !== null && isLive(ops.task(running))) return running;
  if (
    input.cause === "background" &&
    (ready !== null || backgroundFailedAt === (headEntryId ?? ""))
  ) {
    return null;
  }
  const task = ops.spawn(def, { conversationId, parentTaskId: null, input });
  draft.update((d) => {
    d.running = task.id;
  });
  return task.id;
}

/** How a compaction task ended, for the generation that waited on it. */
export function compactionOutcome(
  rec: TaskRecord | null,
): { ok: true; value: CompactionValue } | { ok: false; error: ModelError } {
  const outcome = rec?.outcome ?? null;
  if (outcome?.type === "done") {
    const value = outcome.value as unknown as CompactionValue;
    if (value.outcome === "failed") return { ok: false, error: value.error };
    return { ok: true, value };
  }
  if (outcome?.type === "aborted") {
    return { ok: false, error: modelError("aborted", `compaction aborted: ${outcome.reason}`) };
  }
  const why =
    outcome?.type === "faulted" ? outcome.message : "the compaction ended without a result";
  return { ok: false, error: modelError("provider", why, { retryable: false }) };
}

// ---- the task ---------------------------------------------------------------

const defaultsOf = new WeakMap<object, CompactionOverrides>();

/** The harness-level settings a compaction definition was made with. */
export function compactionDefaults(def: CompactionTask): CompactionOverrides {
  return defaultsOf.get(def) ?? {};
}

type Scope = TaskScope<null>;
type State = TaskState<CompactionInput, null>;

/**
 * The compaction task kind. `defaults` are the harness-level settings;
 * a conversation's `agent.compaction` overrides them.
 */
export function compactionTaskDefinition(
  deps: AgentDeps,
  defaults: CompactionOverrides = {},
): CompactionTask {
  resolveCompactionSettings(defaults); // validate early
  const def = defineTask<CompactionInput, null>({
    kind: COMPACTION_TASK_KIND,
    lane: runLane,
    phases: { summarize: (task, rt, ctx) => summarize(task, rt, ctx) },
    onAbort: async (task, scope) => {
      await scope.commit(async (tx) => {
        const draft = await tx.doc(compactionDoc, task.conversationId);
        if (draft.value.running === task.id) {
          draft.update((d) => {
            d.running = null;
          });
        }
      });
    },
  });
  defaultsOf.set(def, structuredClone(defaults));
  return def;

  /**
   * Archive the summarized range (`archiveSummarized`, with a store) and, for
   * a structured summary, re-attach the open todo. Both are appended to the
   * summary text. An archive failure is reported and skipped, never fatal.
   */
  async function attachTrailer(
    summary: string,
    plan: CompactionPlan,
    settings: CompactionSettings,
    structured: boolean,
    at: { task: State; ctx: Context; conversation: ToolConversation; runId: string },
  ): Promise<string> {
    const archives = structured ? splitSummaryTrailer(plan.previousSummary ?? "").archives : [];
    if (settings.archiveSummarized && deps.offload) {
      try {
        const stored = await deps.offload.put({
          conversation: at.conversation,
          runId: at.runId,
          toolCallId: at.task.id,
          toolName: "compaction",
          text: renderArchive(plan),
          kind: "history",
          signal: at.ctx.signal,
        });
        if (typeof stored?.path !== "string" || stored.path.length === 0) {
          throw new TypeError("offload store answered without a path");
        }
        archives.push(stored.path);
      } catch (err) {
        if (err instanceof LeaseLost) throw err;
        at.ctx.throwIfCancelled();
        deps.report(err, {
          during: "offload",
          taskId: at.task.id,
          conversationId: at.task.conversationId,
        });
      }
    }
    const todo = structured ? openTodoItems(plan.summarized, plan.previousSummary) : [];
    return withTrailer(summary, { archives, todo });
  }

  async function summarize(task: State, rt: Scope, ctx: Context): Promise<Committed> {
    const conversationId = task.conversationId;
    const { runId, round, cause } = task.input;
    const { conversation, agent } = await loadConversation(deps, conversationId);
    const settings = resolveCompactionSettings(defaults, agent.compaction);
    const entries = await listAllEntries(deps.session, conversationId);
    const plan = planCompaction(entries, settings.keepRecentTokens);
    if (plan === null) {
      return finish(rt, task, null, { outcome: "skipped" });
    }

    const structured = settings.structuredSummary;
    const input: ModelInput = {
      messages: [
        {
          role: "system",
          text: structured ? STRUCTURED_COMPACTION_PROMPT : COMPACTION_SYSTEM_PROMPT,
        },
        {
          role: "user",
          content: structured ? renderStructuredTranscript(plan) : renderTranscript(plan),
        },
      ],
      cache: false,
    };
    const stream = deps.models.resolveAndStream(
      ctx,
      (c) =>
        deps.policy.resolveModelRequest({
          ctx: c,
          purpose: "compaction",
          conversation,
          agent,
          runId,
          round,
        }),
      input,
    );
    const terminal = await stream.result();
    if (terminal.type === "error") {
      ctx.throwIfCancelled();
      const spend =
        terminal.partial.provider === ""
          ? null
          : {
              provider: terminal.partial.provider,
              model: terminal.partial.model,
              usage: terminal.usage,
            };
      return finish(rt, task, spend, { outcome: "failed", error: terminal.error }, (d) => {
        if (cause === "background") d.backgroundFailedAt = plan.headEntryId ?? "";
      });
    }
    const message = terminal.message;
    const spend = { provider: message.provider, model: message.model, usage: terminal.usage };
    const raw = assistantText(message).trim();
    let summary = raw;
    let quality: CompactionQuality | undefined;
    if (structured && raw.length > 0) {
      const done = completeStructuredSummary(raw, plan.previousSummary);
      summary = done.summary;
      const summarizedChars = plan.summarized.reduce((n, e) => n + entryChars(e), 0);
      quality = {
        summaryChars: summary.length,
        summarizedChars,
        ratio: summarizedChars > 0 ? summary.length / summarizedChars : null,
        missingHeadings: done.missing,
      };
    }
    await deps.hooks.afterCompaction(
      {
        conversation,
        agent,
        runId,
        round,
        taskId: task.id,
        message,
        usage: terminal.usage,
        ...(quality ? { quality } : {}),
      },
      task.id,
    );
    if (
      quality &&
      (quality.missingHeadings.length > 0 || (quality.ratio ?? 1) < MIN_SUMMARY_RATIO)
    ) {
      deps.report(
        new Error(
          `compaction summary quality: ratio ${quality.ratio?.toFixed(4) ?? "n/a"}, missing headings: ${quality.missingHeadings.join(", ") || "none"}`,
        ),
        { during: "compaction_quality", taskId: task.id, conversationId },
      );
    }
    if (summary.length === 0) {
      const error = modelError("provider", "the summary request returned no text", {
        retryable: false,
      });
      return finish(rt, task, spend, { outcome: "failed", error }, (d) => {
        if (cause === "background") d.backgroundFailedAt = plan.headEntryId ?? "";
      });
    }
    summary = await attachTrailer(summary, plan, settings, structured, {
      task,
      ctx,
      conversation,
      runId,
    });
    return finish(
      rt,
      task,
      spend,
      {
        outcome: "compacted",
        firstKeptEntryId: plan.firstKeptEntryId,
        summarized: plan.summarized.length,
      },
      (d) => {
        d.ready = { taskId: task.id, summary, firstKeptEntryId: plan.firstKeptEntryId };
        d.backgroundFailedAt = null;
      },
    );
  }
}

type Spend = { provider: string; model: string; usage: Usage } | null;

function finish(
  rt: Scope,
  task: State,
  spend: Spend,
  value: CompactionValue,
  patch?: (d: CompactionState) => void,
): Promise<Committed> {
  return rt.commit(async (tx) => {
    if (spend !== null) {
      recordUsage(
        await tx.doc(usageDoc, task.conversationId),
        spend.provider,
        spend.model,
        spend.usage,
        "compaction",
      );
    }
    const draft = await tx.doc(compactionDoc, task.conversationId);
    draft.update((d) => {
      if (d.running === task.id) d.running = null;
      patch?.(d);
    });
    return done(value as unknown as JsonValue);
  });
}

// ---- wiring -----------------------------------------------------------------

const compactionOf = new WeakMap<object, CompactionTask>();

/** Remember which compaction kind a generation definition spawns. */
export function bindCompactionTask(generation: object, compaction: CompactionTask): void {
  compactionOf.set(generation, compaction);
}

/**
 * The compaction task definition a generation definition spawns, to register
 * with the task runtime next to it.
 */
export function compactionTaskOf(
  generation: TaskDefinition<never, never> | object,
): CompactionTask {
  const def = compactionOf.get(generation);
  if (!def) throw new TypeError("not a generation task definition");
  return def;
}
