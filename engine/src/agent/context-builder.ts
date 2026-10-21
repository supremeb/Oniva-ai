/**
 * Conversation entries and the model context built from them.
 *
 * Entry kinds written by the agent loop:
 * - `system`: prompt and tool set ({@link SystemPayload}), appended on change;
 * - `user`: an input ({@link UserPayload});
 * - `assistant`: a completed model reply ({@link AssistantPayload}), or with
 *   `partial: true` the text streamed before a failed generation (written just
 *   before that run's `run_error`; only its text is sent to the model);
 * - `tool_result`: the outcome of one tool call ({@link ToolResultPayload});
 * - `run_error`: why a run failed ({@link RunErrorPayload}); not sent to the model;
 * - `compaction`: a summary of older context ({@link CompactionPayload});
 * - `run_resumed`: display only ({@link RunResumedPayload}); a run (or one of its
 *   subagents) was in flight when the worker restarted. Written to the root
 *   conversation, once per restart, and not sent to the model.
 * Unknown kinds (display-only writes, host annotations) are ignored here.
 *
 * The context starts at the newest `compaction` entry (its summary, then the
 * entries it keeps verbatim), always led by the newest `system` entry. Tool
 * calls left without a result (a run aborted mid-round) get a synthesized
 * `aborted` result so the context stays well formed.
 */
import type { JsonValue } from "../core/json.js";
import type {
  AssistantMessage,
  Message,
  ModelError,
  TextContent,
  ToolResultMessage,
  Usage,
} from "../models/types.js";
import type { EntryRecord } from "../storage/types.js";
import type { ToolResultApproval } from "./approvals.js";
import { clearStub } from "./context-clearing.js";
import type { SystemPayload } from "./prompt.js";

export const ENTRY_KINDS = Object.freeze({
  system: "system",
  user: "user",
  assistant: "assistant",
  toolResult: "tool_result",
  runError: "run_error",
  compaction: "compaction",
  /** Display-only: the run was resumed after a restart. Never in model context. */
  runResumed: "run_resumed",
} as const);

export type UserPayload = {
  submissionId: string;
  content: string | TextContent[];
  /**
   * The submission's `requestId` (the caller's id, or the submission id when
   * none was given), so a client can match its optimistic message to this
   * entry. Absent on entries written before this field existed.
   */
  requestId?: string;
};

export type AssistantPayload = {
  runId: string;
  round: number;
  message: AssistantMessage;
  usage: Usage;
  /**
   * True for the text streamed before a generation failed, kept so clients
   * can show it. Its blocks are as streamed (tool calls may be incomplete);
   * the model sees only its text ({@link contextMessage}).
   */
  partial?: boolean;
};

export type ToolResultStatus =
  | "ok"
  | "error"
  | "blocked"
  | "invalid_arguments"
  | "unknown_tool"
  | "limit_exceeded"
  | "interrupted"
  | "aborted"
  /** A human denied the approval the call required. */
  | "denied"
  /** The approval the call required expired before a decision. */
  | "approval_expired"
  /** The question the call asked a human expired before an answer. */
  | "question_expired"
  /** The call ran past its time limit. */
  | "timeout"
  /** The process running the call lost the session lease before it reported. */
  | "lease_lost";

export type ToolResultPayload = {
  runId: string;
  toolCallId: string;
  toolName: string;
  status: ToolResultStatus;
  content: string | TextContent[];
  isError: boolean;
  details: JsonValue;
  terminate: boolean;
  /** Execution attempts (0 when the tool never ran). */
  attempts: number;
  /** How the approval the call required ended (absent when none was required). */
  approval?: ToolResultApproval;
};

export type RunErrorPayload = {
  runId: string;
  error: ModelError;
};

export type CompactionPayload = {
  summary: string;
  /** First entry kept verbatim after the summary, or null for "everything after this entry". */
  firstKeptEntryId: string | null;
};

export type RunResumedPayload = {
  runId: string;
  /** Epoch ms of the recovery commit. */
  at: number;
};

export type EntryKind = (typeof ENTRY_KINDS)[keyof typeof ENTRY_KINDS];

/** The payload type of each entry kind the agent loop writes. */
export interface EntryPayloads {
  system: SystemPayload;
  user: UserPayload;
  assistant: AssistantPayload;
  tool_result: ToolResultPayload;
  run_error: RunErrorPayload;
  compaction: CompactionPayload;
  run_resumed: RunResumedPayload;
}

/**
 * The typed payload of an entry of `kind`. Entries are only written through
 * {@link toEntryPayload}, so the kind fixes the payload shape; a kind mismatch
 * is a caller bug and throws.
 */
export function payloadOf<K extends EntryKind>(
  entry: Pick<EntryRecord, "kind" | "payload">,
  kind: K,
): EntryPayloads[K] {
  if (entry.kind !== kind) throw new TypeError(`expected a ${kind} entry, got ${entry.kind}`);
  return entry.payload as unknown as EntryPayloads[K];
}

/** A typed payload for an entry of `kind`, as stored JSON. */
export function toEntryPayload<K extends EntryKind>(
  _kind: K,
  payload: EntryPayloads[K],
): JsonValue {
  return payload as unknown as JsonValue;
}

