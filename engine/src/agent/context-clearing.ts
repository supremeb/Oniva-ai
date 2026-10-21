/**
 * Tier 1 context reduction: view-time clearing of stale tool results.
 *
 * Stored entries are never changed. A conversation document (`agent.context`)
 * holds a watermark, the id of the newest `tool_result` entry that is cleared;
 * `buildContext` renders every result at or before it as a fixed stub
 * (`applyClearing`, via `clearStub`). Tool calls are untouched, so each call
 * still has its result message.
 *
 * The watermark only moves forward, and only when {@link planClearing} says a
 * worthwhile amount would be freed, so between advances the cleared prefix is
 * byte-stable and the provider's prefix cache survives. The generation
 * `request` phase writes the advance before it builds the context, so a rerun
 * after a crash finds it and does nothing twice.
 */
import { z } from "zod";
import type { TextContent } from "../models/types.js";
import { defineDoc } from "../session/documents.js";
import type { EntryRecord } from "../storage/types.js";
import {
  type BuildContextOptions,
  contextBodyStart,
  ENTRY_KINDS,
  payloadOf,
  type ToolResultPayload,
} from "./context-builder.js";
import type { CompactionSettings } from "./prompt.js";

export type ContextState = {
  /** Newest cleared `tool_result` entry, or null while nothing is cleared. */
  clearedThroughEntryId: string | null;
  /** Estimated tokens cleared so far (summed over the advances). */
  clearedTokens: number;
};

const stateSchema: z.ZodType<ContextState> = z.object({
  clearedThroughEntryId: z.string().nullable(),
  clearedTokens: z.number().int().nonnegative(),
});

export const contextDoc = defineDoc<ContextState>({
  kind: "agent.context",
  scope: "conversation",
  initial: { clearedThroughEntryId: null, clearedTokens: 0 },
  schema: stateSchema,
});

const CHARS_PER_TOKEN = 4;

function contentChars(content: string | TextContent[]): number {
  return typeof content === "string"
    ? content.length
    : content.reduce((n, part) => n + part.text.length, 0);
}

/** Estimated tokens of a tool result's content. */
export function resultTokens(p: ToolResultPayload): number {
  return Math.ceil(contentChars(p.content) / CHARS_PER_TOKEN);
}

/** The path Tier 0 saved the full output to (`details.offloaded.path`), if any. */
function offloadedPath(details: unknown): string | null {
  if (typeof details !== "object" || details === null || Array.isArray(details)) return null;
  const off = (details as { offloaded?: unknown }).offloaded;
  if (typeof off !== "object" || off === null || Array.isArray(off)) return null;
  const path = (off as { path?: unknown }).path;
  return typeof path === "string" && path.length > 0 ? path : null;
}

/**
 * The text that stands for a cleared result. Fixed wording; the path clause
 * names the saved file and is dropped when the output was not saved.
 */
export function clearStub(p: ToolResultPayload): string {
  const head = `[cleared: ${p.toolName} result, ~${resultTokens(p)} tokens. Re-run the tool`;
  const path = offloadedPath(p.details);
  return path === null ? `${head}.]` : `${head}, or read ${path} if it was saved.]`;
}

/** Whether these settings have clearing on. */
export function clearingEnabled(settings: Pick<CompactionSettings, "clearTriggerTokens">): boolean {
  return settings.clearTriggerTokens > 0;
}

/**
 * The options that make `buildContext` apply the stored watermark: none when
 * clearing is off (turning it off restores verbatim results) or nothing is cleared.
 */
export function applyClearing(
  settings: Pick<CompactionSettings, "clearTriggerTokens" | "excludeTools">,
  state: ContextState,
): BuildContextOptions {
  if (!clearingEnabled(settings) || state.clearedThroughEntryId === null) return {};
  return { watermark: state.clearedThroughEntryId, excludeTools: settings.excludeTools };
}

export interface ClearingPlan {
  /** The new watermark: the newest entry that becomes cleared. */
  watermark: string;
  /** Estimated tokens freed by this advance. */
  freedTokens: number;
}

/**
 * Decide whether to advance the watermark. Null unless clearing is on, the
 * context `estimate` is at or above `clearTriggerTokens`, and the results that
 * would newly be cleared (in context, after the current watermark, not in
 * `excludeTools`, older than the newest `keepToolResults`) total at least
 * `clearAtLeastTokens`.
 */
export function planClearing(
  entries: readonly EntryRecord[],
  settings: Pick<
    CompactionSettings,
    "clearTriggerTokens" | "clearAtLeastTokens" | "keepToolResults" | "excludeTools"
  >,
  estimate: number,
  state: ContextState,
): ClearingPlan | null {
  if (!clearingEnabled(settings) || estimate < settings.clearTriggerTokens) return null;
  let head = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i]?.kind === ENTRY_KINDS.compaction) {
      head = i;
      break;
    }
  }
  const start = contextBodyStart(entries, head);
  const results: number[] = [];
  for (let i = start; i < entries.length; i++) {
    if (entries[i]?.kind === ENTRY_KINDS.toolResult) results.push(i);
  }
  const marked =
    state.clearedThroughEntryId === null
      ? -1
      : entries.findIndex((e) => e.id === state.clearedThroughEntryId);
  const excluded = new Set(settings.excludeTools);
  const eligible = results.slice(0, Math.max(0, results.length - settings.keepToolResults));
  let freed = 0;
  let newest = -1;
  for (const i of eligible) {
    if (i <= marked) continue;
    const p = payloadOf(entries[i] as EntryRecord, ENTRY_KINDS.toolResult);
    if (excluded.has(p.toolName)) continue;
    freed += resultTokens(p);
    newest = i;
  }
  if (newest < 0 || freed < settings.clearAtLeastTokens) return null;
  return { watermark: (entries[newest] as EntryRecord).id, freedTokens: freed };
}
