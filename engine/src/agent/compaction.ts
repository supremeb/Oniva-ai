/**
 * Compaction (R14): keep a conversation's context inside the model window.
 *
 * Token estimate: the usage the newest reply reported (its prompt plus its
 * output) plus a characters/4 heuristic for the entries written after it.
 * When no reply was reported since the newest `compaction` entry, the whole
 * built context is estimated by characters.
 *
 * Thresholds, from the window (`contextWindow`, else `contextWindows[model]`,
 * else {@link DEFAULT_CONTEXT_WINDOW}):
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
import {
  assistantText,
  buildContext,
  type CompactionPayload,
  ENTRY_KINDS,
  payloadOf,
  toEntryPayload,
} from "./context-builder.js";
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
});

const CHARS_PER_TOKEN = 4;
/** Longest single item (message, tool result) quoted into a summary request. */
const MAX_ITEM_CHARS = 8_000;

export const COMPACTION_SYSTEM_PROMPT = [
  "You compress the earlier part of a conversation between a user and an AI assistant that uses tools.",
  "Write a summary the assistant can continue from without the original messages.",
  "Keep: the user's goals and instructions, decisions made, facts learned, names, identifiers, paths and numbers that may matter later, tool calls made and what they returned, and any work still open.",
  "Drop pleasantries and repetition. If a previous summary is given, fold it in.",
  "Reply with the summary only.",
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
): CompactionThresholds {
  const window =
    settings.contextWindow ??
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

/** The summary request's user message: the previous summary and the entries, as text. */
export function renderTranscript(plan: CompactionPlan): string {
  const parts: string[] = [];
  if (plan.previousSummary !== null) {
    parts.push(`<previous_summary>\n${plan.previousSummary}\n</previous_summary>`);
  }
  const lines: string[] = [];
  for (const e of plan.summarized) {
    if (e.kind === ENTRY_KINDS.user) {
      lines.push(`[user]\n${clip(plain(payloadOf(e, ENTRY_KINDS.user).content))}`);
    } else if (e.kind === ENTRY_KINDS.assistant) {
      const message = payloadOf(e, ENTRY_KINDS.assistant).message;
      const text = assistantText(message);
      if (text.length > 0) lines.push(`[assistant]\n${clip(text)}`);
      for (const b of message.blocks) {
        // A partial reply's tool calls were never run.
        if (b.type === "tool_call" && payloadOf(e, ENTRY_KINDS.assistant).partial !== true) {
          lines.push(`[assistant called tool ${b.name}]\n${clip(JSON.stringify(b.arguments))}`);
        }
      }
    } else {
      const r = payloadOf(e, ENTRY_KINDS.toolResult);
      const flag = r.isError ? ` (${r.status})` : "";
      lines.push(`[result of ${r.toolName}${flag}]\n${clip(plain(r.content))}`);
    }
  }
  parts.push(`<conversation>\n${lines.join("\n\n")}\n</conversation>`);
  parts.push("Summarize the conversation above.");
  return parts.join("\n\n");
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

    const input: ModelInput = {
      messages: [
        { role: "system", text: COMPACTION_SYSTEM_PROMPT },
        { role: "user", content: renderTranscript(plan) },
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
    await deps.hooks.afterCompaction(
      { conversation, agent, runId, round, taskId: task.id, message, usage: terminal.usage },
      task.id,
    );
    const summary = assistantText(message).trim();
    if (summary.length === 0) {
      const error = modelError("provider", "the summary request returned no text", {
        retryable: false,
      });
      return finish(rt, task, spend, { outcome: "failed", error }, (d) => {
        if (cause === "background") d.backgroundFailedAt = plan.headEntryId ?? "";
      });
    }
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
