/**
 * Conversation entries and the model context built from them.
 *
 * Entry kinds written by the agent loop:
 * - `system`: prompt and tool set ({@link SystemPayload}), appended on change;
 * - `user`: an input ({@link UserPayload});
 * - `assistant`: a completed model reply ({@link AssistantPayload});
 * - `tool_result`: the outcome of one tool call ({@link ToolResultPayload});
 * - `run_error`: why a run failed ({@link RunErrorPayload}); not sent to the model;
 * - `compaction`: a summary of older context ({@link CompactionPayload}).
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
import type { SystemPayload } from "./prompt.js";

export const ENTRY_KINDS = Object.freeze({
  system: "system",
  user: "user",
  assistant: "assistant",
  toolResult: "tool_result",
  runError: "run_error",
  compaction: "compaction",
} as const);

export type UserPayload = {
  submissionId: string;
  content: string | TextContent[];
};

export type AssistantPayload = {
  runId: string;
  round: number;
  message: AssistantMessage;
  usage: Usage;
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
  /** `authorizeTool` asked for approval; durable approvals are not wired in yet. */
  | "approval_unsupported";

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

export interface BuiltContext {
  /** Newest system payload, or null when none was appended yet. */
  system: SystemPayload | null;
  messages: Message[];
  /** The entry the context starts from (newest compaction), or null. */
  headEntryId: string | null;
}

export const SUMMARY_PREFIX = "Summary of the earlier conversation:\n\n";

export function buildContext(entries: readonly EntryRecord[]): BuiltContext {
  let system: SystemPayload | null = null;
  let head = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i] as EntryRecord;
    if (system === null && e.kind === ENTRY_KINDS.system)
      system = e.payload as unknown as SystemPayload;
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
    const c = compaction.payload as unknown as CompactionPayload;
    messages.push({ role: "user", content: SUMMARY_PREFIX + c.summary });
    const keptAt =
      c.firstKeptEntryId === null ? -1 : entries.findIndex((e) => e.id === c.firstKeptEntryId);
    body = keptAt >= 0 && keptAt < head ? entries.slice(keptAt) : entries.slice(head + 1);
  }

  let open: Map<string, string> = new Map(); // tool call id -> name, awaiting results
  const closeOpen = () => {
    for (const [id, name] of open) messages.push(abortedResult(id, name));
    open = new Map();
  };
  for (const e of body) {
    switch (e.kind) {
      case ENTRY_KINDS.user: {
        closeOpen();
        messages.push({ role: "user", content: (e.payload as unknown as UserPayload).content });
        break;
      }
      case ENTRY_KINDS.assistant: {
        closeOpen();
        const message = (e.payload as unknown as AssistantPayload).message;
        messages.push(message);
        for (const b of message.blocks) if (b.type === "tool_call") open.set(b.id, b.name);
        break;
      }
      case ENTRY_KINDS.toolResult: {
        const p = e.payload as unknown as ToolResultPayload;
        if (!open.has(p.toolCallId)) break; // orphan (its call was compacted away)
        open.delete(p.toolCallId);
        messages.push(toolResultMessage(p));
        break;
      }
      default:
        break;
    }
  }
  closeOpen();
  return { system, messages, headEntryId: head >= 0 ? (entries[head] as EntryRecord).id : null };
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
