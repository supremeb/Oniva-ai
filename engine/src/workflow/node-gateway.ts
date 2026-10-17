/**
 * Workflow node gateway client (plan 2026-10-17-003, U11, KTD4): the engine's
 * HTTP calls to the backend's `/internal/workflows/...` routes.
 *
 * - `prepare`: `POST /internal/workflows/runs/{runId}/nodes/{nodeId}/prepare`
 *   → a {@link PrepareResult};
 * - `finish`: `POST .../nodes/{nodeId}/finish` with a {@link FinishInput}
 *   → a `final` {@link PrepareFinal};
 * - `finalize`: `POST /internal/workflows/runs/{runId}/finalize` with a
 *   {@link FinalizeInput};
 * - `lookup`: `GET /internal/workflows/invocations/{key}` → the outcome stored
 *   under an idempotency key, or null (404). This is what reconciles a call
 *   whose result the engine never saw.
 *
 * Every call is keyed (`Idempotency-Key`, see {@link nodeKey}); the backend
 * answers a key it already completed with the stored outcome without running
 * it again. Because of that every call is safe to send again, so failures
 * that are not definitive (network, 408, 429, 5xx) are retried with
 * full-jitter backoff, honoring `Retry-After`, inside the call's deadline.
 *
 * Fencing and auth match `src/tools/gateway.ts`: `Authorization: Bearer`, and
 * while the worker holds a lease `X-Engine-Fence` and `X-Engine-Session`. A
 * rejected fence token (409 `fence_rejected`) calls `onFenceRejected` and
 * throws `LeaseLost`. Every response is validated with `contract.ts`.
 *
 * Failures throw {@link NodeGatewayError} with a stable `code`: `timeout`
 * (the call's deadline passed), `gateway_error` (a definitive HTTP error),
 * `gateway_unavailable` (network or retries spent) or `invalid_response` (a
 * body the contract rejects). A cancelled call rejects with the signal's
 * reason. The contract is specified by `test/support/fake-workflow-backend.ts`.
 */
import { type Clock, realClock } from "../core/clock.js";
import { errorText, LeaseLost } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import type { Fence } from "../storage/types.js";
import { parseRetryAfter } from "../tools/gateway.js";
import {
  ContractError,
  parseFinalizeInput,
  parseFinishInput,
  parsePrepareResponse,
} from "./contract.js";
import type {
  CONTRACT_VERSION,
  FinalizeInput,
  FinishInput,
  PrepareFinal,
  PrepareResult,
} from "./types.js";

/** `prepare` or `finish`: the node call phases that carry an idempotency key. */
export type NodePhase = "prepare" | "finish";

export interface NodeKeyParts {
  runId: string;
  nodeId: string;
  runAttempt: number;
  nodeAttempt: number;
  phase: NodePhase;
}

/** The idempotency key of a node call: `{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{phase}` (KTD4). */
export function nodeKey(p: NodeKeyParts): string {
  return `${p.runId}:${p.nodeId}:r${p.runAttempt}:a${p.nodeAttempt}:${p.phase}`;
}

/** The idempotency key of the run's `finalize` call: `{runId}:r{runAttempt}:finalize`. */
export function finalizeKey(runId: string, runAttempt: number): string {
  return `${runId}:r${runAttempt}:finalize`;
}

/** The body of a `prepare` request. Tenant and project come from the run, never from the engine. */
export interface PrepareRequest {
  contractVersion: typeof CONTRACT_VERSION;
  runAttempt: number;
  nodeAttempt: number;
  nodeType: string;
}

export interface NodeFenceRejection {
  idempotencyKey?: string;
  token: number | bigint;
}

export interface NodeGatewayRetryOptions {
  /** Total attempts per call (1 = no retry). Default 3. */
  attempts: number;
  /** First backoff delay; doubles per retry. Default 200. */
  baseDelayMs: number;
  /** Backoff cap. Default 2000. */
  maxDelayMs: number;
}

export interface NodeGatewayOptions {
  /** Backend origin, e.g. `https://api.internal`; a trailing slash is ignored. */
  baseUrl: string;
  serviceToken: string;
  /** The current lease fence (`LeaseKeeper.fence`, or a bare token); null without a lease. */
  fence: () => Fence | number | bigint | null;
  sessionId?: string;
  onFenceRejected?: (rejection: NodeFenceRejection) => void;
  fetch?: typeof fetch;
  clock?: Clock;
  retry?: Partial<NodeGatewayRetryOptions>;
  /** Largest response body read. Default 256 KiB (the contract caps bodies at 64 KiB). */
  maxResponseBytes?: number;
  /** Uniform [0, 1) source for backoff jitter. Default `Math.random`. */
  random?: () => number;
}

