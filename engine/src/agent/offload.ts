/**
 * Tier 0 of context reduction (R11): offload at write time.
 *
 * A tool result whose estimated size passes `offloadTokens` is stored out of
 * context through the host's {@link OffloadStore}. The `tool_result` entry
 * keeps a short preview (the first lines) and a pointer to the stored object,
 * and `details.offloaded` records where it went. The model reads more with
 * the tool the pointer names (`read_file` with `offset` and `limit`).
 *
 * Best effort: any store failure keeps the original (capped) result and
 * reports a warning; it never fails the tool call. Two failures are not
 * swallowed because they mean this process must stop, not carry on: a lost
 * lease and a cancelled call.
 *
 * Replay: `put` is idempotent by `runId:toolCallId`. A crash between the put
 * and the commit of the result re-runs the call's `finish`; the second put
 * finds the object and answers the same path.
 *
 * Inert by default: with `offloadTokens` 0 (the default), or without a store,
 * nothing happens and the result is returned as it is.
 */
import { LeaseLost } from "../core/errors.js";
import type { JsonObject } from "../core/json.js";
import type { ToolConversation, ToolResult } from "../tools/registry.js";
import type { CompactionSettings } from "./prompt.js";

/** What the engine asks the store to keep. */
export interface OffloadPutRequest {
  /** The conversation that made the call (a subagent's child keeps its own id and `rootId`). */
  conversation: ToolConversation;
  runId: string;
  toolCallId: string;
  toolName: string;
  /** The full result text. */
  text: string;
  /** Cancelled with the tool call; a store should stop when it aborts. */
  signal?: AbortSignal;
}

/** Where the store kept the text, in the namespace `read_file` resolves. */
export interface OffloadPutResult {
  path: string;
  bytes: number;
}

/**
 * Host-supplied storage for offloaded results. `put` must be idempotent by
 * `runId:toolCallId`: a repeat answers the same path and stores nothing new.
 */
export interface OffloadStore {
  put(request: OffloadPutRequest): Promise<OffloadPutResult>;
}

/** Longest preview kept in the entry, in lines. */
export const OFFLOAD_PREVIEW_LINES = 40;
/** Longest preview kept in the entry, in characters. */
export const OFFLOAD_PREVIEW_CHARS = 2_000;

const CHARS_PER_TOKEN = 4;

/** A result's content as one text; parts are joined with newlines (as the result cap does). */
export function resultText(result: Pick<ToolResult, "content">): string {
  return typeof result.content === "string"
    ? result.content
    : result.content.map((c) => c.text).join("\n");
}

/** Estimated tokens of a text (characters / 4, rounded up). */
export function estimateTextTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

/** The head of a text: at most {@link OFFLOAD_PREVIEW_LINES} lines and {@link OFFLOAD_PREVIEW_CHARS} characters. */
export function buildOffloadPreview(text: string): string {
  const head = text.split("\n", OFFLOAD_PREVIEW_LINES).join("\n");
  return head.length > OFFLOAD_PREVIEW_CHARS ? head.slice(0, OFFLOAD_PREVIEW_CHARS) : head;
}

/** The line that tells the model where the rest is. */
export function offloadPointer(path: string, tokens: number): string {
  return `[full output (~${tokens} tokens) saved to ${path}; use read_file with offset and limit to read more]`;
}

export interface MaybeOffloadInput {
  store: OffloadStore | undefined;
  settings: Pick<CompactionSettings, "offloadTokens" | "excludeTools">;
  conversation: ToolConversation;
  runId: string;
  call: { id: string; name: string };
  /** The result after the entry-size and token caps. */
  result: ToolResult;
  signal?: AbortSignal | undefined;
  /** Receives the error of a failed store; the original result is then kept. */
  onWarning: (error: unknown) => void;
}

/**
 * The result to commit: the preview-and-pointer replacement when the result
 * is over the threshold and the store kept it, otherwise `result` itself.
 */
export async function maybeOffload(input: MaybeOffloadInput): Promise<ToolResult> {
  const { store, settings, result, call } = input;
  if (!store || settings.offloadTokens <= 0) return result;
  if (settings.excludeTools.includes(call.name)) return result;
  const text = resultText(result);
  const tokens = estimateTextTokens(text);
  if (tokens <= settings.offloadTokens) return result;

  let stored: OffloadPutResult;
  try {
    stored = await store.put({
      conversation: input.conversation,
      runId: input.runId,
      toolCallId: call.id,
      toolName: call.name,
      text,
      ...(input.signal ? { signal: input.signal } : {}),
    });
    if (typeof stored?.path !== "string" || stored.path.length === 0) {
      throw new TypeError("offload store answered without a path");
    }
  } catch (err) {
    if (err instanceof LeaseLost) throw err;
    input.signal?.throwIfAborted();
    input.onWarning(err);
    return result;
  }

  const prior = result.details;
  const base: JsonObject =
    prior === undefined || prior === null
      ? {}
      : typeof prior === "object" && !Array.isArray(prior)
        ? (prior as JsonObject)
        : { value: prior };
  return {
    ...result,
    content: `${buildOffloadPreview(text)}\n\n${offloadPointer(stored.path, tokens)}`,
    details: { ...base, offloaded: { path: stored.path, bytes: stored.bytes } },
  };
}
