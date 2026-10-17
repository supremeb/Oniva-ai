/**
 * Retry plumbing for fetch-based model providers.
 *
 * - `withRetry` wraps one provider attempt (an async generator of stream
 *   events). It re-runs the attempt after a transient failure, but only while
 *   nothing has reached the caller: once a `block_start`, `block_delta` or
 *   `block_end` has been yielded, a failure is passed through untouched,
 *   because replaying would duplicate partial output.
 * - Backoff is exponential with jitter in [50%, 100%], capped per delay, and
 *   bounded by both a retry count and a total time budget. A server-provided
 *   delay (`Retry-After`, OpenRouter's `x-ratelimit-reset`) replaces the
 *   computed one; if it does not fit the remaining budget, retrying stops.
 * - A cancelled context stops the wait at once and ends with `aborted`.
 * - Per-attempt timers bound how long an attempt may wait for its first data
 *   event (`firstByteTimeoutMs`) and how long it may go silent between data
 *   events (`idleTimeoutMs`). A timeout aborts only that attempt's controller;
 *   before any block event the attempt is retried as `network`, after one it
 *   ends `network, retryable: true` with the partial kept.
 * - `describeError` renders a thrown value with its `cause` chain (code and
 *   message only), so undici's bare "fetch failed" keeps its real reason.
 *   Headers, request bodies and keys are never read.
 */
import type { Context } from "../core/context.js";
import { abortMessage, type MessageBuilder, modelError } from "./stream.js";
import type { ModelError, StreamEvent } from "./types.js";

export interface RetryInfo {
  provider: string;
  model: string;
  /** The attempt that just failed, 1-based. */
  attempt: number;
  delayMs: number;
  error: ModelError;
}

export interface RetryPolicy {
  /** Retries after the first attempt; `0` disables retrying. Default 3. */
  maxRetries?: number;
  /** Total time the retries may take (waits only), in ms. Default 60 000. */
  budgetMs?: number;
  /** First delay; doubles each retry. Default 500 ms. */
  baseDelayMs?: number;
  /** Cap on a computed delay. Default 8 000 ms. */
  maxDelayMs?: number;
  /** Called before each wait (log it; never throws into the stream). */
  onRetry?: (info: RetryInfo) => void;
  /**
   * Abort an attempt that has produced no data event within this long, in ms.
   * Default 120 000; `0` disables it.
   */
  firstByteTimeoutMs?: number;
  /** Abort an attempt silent for this long between data events, in ms. Default 120 000; `0` disables. */
  idleTimeoutMs?: number;
  /** Test seams (`sleep` must resolve early when the signal aborts). */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  random?: () => number;
  /** Test seam for the attempt timers: arm `callback` after `ms`, return a cancel. */
  setTimer?: SetTimer;
}

/** Arms a callback after `ms`, returning a function that cancels it. */
export type SetTimer = (ms: number, callback: () => void) => () => void;

export const DEFAULT_MAX_RETRIES = 3;
export const DEFAULT_RETRY_BUDGET_MS = 60_000;
export const DEFAULT_FIRST_BYTE_TIMEOUT_MS = 120_000;
export const DEFAULT_IDLE_TIMEOUT_MS = 120_000;
const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 8_000;

/** What an attempt tells the retry loop about a failure it yields. */
export interface AttemptMeta {
  /** Server-requested wait before the next attempt, in ms. */
  retryAfterMs?: number | null;
  /** Force the decision (otherwise derived from the error's kind). */
  retryable?: boolean;
}

/**
 * Fired by an attempt timer and used as the attempt controller's abort reason,
 * so a provider can tell a timeout from a run abort (`aborted`).
 */
export class ModelTimeoutError extends Error {
  constructor(
    readonly phase: "first_byte" | "idle",
    readonly timeoutMs: number,
  ) {
    super(
      phase === "first_byte" ? `no response within ${timeoutMs}ms` : `no data for ${timeoutMs}ms`,
    );
    this.name = "ModelTimeoutError";
  }
}

/** The timeout that aborted `signal`, or null when it aborted for any other reason. */
export function modelTimeout(signal: AbortSignal): ModelTimeoutError | null {
  const reason: unknown = signal.reason;
  return reason instanceof ModelTimeoutError ? reason : null;
}

/** Kinds worth another try (plus HTTP 409). Never auth, invalid_request, context_overflow, denied or aborted. */
export function isTransientError(error: ModelError): boolean {
  if (error.status === 409) return true;
  return error.kind === "rate_limited" || error.kind === "overloaded" || error.kind === "network";
}

