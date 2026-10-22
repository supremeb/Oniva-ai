/**
 * Memory curator gateway client (plan 2026-10-21-001, U30, KTD18): the engine's
 * HTTP calls to the backend's `/internal/memory/curate/...` routes.
 *
 * - `prepare`: `POST /internal/memory/curate/prepare` → a {@link CuratePrepareResponse}
 *   (`skip`, or `llm` with the curator prompt and an opaque `ctx`);
 * - `apply`: `POST /internal/memory/curate/apply` with the child's answer text
 *   → an {@link CurateApplyResponse}. The backend parses and validates the deltas;
 *   the engine never interprets them.
 *
 * Every call is keyed (`Idempotency-Key`, see {@link curateKey}); the backend
 * answers a key it already completed with the stored outcome without running
 * it again. Because of that every call is safe to send again, so failures that
 * are not definitive (network, 408, 429, 5xx) are retried with full-jitter
 * backoff, honoring `Retry-After`, inside the call's deadline.
 *
 * Fencing and auth match `src/workflow/node-gateway.ts`: `Authorization:
 * Bearer`, and while the worker holds a lease `X-Engine-Fence` and
 * `X-Engine-Session`. A rejected fence token (409 `fence_rejected`) calls
 * `onFenceRejected` and throws `LeaseLost`. Every response is validated with
 * `contract.ts`.
 *
 * Failures throw {@link MemoryGatewayError} with a stable `code`: `timeout`,
 * `gateway_error` (a definitive HTTP error), `gateway_unavailable` (network or
 * retries spent) or `invalid_response`. A cancelled call rejects with the
 * signal's reason. The contract is specified by
 * `test/support/fake-memory-backend.ts`.
 */
import { type Clock, realClock } from "../core/clock.js";
import { errorText, LeaseLost } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import type { Fence } from "../storage/types.js";
import { parseRetryAfter } from "../tools/gateway.js";
import {
  type CurateApplyRequest,
  type CurateApplyResponse,
  type CuratePrepareRequest,
  type CuratePrepareResponse,
  MemoryContractError,
  parseCurateApplyRequest,
  parseCurateApplyResponse,
  parseCuratePrepareRequest,
  parseCuratePrepareResponse,
} from "./contract.js";

export type CuratePhase = "prepare" | "apply";

/** The idempotency key of a curator call: `curate:{conversationId}:{runId}:{phase}` (KTD18). */
export function curateKey(conversationId: string, runId: string, phase: CuratePhase): string {
  return `curate:${conversationId}:${runId}:${phase}`;
}

export const PREPARE_PATH = "/internal/memory/curate/prepare";
export const APPLY_PATH = "/internal/memory/curate/apply";

export interface MemoryFenceRejection {
  idempotencyKey?: string;
  token: number | bigint;
}

export interface MemoryGatewayRetryOptions {
  /** Cap on attempts per call (1 = no retry). Default: unlimited, bounded by `budgetMs`. */
  attempts: number;
  /** First backoff delay; doubles per retry. Default 250. */
  baseDelayMs: number;
  /** Backoff cap. Default 5000. */
  maxDelayMs: number;
  /**
   * Total time a transient failure (network, 408, 429, 5xx) is retried with the
   * same idempotency key (`ENGINE_POLICY_RETRY_MS`, default 60 s); 0 disables
   * retrying. A `503 in_flight` answer restarts it. The call's own timeout and
   * abort still bound it.
   */
  budgetMs: number;
}

export interface MemoryGatewayOptions {
  /** Backend origin, e.g. `https://api.internal`; a trailing slash is ignored. */
  baseUrl: string;
  serviceToken: string;
  /** The current lease fence (`LeaseKeeper.fence`, or a bare token); null without a lease. */
  fence: () => Fence | number | bigint | null;
  sessionId?: string;
  onFenceRejected?: (rejection: MemoryFenceRejection) => void;
  fetch?: typeof fetch;
  clock?: Clock;
  retry?: Partial<MemoryGatewayRetryOptions>;
  /** Largest response body read. Default 1 MiB (a prepare response carries the digest). */
  maxResponseBytes?: number;
  /** Uniform [0, 1) source for backoff jitter. Default `Math.random`. */
  random?: () => number;
}

export interface MemoryCallOptions {
  /** Cancels the call. */
  signal?: AbortSignal;
  /** Deadline covering the call and its retries, in ms of the gateway clock. 0 or absent: none. */
  timeoutMs?: number;
}

export interface MemoryGateway {
  prepare(
    key: string,
    body: CuratePrepareRequest,
    options?: MemoryCallOptions,
  ): Promise<CuratePrepareResponse>;
  apply(
    key: string,
    body: CurateApplyRequest,
    options?: MemoryCallOptions,
  ): Promise<CurateApplyResponse>;
}

export type MemoryGatewayErrorCode =
  | "timeout"
  | "gateway_error"
  | "gateway_unavailable"
  | "invalid_response";

/** A memory gateway call failed; `code` is stable. */
export class MemoryGatewayError extends Error {
  constructor(
    readonly code: MemoryGatewayErrorCode,
    message: string,
    readonly status?: number,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "MemoryGatewayError";
  }
}

