/**
 * Tool gateway client (R6, R18): Oniva's backend tools, run over HTTP.
 *
 * {@link createGatewayTools} loads the gateway's manifest and turns each entry
 * into a registry tool:
 *
 * - Parameters: the manifest's JSON Schema becomes a zod schema with zod's
 *   own `z.fromJSONSchema`, so arguments are validated in the engine and the
 *   provider sees an equivalent schema. A schema zod cannot convert (external
 *   `$ref`, `if`/`then`, ...) falls back to "any object with the schema's
 *   `required` keys", with the original schema still sent to the provider
 *   (as zod metadata); the gateway
 *   validates fully either way.
 * - Replay: a read (`sideEffect: "none"`) or an idempotent write is
 *   `replay: "safe"`; any other write is `"unsafe"`, and after a restart its
 *   `reconcile` asks the gateway for the outcome stored under the call's
 *   idempotency key (`GET /internal/tools/invocations/{key}`). Without one
 *   the agent loop reports the call `interrupted`.
 * - `requiresApproval` is exposed through {@link GatewayToolset.requiresApproval}
 *   for the host's `HostPolicy.authorizeTool` to consult.
 *
 * Invoking posts `{ args, runId, toolCallId, tenantId, projectId }` to
 * `/internal/tools/{name}/invoke`. Tenant and project come from the
 * conversation's stored binding, never from the model's arguments. Headers
 * carry the service token and `Idempotency-Key: runId:toolCallId`.
 *
 * Fencing: while the worker holds a lease, every request (manifest load,
 * invoke, reconcile, {@link GatewayToolset.raiseFence}) carries
 * `X-Engine-Fence: <token>` and `X-Engine-Session`. The gateway raises the
 * session's high-water mark on any fenced request, so a new owner's manifest
 * load or reconcile alone locks out the previous owner's invokes still on
 * the wire. A rejected token (409 `fence_rejected`) calls `onFenceRejected`.
 *
 * Failures become error results rather than throws, with `details.error`:
 * - `timeout`: the tool's `timeoutMs` passed (covering retries); the request
 *   is aborted. For a non-idempotent write the outcome is unknown (the
 *   gateway keeps running a started effect), so the client asks once for an
 *   outcome stored under the idempotency key and returns it when there is
 *   one; otherwise the result says the write may have taken effect, with
 *   `details.outcome: "unknown"`;
 * - `lease_lost`: the gateway rejected the fence token, or the lease was
 *   already lost;
 * - `gateway_error` (an HTTP error status, or a response body over
 *   `maxResponseBytes`, with `details.reason: "response_too_large"`) or
 *   `gateway_unavailable` (network failure). Writes are sent exactly once;
 *   reads retry 5xx, 429 and network failures with full-jitter exponential
 *   backoff, honoring `Retry-After` on 429/503, within the tool's deadline.
 * A cancelled call (the caller's signal) rejects with the signal's reason.
 *
 * Tier 0 offload (U12): {@link createOffloadStore} builds an `OffloadStore`
 * over the same plumbing. `put` posts `{ tenantId, projectId, conversationId,
 * engineConversationId, runId, toolCallId, toolName, text }` to
 * `POST /internal/workspace/offload` with `Idempotency-Key: runId:toolCallId`
 * and the fence headers, and reads `{ path, bytes }` back. The route is
 * idempotent, so every failure that is an outage (network, 408, 429, 5xx) is
 * retried within `timeoutMs`; a definitive answer (413, 400, ...) throws at
 * once, a rejected fence throws `LeaseLost`.
 *
 * The contract is specified by the fake gateway in
 * `test/support/fake-gateway.ts`.
 */
import { z } from "zod";
import type { OffloadPutRequest, OffloadPutResult, OffloadStore } from "../agent/offload.js";
import { type Clock, realClock } from "../core/clock.js";
import { errorText, LeaseLost } from "../core/errors.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import type { TextContent } from "../models/types.js";
import type { Fence } from "../storage/types.js";
import {
  backendConversationIds,
  defineTool,
  type ReplayPolicy,
  type ToolConversation,
  type ToolDefinition,
  type ToolResult,
} from "./registry.js";

export type GatewaySideEffect = "none" | "write";