const EMITTED: ReadonlySet<StreamEvent["type"]> = new Set([
  "block_start",
  "block_delta",
  "block_end",
]);

/**
 * Run `attempt` until it succeeds or fails for good. `attempt` yields the
 * usual events ending in `done` or `error` and may fill `meta` before it
 * yields an error. The first `start` event is passed on; later ones are
 * dropped so the caller sees one message.
 *
 * Each attempt gets its own `AbortController`, combined with the run signal:
 * the run signal still wins, while the per-attempt timers abort only this
 * attempt (see {@link ModelTimeoutError}).
 */
export async function* withRetry(
  ctx: Context,
  b: MessageBuilder,
  policy: RetryPolicy,
  attempt: (meta: AttemptMeta, signal: AbortSignal) => AsyncIterable<StreamEvent>,
): AsyncGenerator<StreamEvent> {
  const maxRetries = Math.max(0, policy.maxRetries ?? DEFAULT_MAX_RETRIES);
  const budget = Math.max(0, policy.budgetMs ?? DEFAULT_RETRY_BUDGET_MS);
  const base = Math.max(1, policy.baseDelayMs ?? DEFAULT_BASE_DELAY_MS);
  const maxDelay = Math.max(base, policy.maxDelayMs ?? DEFAULT_MAX_DELAY_MS);
  const sleep = policy.sleep ?? abortableSleep;
  const random = policy.random ?? Math.random;
  const setTimer = policy.setTimer ?? defaultSetTimer;
  const firstByteTimeoutMs = Math.max(
    0,
    policy.firstByteTimeoutMs ?? DEFAULT_FIRST_BYTE_TIMEOUT_MS,
  );
  const idleTimeoutMs = Math.max(0, policy.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS);
  let spent = 0;
  let startSent = false;

  for (let n = 1; ; n++) {
    const meta: AttemptMeta = {};
    const controller = new AbortController();
    const signal = AbortSignal.any([ctx.signal, controller.signal]);
    const timers = attemptTimers(firstByteTimeoutMs, idleTimeoutMs, setTimer, (reason) =>
      controller.abort(reason),
    );
    let emitted = false;
    let failure: ModelError | undefined;
    try {
      for await (const event of attempt(meta, signal)) {
        if (event.type === "start") {
          if (startSent) continue;
          startSent = true;
        } else if (EMITTED.has(event.type)) {
          emitted = true;
          timers.onData();
        }
        if (event.type === "error" && !emitted && !ctx.cancelled) {
          const transient = meta.retryable ?? isTransientError(event.error);
          const delay =
            transient && n <= maxRetries ? nextDelay(n, meta.retryAfterMs ?? null) : null;
          if (delay !== null) {
            failure = event.error;
            spent += delay;
            safely(() =>
              policy.onRetry?.({
                provider: b.provider,
                model: b.model,
                attempt: n,
                delayMs: delay,
                error: event.error,
              }),
            );
            timers.clear();
            await sleep(delay, ctx.signal);
            break;
          }
        }
        yield event;
        if (event.type === "done" || event.type === "error") return;
      }
    } finally {
      timers.clear();
    }
    if (failure === undefined) return; // attempt ended without a terminal event; ModelStream reports it
    if (ctx.cancelled) {
      yield b.fail(modelError("aborted", abortMessage(ctx)));
      return;
    }
  }

  /** Delay before retry `n`, or null when it would exceed the time budget. */
  function nextDelay(n: number, hinted: number | null): number | null {
    const computed = Math.min(maxDelay, base * 2 ** Math.min(n - 1, 30));
    const delay = hinted ?? Math.max(1, Math.round(computed * (0.5 + random() * 0.5)));
    return spent + delay > budget ? null : delay;
  }
}

const defaultSetTimer: SetTimer = (ms, callback) => {
  const handle = setTimeout(callback, ms);
  return () => clearTimeout(handle);
};

/**
 * The timers for one attempt. The first-byte timer is cleared by the first
 * data event; the idle timer is armed then and reset on every data event. A
 * timer fires `abort` with a {@link ModelTimeoutError}.
 */