export interface NodeCallOptions {
  /** Cancels the call. */
  signal?: AbortSignal;
  /** Deadline covering the call and its retries, in ms of the gateway clock. 0 or absent: none. */
  timeoutMs?: number;
}

export interface NodeGateway {
  prepare(
    ref: { runId: string; nodeId: string },
    key: string,
    body: PrepareRequest,
    options?: NodeCallOptions,
  ): Promise<PrepareResult>;
  finish(
    ref: { runId: string; nodeId: string },
    key: string,
    input: FinishInput,
    options?: NodeCallOptions,
  ): Promise<PrepareFinal>;
  finalize(
    runId: string,
    key: string,
    input: FinalizeInput,
    options?: NodeCallOptions,
  ): Promise<void>;
  /** The outcome stored under `key`, or null when the backend has none. */
  lookup(key: string, options?: NodeCallOptions): Promise<PrepareResult | null>;
}

export type NodeGatewayErrorCode =
  | "timeout"
  | "gateway_error"
  | "gateway_unavailable"
  | "invalid_response";

/** A node gateway call failed; `code` is stable. */
export class NodeGatewayError extends Error {
  constructor(
    readonly code: NodeGatewayErrorCode,
    message: string,
    readonly status?: number,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "NodeGatewayError";
  }
}

const DEFAULT_RETRY: NodeGatewayRetryOptions = { attempts: 3, baseDelayMs: 200, maxDelayMs: 2_000 };
const DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_RETRY_AFTER_MS = 60_000;

type Attempt =
  | { kind: "response"; status: number; body: JsonValue | null; retryAfterMs?: number }
  | { kind: "too_large"; status: number }
  | { kind: "network"; error: unknown };

class Timeout extends Error {}

/** Build the node gateway client. */
export function createNodeGateway(options: NodeGatewayOptions): NodeGateway {
  const http = new NodeHttp(options);
  const node = (ref: { runId: string; nodeId: string }, phase: NodePhase) =>
    `/internal/workflows/runs/${encodeURIComponent(ref.runId)}/nodes/${encodeURIComponent(ref.nodeId)}/${phase}`;
  return {
    async prepare(ref, key, body, call) {
      const res = await http.call("POST", node(ref, "prepare"), key, body, call);
      return parseResponse(res, parsePrepareResponse, "prepare");
    },
    async finish(ref, key, input, call) {
      const body = parseFinishInput(input);
      const res = await http.call("POST", node(ref, "finish"), key, body, call);
      const result = parseResponse(res, parsePrepareResponse, "finish");
      if (result.kind !== "final") {
        throw new NodeGatewayError("invalid_response", "finish must answer a final result");
      }
      return result;
    },
    async finalize(runId, key, input, call) {
      const body = parseFinalizeInput(input);
      await http.call(
        "POST",
        `/internal/workflows/runs/${encodeURIComponent(runId)}/finalize`,
        key,
        body,
        call,
      );
    },
    async lookup(key, call) {
      const res = await http.call(
        "GET",
        `/internal/workflows/invocations/${encodeURIComponent(key)}`,
        undefined,
        undefined,
        call,
        true,
      );
      if (res === null) return null;
      return parseResponse(res, parsePrepareResponse, "lookup");
    },
  };
}

function parseResponse<T>(body: JsonValue | null, parse: (raw: unknown) => T, what: string): T {
  try {
    return parse(body);
  } catch (err) {
    if (err instanceof ContractError) {
      throw new NodeGatewayError(
        "invalid_response",
        `${what}: the backend sent an invalid body (${err.code}): ${err.message}`,
        undefined,
        { cause: err },
      );
    }
    throw err;
  }
}

class NodeHttp {
  readonly #base: string;
  readonly #options: NodeGatewayOptions;
  readonly #fetch: typeof fetch;
  readonly #clock: Clock;
  readonly #retry: NodeGatewayRetryOptions;
  readonly #maxResponseBytes: number;
  readonly #random: () => number;