/** One manifest entry. */
export interface GatewayToolInfo {
  name: string;
  description: string;
  /** JSON Schema of the argument object. */
  parameters: JsonObject;
  sideEffect: GatewaySideEffect;
  idempotent: boolean;
  timeoutMs: number;
  requiresApproval: boolean;
  /** Whether the tool's result is untrusted input for the conversation (taints it). */
  untrustedOutput: boolean;
}

export interface GatewayRetryOptions {
  /** Total attempts for reads (1 = no retry). Default 3. */
  attempts: number;
  /** First backoff delay; doubles per retry. Default 200. */
  baseDelayMs: number;
  /** Backoff cap. Default 2000. */
  maxDelayMs: number;
}

export interface FenceRejection {
  /** The tool whose invoke or reconcile was rejected; absent for a manifest load or `raiseFence`. */
  tool?: string;
  idempotencyKey?: string;
  token: number | bigint;
}

export interface GatewayClientOptions {
  /** Gateway origin, e.g. `https://api.internal`; a trailing slash is ignored. */
  baseUrl: string;
  serviceToken: string;
  /**
   * The current lease fence (`LeaseKeeper.fence`, or a bare token); null when
   * the worker runs without a lease. A `LeaseLost` throw means the lease is
   * gone and the call is not sent.
   */
  fence: () => Fence | number | bigint | null;
  /** Sent as `X-Engine-Session`, so the gateway can key fence tokens by session. */
  sessionId?: string;
  /** Called when the gateway rejects the fence token. Fence the worker here. */
  onFenceRejected?: (rejection: FenceRejection) => void;
  fetch?: typeof fetch;
  clock?: Clock;
  retry?: Partial<GatewayRetryOptions>;
  /** Timeout for the manifest load (per attempt). Default 10000. */
  manifestTimeoutMs?: number;
  /** Largest response body read; a bigger one is an error. Default 1 MiB. */
  maxResponseBytes?: number;
  /** Uniform [0, 1) source for backoff jitter. Default `Math.random`. */
  random?: () => number;
  /** Cancels the manifest load. */
  signal?: AbortSignal;
}

export interface GatewayToolset {
  /** Registry tools, in manifest order. */
  readonly tools: ToolDefinition<JsonObject>[];
  names(): string[];
  /** The manifest entry for a tool. */
  describe(name: string): GatewayToolInfo | undefined;
  /** Whether the manifest asks for human approval before running the tool. */
  requiresApproval(name: string): boolean;
  /**
   * Send the current fence token to the gateway (a fenced manifest read), so
   * it raises the session's high-water mark. Call it right after acquiring a
   * lease, before recovering the runtime, when the toolset outlives a lease.
   * Rejects with `LeaseLost` when the gateway refuses the token.
   */
  raiseFence(signal?: AbortSignal): Promise<void>;
}

export type GatewayErrorCode = "timeout" | "lease_lost" | "gateway_error" | "gateway_unavailable";

const DEFAULT_RETRY: GatewayRetryOptions = { attempts: 3, baseDelayMs: 200, maxDelayMs: 2_000 };
const DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024;
/** Longest `Retry-After` honored when no tool deadline bounds the wait. */
const MAX_RETRY_AFTER_MS = 60_000;
/** Budget for the one reconcile after a write timed out. */
const TIMEOUT_RECONCILE_MS = 5_000;
const DEFAULT_OFFLOAD_TIMEOUT_MS = 20_000;
const UNKNOWN_OUTCOME = " It may have taken effect; check before calling again.";

/** Load the manifest and build the gateway's tools. */
export async function createGatewayTools(options: GatewayClientOptions): Promise<GatewayToolset> {
  const http = new GatewayHttp(options);
  const manifest = await http.manifest();
  const infos = new Map(manifest.map((t) => [t.name, t]));
  const tools = manifest.map((info) => http.tool(info));
  return {
    tools,
    names: () => [...infos.keys()],
    describe: (name) => {
      const info = infos.get(name);
      return info ? structuredClone(info) : undefined;
    },
    requiresApproval: (name) => infos.get(name)?.requiresApproval === true,
    raiseFence: async (signal) => {
      await http.manifest(signal);
    },
  };
}