function attemptTimers(
  firstByteMs: number,
  idleMs: number,
  setTimer: SetTimer,
  abort: (reason: unknown) => void,
): { onData: () => void; clear: () => void } {
  let firstByte: (() => void) | undefined;
  let idle: (() => void) | undefined;
  let started = false;

  const resetIdle = (): void => {
    idle?.();
    idle =
      idleMs > 0 ? setTimer(idleMs, () => abort(new ModelTimeoutError("idle", idleMs))) : undefined;
  };
  if (firstByteMs > 0) {
    firstByte = setTimer(firstByteMs, () =>
      abort(new ModelTimeoutError("first_byte", firstByteMs)),
    );
  }
  return {
    onData(): void {
      if (!started) {
        started = true;
        firstByte?.();
        firstByte = undefined;
      }
      resetIdle();
    },
    clear(): void {
      firstByte?.();
      firstByte = undefined;
      idle?.();
      idle = undefined;
    },
  };
}

/** Abortable wait: resolves when the time is up or `signal` aborts. */
export function abortableSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve) => {
    if (signal?.aborted) return resolve();
    const done = (): void => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  });
}

function safely(fn: () => void): void {
  try {
    fn();
  } catch {
    // a logging hook must never break the stream
  }
}

/** `Retry-After` (delta seconds or HTTP date) as ms, or null when absent or unusable. */
export function retryAfterMs(header: string | null, now: number): number | null {
  if (header === null) return null;
  const v = header.trim();
  if (/^\d+(\.\d+)?$/.test(v)) return Math.round(Number(v) * 1000);
  const at = Date.parse(v);
  return Number.isNaN(at) ? null : Math.max(0, at - now);
}

/**
 * OpenRouter's `x-ratelimit-reset`: a unix timestamp in ms (seconds are also
 * accepted) at which the limit resets; returned as the wait from `now`.
 */
export function rateLimitResetMs(header: string | null, now: number): number | null {
  if (header === null) return null;
  const v = Number(header.trim());
  if (!Number.isFinite(v) || v <= 0) return null;
  const at = v < 1e11 ? v * 1000 : v;
  return Math.max(0, Math.round(at - now));
}

/** The server's requested wait from response headers, or null. */
export function serverRetryDelay(headers: Headers, now: number): number | null {
  return (
    retryAfterMs(headers.get("retry-after"), now) ??
    rateLimitResetMs(headers.get("x-ratelimit-reset"), now)
  );
}

// ---------------------------------------------------------------------------
// Error description
// ---------------------------------------------------------------------------

const MAX_PART = 200;

interface Coded {
  code?: unknown;
  errors?: unknown;
}

/**
 * "fetch failed (ECONNRESET: other side closed)": the error's message plus,
 * for each error down its `cause` chain, `code: message`. Only code and
 * message strings are read.
 */
export function describeError(err: unknown): string {
  if (!(err instanceof Error)) return String(err).slice(0, MAX_PART * 2);
  const top = clip(err.message) || err.name;
  const parts: string[] = [];
  const seen = new Set<unknown>([err]);
  let cause: unknown = err.cause;
  while (cause instanceof Error && !seen.has(cause)) {
    seen.add(cause);
    const part = codeAndMessage(cause);
    if (part !== "" && part !== top && !parts.includes(part)) parts.push(part);
    // happy-eyeballs failures arrive as an AggregateError of per-address errors
    const inner = (cause as Coded).errors;
    if (Array.isArray(inner)) {
      for (const e of inner.slice(0, 2)) {
        if (e instanceof Error && !seen.has(e)) {
          seen.add(e);
          const p = codeAndMessage(e);
          if (p !== "" && !parts.includes(p)) parts.push(p);
        }
      }
    }
    cause = cause.cause;
  }
  return parts.length === 0 ? top : `${top} (${parts.join("; ")})`;
}

function codeAndMessage(err: Error): string {
  const code = typeof (err as Coded).code === "string" ? clip((err as Coded).code as string) : "";
  const message = clip(err.message);
  if (code !== "" && message !== "" && message !== code) return `${code}: ${message}`;
  return code || message;
}

function clip(s: string): string {
  return s.length > MAX_PART ? `${s.slice(0, MAX_PART)}...` : s;
}

/** True when an error (or anything in its cause chain) carries a connection-level code. */
export function isTransportError(err: unknown): boolean {
  const seen = new Set<unknown>();
  let e: unknown = err;
  while (e instanceof Error && !seen.has(e)) {
    seen.add(e);
    const code = (e as Coded).code;
    if (
      typeof code === "string" &&
      /^(E(CONN|PIPE|TIMEDOUT|NOTFOUND|AI_AGAIN|HOSTUNREACH|NETUNREACH)|UND_ERR_|ERR_STREAM)/.test(
        code,
      )
    ) {
      return true;
    }
    if (e.name === "TimeoutError") return true;
    e = e.cause;
  }
  return false;
}
