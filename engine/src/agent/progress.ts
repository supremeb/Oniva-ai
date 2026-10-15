/**
 * Throttled progress commits (KTD11).
 *
 * Partial model output goes to the conversation's `agent.live` document and
 * partial tool output to the tool task's `agent.tool_output` document. Both
 * are written at most once per interval (100 ms by default) on the injected
 * clock: the first change is written at once, later ones are coalesced into
 * one trailing write. Progress writes are best effort; the final commit of
 * the reply or result is what counts.
 */
import { z } from "zod";
import type { CancelTimer, Clock } from "../core/clock.js";
import { defineDoc } from "../session/documents.js";
import { jsonValue } from "./prompt.js";

export const DEFAULT_PROGRESS_INTERVAL_MS = 100;

/**
 * Most characters of partial tool output kept in the live document (the tail
 * is kept). The document is rewritten on every progress write, so this bounds
 * the write cost; the tool's own result is not affected by it.
 */
export const MAX_TOOL_OUTPUT_CHARS = 16 * 1024;

/** Snapshot size (characters) above which tool output is written less often. */
export const LARGE_TOOL_OUTPUT_CHARS = 8 * 1024;

/** Minimum interval between writes of a large tool output snapshot. */
export const LARGE_TOOL_OUTPUT_INTERVAL_MS = 250;

/**
 * The progress interval for a tool output snapshot of `chars` characters. A
 * base of 0 (throttling off, as tests configure it) is kept as is.
 */
export function toolOutputIntervalMs(baseMs: number, chars: number): number {
  if (baseMs <= 0 || chars <= LARGE_TOOL_OUTPUT_CHARS) return baseMs;
  return Math.max(baseMs, LARGE_TOOL_OUTPUT_INTERVAL_MS);
}

const liveSchema = z.object({
  runId: z.string().nullable(),
  taskId: z.string().nullable(),
  /** The streaming assistant message (an `AssistantMessage`), or null when idle. */
  message: jsonValue.nullable(),
});

export type LiveState = z.infer<typeof liveSchema>;

export const liveDoc = defineDoc<LiveState>({
  kind: "agent.live",
  scope: "conversation",
  initial: { runId: null, taskId: null, message: null },
  schema: liveSchema,
});

const toolOutputSchema = z.object({
  text: z.string(),
  /** True once characters were dropped from the front to stay under the cap. */
  truncated: z.boolean(),
  /** The execution attempt the output belongs to (a replay-safe rerun starts a new one). */
  attempt: z.number().int().min(1),
});

export type ToolOutputState = z.infer<typeof toolOutputSchema>;

export const toolOutputDoc = defineDoc<ToolOutputState>({
  kind: "agent.tool_output",
  scope: "task",
  initial: { text: "", truncated: false, attempt: 1 },
  schema: toolOutputSchema,
});

/**
 * Calls `flush` at most once per interval while changes keep arriving. The
 * interval may be a function, read each time a write is scheduled.
 */
export class ProgressWriter {
  readonly #clock: Clock;
  readonly #intervalMs: () => number;
  readonly #flush: () => Promise<void>;
  readonly #onError: (error: unknown) => void;
  #last = Number.NEGATIVE_INFINITY;
  #dirty = false;
  #closed = false;
  #timer: CancelTimer | null = null;
  #inflight: Promise<void> | null = null;

  constructor(
    clock: Clock,
    intervalMs: number | (() => number),
    flush: () => Promise<void>,
    onError: (error: unknown) => void = () => {},
  ) {
    this.#clock = clock;
    this.#intervalMs = typeof intervalMs === "function" ? intervalMs : () => intervalMs;
    this.#flush = flush;
    this.#onError = onError;
  }

  /** Something changed; write it soon. */
  mark(): void {
    if (this.#closed) return;
    this.#dirty = true;
    this.#schedule();
  }

  /** Stop writing; resolves once any write in flight has settled. Pending changes are dropped. */
  async close(): Promise<void> {
    this.#closed = true;
    this.#timer?.();
    this.#timer = null;
    await this.#inflight;
  }

  #schedule(): void {
    if (this.#inflight || this.#timer || this.#closed) return;
    const wait = this.#last + this.#intervalMs() - this.#clock.now();
    if (wait <= 0) {
      this.#run();
      return;
    }
    this.#timer = this.#clock.setTimer(wait, () => {
      this.#timer = null;
      this.#run();
    });
  }

  #run(): void {
    if (!this.#dirty || this.#closed) return;
    this.#dirty = false;
    this.#last = this.#clock.now();
    this.#inflight = this.#flush()
      .catch((err: unknown) => this.#onError(err))
      .finally(() => {
        this.#inflight = null;
        if (this.#dirty) this.#schedule();
      });
  }
}

/** Append to partial output, keeping at most {@link MAX_TOOL_OUTPUT_CHARS} (the tail). */
export function appendOutput(state: ToolOutputState, text: string): ToolOutputState {
  const joined = state.text + text;
  if (joined.length <= MAX_TOOL_OUTPUT_CHARS) return { ...state, text: joined };
  return { ...state, text: joined.slice(joined.length - MAX_TOOL_OUTPUT_CHARS), truncated: true };
}