export interface OffloadClientOptions extends GatewayClientOptions {
  /** Budget for one `put`, retries included. Default 20000. */
  timeoutMs?: number;
}

/** An {@link OffloadStore} backed by the gateway's `POST /internal/workspace/offload`. */
export function createOffloadStore(options: OffloadClientOptions): OffloadStore {
  const http = new GatewayHttp(options);
  const timeoutMs = options.timeoutMs ?? DEFAULT_OFFLOAD_TIMEOUT_MS;
  return { put: (request) => http.offload(request, timeoutMs) };
}

/** `replay` for a manifest entry. */
export function gatewayReplayPolicy(
  info: Pick<GatewayToolInfo, "sideEffect" | "idempotent">,
): ReplayPolicy {
  return info.sideEffect === "none" || info.idempotent ? "safe" : "unsafe";
}

/** A zod schema for a manifest JSON Schema (see the module comment). */
export function gatewayParameters(schema: JsonObject): z.ZodType<JsonObject> {
  try {
    return z.fromJSONSchema(schema as never) as unknown as z.ZodType<JsonObject>;
  } catch {
    const required = Array.isArray(schema.required)
      ? schema.required.filter((k): k is string => typeof k === "string")
      : [];
    const { $schema: _drop, ...forProvider } = schema;
    return z
      .looseObject({})
      .superRefine((value, ctx) => {
        for (const key of required) {
          if (!(key in value)) ctx.addIssue({ code: "custom", path: [key], message: "required" });
        }
      })
      .meta(forProvider) as unknown as z.ZodType<JsonObject>;
  }
}

// ---------------------------------------------------------------------------

type Attempt =
  | { kind: "response"; status: number; body: JsonValue | null; retryAfterMs?: number }
  | { kind: "too_large"; status: number }
  | { kind: "network"; error: unknown };

class Timeout extends Error {}

class GatewayHttp {
  readonly #base: string;
  readonly #options: GatewayClientOptions;
  readonly #fetch: typeof fetch;
  readonly #clock: Clock;
  readonly #retry: GatewayRetryOptions;
  readonly #maxResponseBytes: number;
  readonly #random: () => number;

  constructor(options: GatewayClientOptions) {
    if (typeof options.baseUrl !== "string" || options.baseUrl.length === 0) {
      throw new TypeError("gateway baseUrl is required");
    }
    if (typeof options.serviceToken !== "string" || options.serviceToken.length === 0) {
      throw new TypeError("gateway serviceToken is required");
    }
    this.#base = options.baseUrl.replace(/\/+$/, "");
    this.#options = options;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#clock = options.clock ?? realClock;
    this.#retry = { ...DEFAULT_RETRY, ...options.retry };
    if (!Number.isSafeInteger(this.#retry.attempts) || this.#retry.attempts < 1) {
      throw new RangeError("gateway retry.attempts must be a positive integer");
    }
    this.#maxResponseBytes = options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    if (!Number.isSafeInteger(this.#maxResponseBytes) || this.#maxResponseBytes < 1) {
      throw new RangeError("gateway maxResponseBytes must be a positive integer");
    }
    this.#random = options.random ?? Math.random;
  }

  /** Load the manifest, fenced (which also raises the gateway's high-water mark). */
  async manifest(signal = this.#options.signal): Promise<GatewayToolInfo[]> {
    const url = `${this.#base}/internal/tools/manifest`;
    const timeoutMs = this.#options.manifestTimeoutMs ?? 10_000;
    for (let attempt = 1; ; attempt++) {
      const token = this.#token();
      const res = await this.#send(
        url,
        { method: "GET", headers: this.#headers(undefined, token) },
        signal,
        timeoutMs,
      );
      if (res.kind === "response" && res.status === 200) return parseManifest(res.body);
      this.#throwIfFenceRejected(res, token, {});
      if (!isRetryableAttempt(res) || attempt >= this.#retry.attempts) {
        throw new Error(`gateway manifest load failed: ${whyFailed(res, this.#maxResponseBytes)}`);
      }
      await this.#backoff(attempt, signal, undefined, retryAfterOf(res));
    }
  }

