/**
 * Context for a subagent's first entry (R17).
 *
 * A child normally sees only its task prompt. Two opt-in things add to it:
 * the per-call `context` (a brief and workspace file pointers) and, in `fork`
 * mode, a bounded narrative of the parent's conversation. This module renders
 * those parts as text and composes them; it is pure, so `subagents.ts` only
 * decides when to use it.
 *
 * The first entry is, in order and separated by blank lines: the static
 * `contextPreamble`, the fork block, the brief, the file pointers, then the
 * task. With none of the parts present the entry is the task prompt itself,
 * byte for byte.
 *
 * The fork block is written in the third person under a fixed header so the
 * child does not mistake the parent's tool calls for its own. It holds the
 * parent's newest compaction summary (its body, without the archive/todo
 * trailer) and the last {@link FORK_RECENT_ENTRIES} context entries: `user` and
 * `assistant` text and tool names only. A tool result's body is never copied,
 * only a one-line size note, which also limits what untrusted content is
 * carried over. The block is capped at {@link FORK_BLOCK_MAX_TOKENS} tokens.
 */
import type { TextContent } from "../models/types.js";
import type { EntryRecord } from "../storage/types.js";
import { splitSummaryTrailer } from "./compaction.js";
import { assistantText, contextBodyStart, ENTRY_KINDS, payloadOf } from "./context-builder.js";

const CHARS_PER_TOKEN = 4;

/** Context entries (user, assistant, tool result) a fork block narrates. */
export const FORK_RECENT_ENTRIES = 6;
/** Token cap of a whole fork block. */
export const FORK_BLOCK_MAX_TOKENS = 2000;
/** Characters of the parent's summary kept in a fork block. */
export const FORK_SUMMARY_MAX_CHARS = 3200;
/** Characters of one user or assistant message kept in a fork block. */
export const FORK_ITEM_MAX_CHARS = 700;

/** Longest per-call `context.brief` and static `contextPreamble`, in characters. */
export const CONTEXT_BRIEF_MAX_CHARS = 4000;
export const CONTEXT_PREAMBLE_MAX_CHARS = 4000;
/** Most `context.files` paths, and the longest one. */
export const CONTEXT_FILES_MAX = 20;
export const CONTEXT_FILE_MAX_CHARS = 512;

export const FORK_HEADER =
  "[For context] Background from the parent agent's conversation, written in the third person. The parent's tool calls and results below belong to the parent, not to you: do not repeat them or assume you ran them. Use this only to understand the task that follows.";

const OMITTED = "[... omitted]";