  constructor(options: NodeGatewayOptions) {
    if (typeof options.baseUrl !== "string" || options.baseUrl.length === 0) {
      throw new TypeError("node gateway baseUrl is required");
    }
    if (typeof options.serviceToken !== "string" || options.serviceToken.length === 0) {
      throw new TypeError("node gateway serviceToken is required");
    }
    this.#base = options.baseUrl.replace(/\/+$/, "");
    this.#options = options;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#clock = options.clock ?? realClock;
    this.#retry = { ...DEFAULT_RETRY, ...options.retry };
    if (!Number.isSafeInteger(this.#retry.attempts) || this.#retry.attempts < 1) {
      throw new RangeError("node gateway retry.attempts must be a positive integer");
    }
    this.#maxResponseBytes = options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    this.#random = options.random ?? Math.random;
  }

  /**
   * One keyed call with retries. Resolves the parsed 2xx body; with
   * `nullOn404` a 404 resolves null.
   */
  async call(
    method: "GET" | "POST",
    path: string,
    key: string | undefined,
    body: unknown,
    call: NodeCallOptions = {},
    nullOn404 = false,
  ): Promise<JsonValue | null> {
    const deadline =
      call.timeoutMs !== undefined && call.timeoutMs > 0
        ? this.#clock.monotonic() + call.timeoutMs
        : undefined;
    try {
      return await this.#attempts(method, path, key, body, call.signal, deadline, nullOn404);
    } catch (err) {
      if (err instanceof Timeout) {
        throw new NodeGatewayError("timeout", `node call timed out after ${call.timeoutMs} ms`);
      }
      throw err;
    }
  }

  async #attempts(
    method: "GET" | "POST",
    path: string,
    key: string | undefined,
    body: unknown,
    signal: AbortSignal | undefined,
    deadline: number | undefined,
    nullOn404: boolean,
  ): Promise<JsonValue | null> {
    const url = `${this.#base}${path}`;
    const text = body === undefined ? undefined : JSON.stringify(body);
    for (let attempt = 1; ; attempt++) {
      const token = this.#token();
      const init: RequestInit = {
        method,
        headers: {
          ...this.#headers(key, token),
          ...(text === undefined ? {} : { "content-type": "application/json" }),
        },
        ...(text === undefined ? {} : { body: text }),
      };
      const res = await this.#send(url, init, signal, deadline);
      if (res.kind === "response") {
        if (res.status >= 200 && res.status < 300) return res.body;
        if (res.status === 404 && nullOn404) return null;
        if (res.status === 409 && errorOf(res.body) === "fence_rejected") {
          if (token !== null) {
            this.#options.onFenceRejected?.({
              ...(key === undefined ? {} : { idempotencyKey: key }),
              token,
            });
          }
          throw new LeaseLost(this.#options.sessionId ?? "", Number(token ?? 0));
        }
      }
      if (!isRetryable(res) || attempt >= this.#retry.attempts)
        throw failure(res, this.#maxResponseBytes);
      await this.#backoff(
        attempt,
        signal,
        deadline,
        res.kind === "response" ? res.retryAfterMs : 0,
      );
    }
  }

  #headers(key: string | undefined, token: number | bigint | null): Record<string, string> {
    const h: Record<string, string> = {
      authorization: `Bearer ${this.#options.serviceToken}`,
      accept: "application/json",
    };
    if (key !== undefined) h["idempotency-key"] = key;
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

  async #backoff(
    attempt: number,
    signal: AbortSignal | undefined,
    deadline: number | undefined,
    retryAfterMs: number | undefined,
  ): Promise<void> {
    const { baseDelayMs, maxDelayMs } = this.#retry;
    const delay = retryAfterMs
      ? deadline === undefined
        ? Math.min(retryAfterMs, MAX_RETRY_AFTER_MS)
        : retryAfterMs
      : Math.floor(this.#random() * Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1)));
    if (deadline !== undefined && this.#clock.monotonic() + delay >= deadline) throw new Timeout();
    // The deadline must still fire while backing off.
    await this.#clock.sleep(delay, signal);
  }
}

function isRetryable(res: Attempt): boolean {
  if (res.kind === "network") return true;
  if (res.kind === "too_large") return false;
  return res.status >= 500 || res.status === 429 || res.status === 408;
}

function failure(res: Attempt, maxBytes: number): NodeGatewayError {
  if (res.kind === "network") {
    return new NodeGatewayError(
      "gateway_unavailable",
      `the backend could not be reached (${errorText(res.error)})`,
      undefined,
      { cause: res.error },
    );
  }
  if (res.kind === "too_large") {
    return new NodeGatewayError(
      "invalid_response",
      `the backend's response (${res.status}) exceeded ${maxBytes} bytes`,
      res.status,
    );
  }
  const message = messageOf(res.body);
  const retryable = res.status >= 500 || res.status === 429 || res.status === 408;
  return new NodeGatewayError(
    retryable ? "gateway_unavailable" : "gateway_error",
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