const DEFAULT_RETRY: MemoryGatewayRetryOptions = {
  attempts: Number.POSITIVE_INFINITY,
  baseDelayMs: 250,
  maxDelayMs: 5_000,
  budgetMs: 60_000,
};
const DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024;

type Attempt =
  | { kind: "response"; status: number; body: JsonValue | null; retryAfterMs?: number }
  | { kind: "too_large"; status: number }
  | { kind: "network"; error: unknown };

class Timeout extends Error {}

/** Build the memory curator gateway client. */
export function createMemoryGateway(options: MemoryGatewayOptions): MemoryGateway {
  const http = new MemoryHttp(options);
  return {
    async prepare(key, input, call) {
      const body = checkRequest(() => parseCuratePrepareRequest(input));
      const res = await http.post(PREPARE_PATH, key, body, call);
      return checkResponse(res, parseCuratePrepareResponse, "prepare");
    },
    async apply(key, input, call) {
      const body = checkRequest(() => parseCurateApplyRequest(input));
      const res = await http.post(APPLY_PATH, key, body, call);
      return checkResponse(res, parseCurateApplyResponse, "apply");
    },
  };
}

function checkRequest<T>(parse: () => T): T {
  try {
    return parse();
  } catch (err) {
    if (err instanceof MemoryContractError) {
      throw new MemoryGatewayError("gateway_error", `invalid request: ${err.message}`, undefined, {
        cause: err,
      });
    }
    throw err;
  }
}

function checkResponse<T>(body: JsonValue | null, parse: (raw: unknown) => T, what: string): T {
  try {
    return parse(body);
  } catch (err) {
    if (err instanceof MemoryContractError) {
      throw new MemoryGatewayError(
        "invalid_response",
        `${what}: the backend sent an invalid body: ${err.message}`,
        undefined,
        { cause: err },
      );
    }
    throw err;
  }
}

class MemoryHttp {
  readonly #base: string;
  readonly #options: MemoryGatewayOptions;
  readonly #fetch: typeof fetch;
  readonly #clock: Clock;
  readonly #retry: MemoryGatewayRetryOptions;
  readonly #maxResponseBytes: number;
  readonly #random: () => number;