  /** Store a tool result out of context (see {@link createOffloadStore}). */
  async offload(request: OffloadPutRequest, timeoutMs: number): Promise<OffloadPutResult> {
    const url = `${this.#base}/internal/workspace/offload`;
    const { conversation } = request;
    const body = JSON.stringify({
      tenantId: conversation.tenantId,
      projectId: conversation.projectId,
      ...backendConversationIds(conversation),
      runId: request.runId,
      toolCallId: request.toolCallId,
      toolName: request.toolName,
      text: request.text,
      // Only sent for an archive, so a tool-result put is byte-identical to before.
      ...(request.kind === "history" ? { kind: "history" } : {}),
    });
    const idempotencyKey = `${request.runId}:${request.toolCallId}`;
    const deadline = this.#clock.monotonic() + timeoutMs;
    const timedOut = () =>
      new Error(`offload of ${idempotencyKey} timed out after ${timeoutMs} ms`);
    for (let attempt = 1; ; attempt++) {
      const token = this.#token();
      let res: Attempt;
      try {
        res = await this.#send(
          url,
          {
            method: "POST",
            headers: {
              ...this.#headers(idempotencyKey, token),
              "content-type": "application/json",
            },
            body,
          },
          request.signal,
          deadline - this.#clock.monotonic(),
        );
      } catch (err) {
        if (err instanceof Timeout) throw timedOut();
        throw err;
      }
      if (res.kind === "response" && res.status === 200) {
        const out = parseOffloadResult(res.body);
        if (!out) throw new Error(`offload ${idempotencyKey}: the gateway sent an invalid result`);
        return out;
      }
      this.#throwIfFenceRejected(res, token, { idempotencyKey });
      if (!isRetryableAttempt(res) || attempt >= this.#retry.attempts) {
        throw new Error(
          `offload ${idempotencyKey} failed: ${whyFailed(res, this.#maxResponseBytes)}`,
        );
      }
      try {
        await this.#backoff(attempt, request.signal, deadline, retryAfterOf(res));
      } catch (err) {
        if (err instanceof Timeout) throw timedOut();
        throw err;
      }
    }
  }

  tool(info: GatewayToolInfo): ToolDefinition<JsonObject> {
    const replay = gatewayReplayPolicy(info);
    return defineTool<JsonObject>({
      name: info.name,
      description: info.description,
      parameters: gatewayParameters(info.parameters),
      replay,
      source: "gateway",
      untrustedOutput: info.untrustedOutput,
      execute: (args, api) => this.#invoke(info, args, api),
      reconcile: (_args, api) => this.#reconcile(info, api.idempotencyKey, api.ctx.signal),
    });
  }

  #headers(
    idempotencyKey: string | undefined,
    token: number | bigint | null,
  ): Record<string, string> {
    const h: Record<string, string> = {
      authorization: `Bearer ${this.#options.serviceToken}`,
      accept: "application/json",
    };
    if (idempotencyKey !== undefined) h["idempotency-key"] = idempotencyKey;
    if (token !== null) h["x-engine-fence"] = String(token);
    if (this.#options.sessionId !== undefined) h["x-engine-session"] = this.#options.sessionId;
    return h;
  }

  /** The current fence token, or null without a lease. Throws `LeaseLost`. */
  #token(): number | bigint | null {
    const fence = this.#options.fence();
    return fence === null ? null : typeof fence === "object" ? fence.token : fence;
  }

  /** On a 409 `fence_rejected`: tell the host, then throw `LeaseLost`. */
  #throwIfFenceRejected(
    res: Attempt,
    token: number | bigint | null,
    where: { tool?: string; idempotencyKey?: string },
  ): void {
    if (!isFenceRejection(res)) return;
    if (token !== null) this.#options.onFenceRejected?.({ ...where, token });
    throw new LeaseLost(this.#options.sessionId ?? "", Number(token ?? 0));
  }

  async #invoke(
    info: GatewayToolInfo,
    args: JsonObject,
    api: {
      signal: AbortSignal;
      idempotencyKey: string;
      runId: string;
      toolCallId: string;
      conversation: ToolConversation;
    },
  ): Promise<ToolResult> {
    let token: number | bigint | null;
    try {
      token = this.#token();
    } catch (err) {
      if (err instanceof LeaseLost) {
        return failure("lease_lost", `${info.name} was not run: this worker lost its lease.`, {});
      }
      throw err;
    }
    const url = `${this.#base}/internal/tools/${encodeURIComponent(info.name)}/invoke`;
    const body = JSON.stringify({
      args,
      runId: api.runId,
      toolCallId: api.toolCallId,
      tenantId: api.conversation.tenantId,
      projectId: api.conversation.projectId,
      ...backendConversationIds(api.conversation),
    });
    const init: RequestInit = {
      method: "POST",
      headers: {
        ...this.#headers(api.idempotencyKey, token),
        "content-type": "application/json",
      },
      body,
    };
    const attempts = info.sideEffect === "none" ? this.#retry.attempts : 1;
    const deadline = this.#clock.monotonic() + info.timeoutMs;

    for (let attempt = 1; ; attempt++) {
      let res: Attempt;
      try {
        res = await this.#send(url, init, api.signal, deadline - this.#clock.monotonic());
      } catch (err) {
        if (err instanceof Timeout) return this.#timedOut(info, api);
        throw err;
      }
      if (res.kind === "too_large") return tooLargeFailure(info, res, this.#maxResponseBytes);
      if (res.kind === "response") {
        if (res.status >= 200 && res.status < 300) {
          const outcome = toResult(res.body);
          return (
            outcome ??
            failure("gateway_error", `${info.name}: the gateway sent an invalid result.`, {
              status: res.status,
            })
          );
        }
        if (isFenceRejection(res)) {
          if (token !== null) {
            this.#options.onFenceRejected?.({
              tool: info.name,
              idempotencyKey: api.idempotencyKey,
              token,
            });
          }
          return failure(
            "lease_lost",
            `${info.name} was not run: the gateway rejected this worker's lease.`,
            {},
          );
        }
      }
      if (!isRetryableAttempt(res) || attempt >= attempts) return httpFailure(info, res);
      try {
        await this.#backoff(attempt, api.signal, deadline, retryAfterOf(res));
      } catch (err) {
        if (err instanceof Timeout) return this.#timedOut(info, api);
        throw err;
      }
    }
  }

  /**
   * The result for a call past its deadline. A non-idempotent write may have
   * run (the gateway does not cancel on disconnect), so ask once for a stored
   * outcome before reporting the outcome unknown.
   */
  async #timedOut(
    info: GatewayToolInfo,
    api: { signal: AbortSignal; idempotencyKey: string },
  ): Promise<ToolResult> {
    const message = `${info.name} timed out after ${info.timeoutMs} ms.`;
    if (gatewayReplayPolicy(info) === "safe") {
      return failure("timeout", message, { timeoutMs: info.timeoutMs });
    }
    try {
      const stored = await this.#reconcileOnce(
        info,
        api.idempotencyKey,
        api.signal,
        Math.min(info.timeoutMs, TIMEOUT_RECONCILE_MS),
      );
      if (stored) return stored;
    } catch {
      // Unreachable, fenced or failing: the outcome stays unknown.
      if (api.signal.aborted) throw api.signal.reason;
    }
    return failure("timeout", `${message}${UNKNOWN_OUTCOME}`, {
      timeoutMs: info.timeoutMs,
      outcome: "unknown",
    });
  }

  /**
   * The outcome stored under `key`, or null when there is none. Fenced, so a
   * successor's reconcile raises the gateway's high-water mark before the
   * previous owner's delayed invoke can land.
   */
  async #reconcile(
    info: GatewayToolInfo,
    key: string,
    signal: AbortSignal,
    attempts = this.#retry.attempts,
  ): Promise<ToolResult | null> {
    const url = `${this.#base}/internal/tools/invocations/${encodeURIComponent(key)}`;
    for (let attempt = 1; ; attempt++) {
      const token = this.#token();
      const res = await this.#send(
        url,
        { method: "GET", headers: this.#headers(undefined, token) },
        signal,
        info.timeoutMs,
      );
      if (res.kind === "response") {
        if (res.status === 404) return null;
        if (res.status === 200) {
          const outcome = toResult(res.body);
          if (!outcome) throw new Error(`gateway sent an invalid stored outcome for ${key}`);
          return outcome;
        }
      }
      this.#throwIfFenceRejected(res, token, { tool: info.name, idempotencyKey: key });
      if (!isRetryableAttempt(res) || attempt >= attempts) {
        throw new Error(
          `gateway reconcile for ${key} failed: ${whyFailed(res, this.#maxResponseBytes)}`,
        );
      }
      await this.#backoff(attempt, signal, undefined, retryAfterOf(res));
    }
  }

  /** One reconcile attempt within `timeoutMs` (after a write timed out). */
  #reconcileOnce(
    info: GatewayToolInfo,
    key: string,
    signal: AbortSignal,
    timeoutMs: number,
  ): Promise<ToolResult | null> {
    return this.#reconcile({ ...info, timeoutMs }, key, signal, 1);
  }

  /**
   * One HTTP exchange. Rejects with the caller's reason when `signal` aborts
   * and with {@link Timeout} when `timeoutMs` passes; network failures and
   * HTTP statuses are returned.
   */
  async #send(
    url: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
    timeoutMs: number,
  ): Promise<Attempt> {
    signal?.throwIfAborted();
    if (timeoutMs <= 0) throw new Timeout();
    const timer = new AbortController();
    const cancel = this.#clock.setTimer(timeoutMs, () => timer.abort(new Timeout()));
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

  /**
   * Wait before retry `attempt + 1`: the server's `Retry-After` when given,
   * else full jitter (uniform in [0, min(max, base * 2^(attempt-1))]).
   * Throws {@link Timeout} when the wait would pass `deadline`.
   */
  async #backoff(
    attempt: number,
    signal: AbortSignal | undefined,
    deadline?: number,
    retryAfterMs?: number,
  ): Promise<void> {
    const { baseDelayMs, maxDelayMs } = this.#retry;
    const delay =
      retryAfterMs !== undefined
        ? deadline === undefined
          ? Math.min(retryAfterMs, MAX_RETRY_AFTER_MS)
          : retryAfterMs
        : Math.floor(this.#random() * Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1)));
    if (deadline !== undefined && this.#clock.monotonic() + delay >= deadline) throw new Timeout();
    await this.#clock.sleep(delay, signal);
  }
}

// ---------------------------------------------------------------------------

function isRetryable(status: number): boolean {
  return status >= 500 || status === 429;
}

function isRetryableAttempt(res: Attempt): boolean {
  return res.kind === "network" || (res.kind === "response" && isRetryable(res.status));
}

function isFenceRejection(res: Attempt): boolean {
  return res.kind === "response" && res.status === 409 && errorOf(res.body) === "fence_rejected";
}

function retryAfterOf(res: Attempt): number | undefined {
  return res.kind === "response" ? res.retryAfterMs : undefined;
}

/** `Retry-After` (delta-seconds or an HTTP date) in ms, or undefined. */
export function parseRetryAfter(value: string | null, nowMs: number): number | undefined {
  if (value === null) return undefined;
  const v = value.trim();
  if (/^\d+$/.test(v)) return Number(v) * 1_000;
  const at = Date.parse(v);
  return Number.isNaN(at) ? undefined : Math.max(0, at - nowMs);
}

/** The body as text, or null when it exceeds `max` bytes (the rest is not read). */
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

function whyFailed(res: Attempt, maxBytes: number): string {
  if (res.kind === "network") return errorText(res.error);
  if (res.kind === "too_large") return `${res.status}, response body over ${maxBytes} bytes`;
  return `${res.status} ${messageOf(res.body)}`;
}

function tooLargeFailure(
  info: GatewayToolInfo,
  res: { status: number },
  maxBytes: number,
): ToolResult {
  const ran = res.status >= 200 && res.status < 300;
  const note =
    ran && info.sideEffect === "write"
      ? " The call ran; do not call it again to get the result."
      : "";
  return failure(
    "gateway_error",
    `${info.name} failed: the gateway's response (${res.status}) exceeded ${maxBytes} bytes.${note}`,
    { status: res.status, reason: "response_too_large" },
  );
}

function failure(code: GatewayErrorCode, content: string, extra: JsonObject): ToolResult {
  return { content, isError: true, details: { error: code, ...extra } };
}

function httpFailure(
  info: GatewayToolInfo,
  res: Exclude<Attempt, { kind: "too_large" }>,
): ToolResult {
  const unsent =
    info.sideEffect === "write" ? " It was not retried; check before calling again." : "";
  if (res.kind === "network") {
    return failure(
      "gateway_unavailable",
      `${info.name} failed: the gateway could not be reached (${errorText(res.error)}).${unsent}`,
      {},
    );
  }
  const message = messageOf(res.body);
  return failure(
    "gateway_error",
    `${info.name} failed: the gateway answered ${res.status}${message ? ` (${message})` : ""}.${res.status >= 500 ? unsent : ""}`,
    { status: res.status, ...(message ? { message } : {}) },
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

/** `{ path, bytes }` from the offload route, or null when it is malformed. */
function parseOffloadResult(body: JsonValue | null): OffloadPutResult | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const { path, bytes } = body;
  if (typeof path !== "string" || path.length === 0) return null;
  if (typeof bytes !== "number" || !Number.isSafeInteger(bytes) || bytes < 0) return null;
  return { path, bytes };
}

/** A gateway outcome as a tool result, or null when it is malformed. */
function toResult(body: JsonValue | null): ToolResult | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const { content, isError, details } = body;
  let parts: string | TextContent[];
  if (typeof content === "string") parts = content;
  else if (
    Array.isArray(content) &&
    content.every(
      (p) =>
        p !== null &&
        typeof p === "object" &&
        !Array.isArray(p) &&
        p.type === "text" &&
        typeof p.text === "string",
    )
  ) {
    parts = content.map((p) => ({ type: "text", text: (p as { text: string }).text }));
  } else return null;
  if (isError !== undefined && typeof isError !== "boolean") return null;
  return {
    content: parts,
    ...(isError === true ? { isError: true } : {}),
    ...(details !== undefined && details !== null ? { details } : {}),
  };
}

function parseManifest(body: JsonValue | null): GatewayToolInfo[] {
  if (!body || typeof body !== "object" || Array.isArray(body) || !Array.isArray(body.tools)) {
    throw new TypeError("gateway manifest: expected { tools: [...] }");
  }
  const seen = new Set<string>();
  return body.tools.map((raw, i) => {
    const at = `gateway manifest tools[${i}]`;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new TypeError(`${at}: expected an object`);
    }
    const {
      name,
      description,
      parameters,
      sideEffect,
      idempotent,
      timeoutMs,
      requiresApproval,
      untrustedOutput,
    } = raw;
    if (typeof name !== "string") throw new TypeError(`${at}: name must be a string`);
    if (seen.has(name)) throw new TypeError(`${at}: duplicate tool ${name}`);
    seen.add(name);
    if (typeof description !== "string") {
      throw new TypeError(`${at} (${name}): description must be a string`);
    }
    if (
      !parameters ||
      typeof parameters !== "object" ||
      Array.isArray(parameters) ||
      parameters.type !== "object"
    ) {
      throw new TypeError(`${at} (${name}): parameters must be an object JSON Schema`);
    }
    if (sideEffect !== "none" && sideEffect !== "write") {
      throw new TypeError(`${at} (${name}): sideEffect must be "none" or "write"`);
    }
    if (idempotent !== undefined && typeof idempotent !== "boolean") {
      throw new TypeError(`${at} (${name}): idempotent must be a boolean`);
    }
    if (typeof timeoutMs !== "number" || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
      throw new TypeError(`${at} (${name}): timeoutMs must be a positive integer`);
    }
    if (requiresApproval !== undefined && typeof requiresApproval !== "boolean") {
      throw new TypeError(`${at} (${name}): requiresApproval must be a boolean`);
    }
    if (untrustedOutput !== undefined && typeof untrustedOutput !== "boolean") {
      throw new TypeError(`${at} (${name}): untrustedOutput must be a boolean`);
    }
    return {
      name,
      description,
      parameters: structuredClone(parameters),
      sideEffect,
      idempotent: idempotent === true,
      timeoutMs,
      requiresApproval: requiresApproval === true,
      untrustedOutput: untrustedOutput === true,
    };
  });
}