const clip = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, Math.max(0, max - OMITTED.length - 1))} ${OMITTED}`;

const plain = (content: string | TextContent[]): string =>
  typeof content === "string" ? content : content.map((c) => c.text).join("");

const oneLine = (text: string): string => text.replace(/\s+/g, " ").trim();

const contentSize = (content: string | TextContent[]): number => plain(content).length;

const CONTEXT_KINDS: ReadonlySet<string> = new Set([
  ENTRY_KINDS.user,
  ENTRY_KINDS.assistant,
  ENTRY_KINDS.toolResult,
]);

export interface ForkBlockOptions {
  /** Entries to narrate (default {@link FORK_RECENT_ENTRIES}). */
  recent?: number;
  /** The parent conversation has handled content the host does not trust. */
  tainted?: boolean;
}

/**
 * The fork block for a parent conversation's `entries` (oldest first), or null
 * when there is nothing to tell (no summary and no context entries).
 */
export function renderForkBlock(
  entries: readonly EntryRecord[],
  options: ForkBlockOptions = {},
): string | null {
  const recent = options.recent ?? FORK_RECENT_ENTRIES;
  let head = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i]?.kind === ENTRY_KINDS.compaction) {
      head = i;
      break;
    }
  }
  const sections: string[] = [];
  if (head >= 0) {
    const { summary } = payloadOf(entries[head] as EntryRecord, ENTRY_KINDS.compaction);
    const body = splitSummaryTrailer(summary).body;
    if (body.length > 0) {
      sections.push(
        `The parent's summary of its earlier conversation:\n${clip(body, FORK_SUMMARY_MAX_CHARS)}`,
      );
    }
  }
  const bodyStart = contextBodyStart(entries, head);
  const tail = entries
    .slice(bodyStart)
    .filter((e) => CONTEXT_KINDS.has(e.kind))
    .slice(-Math.max(0, recent));
  const lines: string[] = [];
  for (const e of tail) {
    if (e.kind === ENTRY_KINDS.user) {
      const text = oneLine(plain(payloadOf(e, ENTRY_KINDS.user).content));
      lines.push(`- The user said: ${clip(text, FORK_ITEM_MAX_CHARS)}`);
    } else if (e.kind === ENTRY_KINDS.assistant) {
      const p = payloadOf(e, ENTRY_KINDS.assistant);
      const text = oneLine(assistantText(p.message));
      if (text.length > 0) lines.push(`- The parent replied: ${clip(text, FORK_ITEM_MAX_CHARS)}`);
      if (p.partial !== true) {
        for (const b of p.message.blocks) {
          if (b.type === "tool_call") lines.push(`- The parent called the tool ${b.name}.`);
        }
      }
    } else {
      const r = payloadOf(e, ENTRY_KINDS.toolResult);
      const flag = r.isError ? `, ${r.status}` : "";
      lines.push(
        `- The tool ${r.toolName} returned a result to the parent (${contentSize(r.content)} characters${flag}, not copied).`,
      );
    }
  }
  if (lines.length > 0) sections.push(`The parent's most recent turns:\n${lines.join("\n")}`);
  if (sections.length === 0) return null;
  if (options.tainted === true) {
    sections.push(
      "Note: the parent has handled content the host does not trust, and this conversation carries the same restriction.",
    );
  }
  const block = `${FORK_HEADER}\n\n${sections.join("\n\n")}`;
  return clip(block, FORK_BLOCK_MAX_TOKENS * CHARS_PER_TOKEN);
}

export interface ChildPromptParts {
  /** The static `contextPreamble` from the override. */
  preamble?: string | undefined;
  /** {@link renderForkBlock} output. */
  fork?: string | null | undefined;
  /** The per-call `context.brief`. */
  brief?: string | undefined;
  /** The per-call `context.files`. */
  files?: readonly string[] | undefined;
}

const TASK_HEADER = "[Task]";

/**
 * The child's first user entry. Without any part it is `prompt` unchanged;
 * otherwise the parts in order, then the task under a `[Task]` header.
 */
export function composeChildPrompt(prompt: string, parts: ChildPromptParts): string {
  const out: string[] = [];
  if (parts.preamble !== undefined && parts.preamble.length > 0) out.push(parts.preamble);
  if (parts.fork !== undefined && parts.fork !== null && parts.fork.length > 0) {
    out.push(parts.fork);
  }
  if (parts.brief !== undefined && parts.brief.length > 0) {
    out.push(`[Brief from the parent]\n${parts.brief}`);
  }
  if (parts.files !== undefined && parts.files.length > 0) {
    out.push(
      `[Files to read first, in the conversation workspace]\n${parts.files.map((f) => `- ${f}`).join("\n")}`,
    );
  }
  if (out.length === 0) return prompt;
  out.push(`${TASK_HEADER}\n${prompt}`);
  return out.join("\n\n");
}

/** Why a per-call `context` is too large, or null. Oversize input is rejected, never cut. */
export function contextProblem(
  context: { brief?: string | undefined; files?: readonly string[] | undefined } | undefined,
): string | null {
  if (context === undefined) return null;
  if (context.brief !== undefined && context.brief.length > CONTEXT_BRIEF_MAX_CHARS) {
    return `context.brief: at most ${CONTEXT_BRIEF_MAX_CHARS} characters`;
  }
  if (context.files !== undefined) {
    if (context.files.length > CONTEXT_FILES_MAX) {
      return `context.files: at most ${CONTEXT_FILES_MAX} paths`;
    }
    if (context.files.some((f) => f.length === 0 || f.length > CONTEXT_FILE_MAX_CHARS)) {
      return `context.files: each path is 1-${CONTEXT_FILE_MAX_CHARS} characters`;
    }
  }
  return null;
}