  constructor(options: MemoryGatewayOptions) {
    if (typeof options.baseUrl !== "string" || options.baseUrl.length === 0) {
      throw new TypeError("memory gateway baseUrl is required");
    }
    if (typeof options.serviceToken !== "string" || options.serviceToken.length === 0) {
      throw new TypeError("memory gateway serviceToken is required");
    }
    this.#base = options.baseUrl.replace(/\/+$/, "");
    this.#options = options;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#clock = options.clock ?? realClock;
    this.#retry = { ...DEFAULT_RETRY, ...options.retry };
    if (
      this.#retry.attempts !== Number.POSITIVE_INFINITY &&
      (!Number.isSafeInteger(this.#retry.attempts) || this.#retry.attempts < 1)
    ) {
      throw new RangeError("memory gateway retry.attempts must be a positive integer");
    }
    this.#maxResponseBytes = options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    this.#random = options.random ?? Math.random;
  }

  /** One keyed POST with retries. Resolves the parsed 2xx body. */
  async post(
    path: string,
    key: string,
    body: unknown,
    call: MemoryCallOptions = {},
  ): Promise<JsonValue | null> {
    const deadline =
      call.timeoutMs !== undefined && call.timeoutMs > 0
        ? this.#clock.monotonic() + call.timeoutMs
        : undefined;
    try {
      return await this.#attempts(path, key, body, call.signal, deadline);
    } catch (err) {
      if (err instanceof Timeout) {
        throw new MemoryGatewayError("timeout", `memory call timed out after ${call.timeoutMs} ms`);
      }
      throw err;
    }
  }

  async #attempts(
    path: string,
    key: string,
    body: unknown,
    signal: AbortSignal | undefined,
    deadline: number | undefined,
  ): Promise<JsonValue | null> {
    const url = `${this.#base}${path}`;
    const budgetMs = Math.max(0, this.#retry.budgetMs);
    let budgetEnd = this.#clock.monotonic() + budgetMs;
    const text = JSON.stringify(body);
    for (let attempt = 1; ; attempt++) {
      const token = this.#token();
      const init: RequestInit = {
        method: "POST",
        headers: { ...this.#headers(key, token), "content-type": "application/json" },
        body: text,
      };
      const res = await this.#send(url, init, signal, deadline);
      if (res.kind === "response") {
        if (res.status >= 200 && res.status < 300) return res.body;
        if (res.status === 409 && errorOf(res.body) === "fence_rejected") {
          if (token !== null) this.#options.onFenceRejected?.({ idempotencyKey: key, token });
          throw new LeaseLost(this.#options.sessionId ?? "", Number(token ?? 0));
        }
      }
      if (!isRetryable(res) || attempt >= this.#retry.attempts) {
        throw failure(res, this.#maxResponseBytes);
      }
      // `503 in_flight` is a live backend holding this key's claim: not an outage, so the budget restarts.
      if (budgetMs > 0 && isInFlight(res)) budgetEnd = this.#clock.monotonic() + budgetMs;
      const delay = this.#delay(attempt, res.kind === "response" ? res.retryAfterMs : undefined);
      if (this.#clock.monotonic() + delay > budgetEnd) throw failure(res, this.#maxResponseBytes);
      await this.#backoff(delay, signal, deadline);
    }
  }

  #headers(key: string, token: number | bigint | null): Record<string, string> {
    const h: Record<string, string> = {
      authorization: `Bearer ${this.#options.serviceToken}`,
      accept: "application/json",
      "idempotency-key": key,
    };
    if (token !== null) h["x-engine-fence"] = String(token);
    if (this.#options.sessionId !== undefined) h["x-engine-session"] = this.#options.sessionId;
    return h;
  }

  #token(): number | bigint | null {
    const fence = this.#options.fence();
    return fence === null ? null : typeof fence === "object" ? fence.token : fence;
  }

  async #send(
    url: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
    deadline: number | undefined,
  ): Promise<Attempt> {
    signal?.throwIfAborted();
    const timer = new AbortController();
    let cancel = () => {};
    if (deadline !== undefined) {
      const remaining = deadline - this.#clock.monotonic();
      if (remaining <= 0) throw new Timeout();
      cancel = this.#clock.setTimer(remaining, () => timer.abort(new Timeout()));
    }
    const combined = signal ? AbortSignal.any([signal, timer.signal]) : timer.signal;
    try {
      const res = await this.#fetch(url, { ...init, signal: combined });
      const text = await readCapped(res, this.#maxResponseBytes);
      if (text === null) return { kind: "too_large", status: res.status };
      let body: JsonValue | null = null;
      if (text.length > 0) {
        try {
          body = JSON.parse(text) as JsonValue;
        } catch {
          body = text;
        }
      }
      const retryAfterMs =
        res.status === 429 || res.status === 503
          ? parseRetryAfter(res.headers.get("retry-after"), Date.now())
          : undefined;
      return {
        kind: "response",
        status: res.status,
        body,
        ...(retryAfterMs === undefined ? {} : { retryAfterMs }),
      };
    } catch (err) {
      if (signal?.aborted) throw signal.reason;
      if (timer.signal.aborted) throw timer.signal.reason;
      return { kind: "network", error: err };
    } finally {
      cancel();
    }
  }

  /** Capped exponential backoff with jitter in [50%, 100%]; the server's `Retry-After` wins. */
  #delay(attempt: number, retryAfterMs: number | undefined): number {
    if (retryAfterMs) return retryAfterMs;
    const { baseDelayMs, maxDelayMs } = this.#retry;
    const capped = Math.min(maxDelayMs, baseDelayMs * 2 ** Math.min(attempt - 1, 30));
    return Math.floor(capped * (0.5 + this.#random() * 0.5));
  }

  async #backoff(
    delay: number,
    signal: AbortSignal | undefined,
    deadline: number | undefined,
  ): Promise<void> {
    if (deadline !== undefined && this.#clock.monotonic() + delay >= deadline) throw new Timeout();
    await this.#clock.sleep(delay, signal);
  }
}

function isInFlight(res: Attempt): boolean {
  return res.kind === "response" && res.status === 503 && errorOf(res.body) === "in_flight";
}

function isRetryable(res: Attempt): boolean {
  if (res.kind === "network") return true;
  if (res.kind === "too_large") return false;
  return res.status >= 500 || res.status === 429 || res.status === 408;
}

function failure(res: Attempt, maxBytes: number): MemoryGatewayError {
  if (res.kind === "network") {
    return new MemoryGatewayError(
      "gateway_unavailable",
      `the backend could not be reached (${errorText(res.error)})`,
      undefined,
      { cause: res.error },
    );
  }
  if (res.kind === "too_large") {
    return new MemoryGatewayError(
      "invalid_response",
      `the backend's response (${res.status}) exceeded ${maxBytes} bytes`,
      res.status,
    );
  }
  const message = messageOf(res.body);
  return new MemoryGatewayError(
    isRetryable(res) ? "gateway_unavailable" : "gateway_error",
    `the backend answered ${res.status}${message ? ` (${message})` : ""}`,
    res.status,
  );
}

function errorOf(body: JsonValue | null): string | undefined {
  if (body && typeof body === "object" && !Array.isArray(body) && typeof body.error === "string") {
    return body.error;
  }
  return undefined;
}

function messageOf(body: JsonValue | null): string {
  if (typeof body === "string") return body.slice(0, 500);
  if (body && typeof body === "object" && !Array.isArray(body)) {
    if (typeof body.message === "string") return body.message.slice(0, 500);
    if (typeof body.error === "string") return body.error;
  }
  return "";
}

async function readCapped(res: Response, max: number): Promise<string | null> {
  const declared = Number(res.headers.get("content-length") ?? "");
  if (Number.isFinite(declared) && declared > max) {
    await res.body?.cancel().catch(() => {});
    return null;
  }
  if (!res.body) return "";
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel().catch(() => {});
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString("utf8");
}