export interface BuiltContext {
  /** Newest system payload, or null when none was appended yet. */
  system: SystemPayload | null;
  messages: Message[];
  /** The entry the context starts from (newest compaction), or null. */
  headEntryId: string | null;
}

export const SUMMARY_PREFIX = "Summary of the earlier conversation:\n\n";

/** Options of {@link buildContext}. Absent or empty: the context is built verbatim. */
export interface BuildContextOptions {
  /**
   * Tier 1 clearing: the id of the newest `tool_result` entry whose content is
   * replaced by a stub (`context-clearing.ts`). Results of `excludeTools` stay
   * verbatim. An id that is not in `entries` clears nothing.
   */
  watermark?: string | null;
  excludeTools?: readonly string[];
}

/**
 * Index of the first entry of the context body: after the newest compaction
 * (`head`), the entry it keeps verbatim from, else the one after it.
 */
export function contextBodyStart(entries: readonly EntryRecord[], head: number): number {
  if (head < 0) return 0;
  const c = payloadOf(entries[head] as EntryRecord, ENTRY_KINDS.compaction);
  const keptAt =
    c.firstKeptEntryId === null ? -1 : entries.findIndex((e) => e.id === c.firstKeptEntryId);
  return keptAt >= 0 && keptAt < head ? keptAt : head + 1;
}

export function buildContext(
  entries: readonly EntryRecord[],
  options: BuildContextOptions = {},
): BuiltContext {
  let system: SystemPayload | null = null;
  let head = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i] as EntryRecord;
    if (system === null && e.kind === ENTRY_KINDS.system) system = payloadOf(e, ENTRY_KINDS.system);
    if (head < 0 && e.kind === ENTRY_KINDS.compaction) head = i;
    if (system !== null && head >= 0) break;
  }

  const messages: Message[] = [];
  if (system !== null && system.text.length > 0) {
    messages.push({ role: "system", text: system.text });
  }

  let body: readonly EntryRecord[] = entries;
  if (head >= 0) {
    const compaction = entries[head] as EntryRecord;
    const c = payloadOf(compaction, ENTRY_KINDS.compaction);
    messages.push({ role: "user", content: SUMMARY_PREFIX + c.summary });
    body = entries.slice(contextBodyStart(entries, head));
  }
  const watermarkAt =
    options.watermark == null ? -1 : body.findIndex((e) => e.id === options.watermark);
  const excluded = new Set(options.excludeTools ?? []);

  let open: Map<string, string> = new Map(); // tool call id -> name, awaiting results
  const closeOpen = () => {
    for (const [id, name] of open) messages.push(abortedResult(id, name));
    open = new Map();
  };
  for (const [i, e] of body.entries()) {
    switch (e.kind) {
      case ENTRY_KINDS.user: {
        closeOpen();
        messages.push({ role: "user", content: payloadOf(e, ENTRY_KINDS.user).content });
        break;
      }
      case ENTRY_KINDS.assistant: {
        closeOpen();
        const payload = payloadOf(e, ENTRY_KINDS.assistant);
        const message =
          payload.partial === true ? partialContextMessage(payload.message) : payload.message;
        if (message === null) break;
        messages.push(message);
        for (const b of message.blocks) if (b.type === "tool_call") open.set(b.id, b.name);
        break;
      }
      case ENTRY_KINDS.toolResult: {
        const p = payloadOf(e, ENTRY_KINDS.toolResult);
        if (!open.has(p.toolCallId)) break; // orphan (its call was compacted away)
        open.delete(p.toolCallId);
        messages.push(
          i <= watermarkAt && !excluded.has(p.toolName)
            ? clearedToolResultMessage(p)
            : toolResultMessage(p),
        );
        break;
      }
      default:
        break;
    }
  }
  closeOpen();
  return { system, messages, headEntryId: head >= 0 ? (entries[head] as EntryRecord).id : null };
}

/**
 * A partial assistant reply as the model sees it on the next turn: its
 * non-empty text blocks only. Incomplete tool calls (no result will ever come)
 * and thinking blocks (cut off, so unsigned) are dropped. Null when no text remains.
 */
function partialContextMessage(message: AssistantMessage): AssistantMessage | null {
  const blocks = message.blocks.filter((b) => b.type === "text" && b.text.length > 0);
  if (blocks.length === 0) return null;
  return { ...message, blocks };
}

export function toolResultMessage(p: ToolResultPayload): ToolResultMessage {
  return {
    role: "tool_result",
    toolCallId: p.toolCallId,
    toolName: p.toolName,
    content: p.content,
    isError: p.isError,
  };
}

/** A tool result as the model sees it once Tier 1 clearing has reached it. */
function clearedToolResultMessage(p: ToolResultPayload): ToolResultMessage {
  return { ...toolResultMessage(p), content: clearStub(p) };
}

function abortedResult(toolCallId: string, toolName: string): ToolResultMessage {
  return {
    role: "tool_result",
    toolCallId,
    toolName,
    content: "The tool call was aborted before it produced a result.",
    isError: true,
  };
}

/** Plain text of an assistant message's text blocks. */
export function assistantText(message: AssistantMessage): string {
  return message.blocks
    .filter((b) => b.type === "text")
    .map((b) => (b as { text: string }).text)
    .join("");
}
