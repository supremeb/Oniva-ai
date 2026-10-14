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
 * carry the service token, `Idempotency-Key: runId:toolCallId` and, when the
 * worker holds a lease, `X-Engine-Fence: <token>`.
 *
 * Failures become error results rather than throws, with `details.error`:
 * - `timeout`: the tool's `timeoutMs` passed (covering retries); the request
 *   is aborted;
 * - `lease_lost`: the gateway rejected the fence token (409
 *   `fence_rejected`), or the lease was already lost; `onFenceRejected` is
 *   called so the host can fence the worker;
 * - `gateway_error` (an HTTP error status) or `gateway_unavailable` (network
 *   failure). Writes are sent exactly once; reads retry 5xx, 429 and network
 *   failures with exponential backoff.
 * A cancelled call (the caller's signal) rejects with the signal's reason.
 *
 * The contract is specified by the fake gateway in
 * `test/support/fake-gateway.ts`.
 */
import { z } from "zod";
import { type Clock, realClock } from "../core/clock.js";
import { LeaseLost } from "../core/errors.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import type { TextContent } from "../models/types.js";
import type { Fence } from "../storage/types.js";
import {
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
  tool: string;
  idempotencyKey: string;
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
}

export type GatewayErrorCode = "timeout" | "lease_lost" | "gateway_error" | "gateway_unavailable";

const DEFAULT_RETRY: GatewayRetryOptions = { attempts: 3, baseDelayMs: 200, maxDelayMs: 2_000 };

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
  };
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
  | { kind: "response"; status: number; body: JsonValue | null }
  | { kind: "network"; error: unknown };

class Timeout extends Error {}

class GatewayHttp {
  readonly #base: string;
  readonly #options: GatewayClientOptions;
  readonly #fetch: typeof fetch;
  readonly #clock: Clock;
  readonly #retry: GatewayRetryOptions;

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
  }

  async manifest(): Promise<GatewayToolInfo[]> {
    const url = `${this.#base}/internal/tools/manifest`;
    const timeoutMs = this.#options.manifestTimeoutMs ?? 10_000;
    for (let attempt = 1; ; attempt++) {
      const res = await this.#send(url, { method: "GET" }, this.#options.signal, timeoutMs);
      if (res.kind === "response" && res.status === 200) return parseManifest(res.body);
      const retryable = res.kind === "network" || isRetryable(res.status);
      if (!retryable || attempt >= this.#retry.attempts) {
        const why =
          res.kind === "network" ? errorText(res.error) : `${res.status} ${messageOf(res.body)}`;
        throw new Error(`gateway manifest load failed: ${why}`);
      }
      await this.#backoff(attempt, this.#options.signal);
    }
  }

  tool(info: GatewayToolInfo): ToolDefinition<JsonObject> {
    const replay = gatewayReplayPolicy(info);
    return defineTool<JsonObject>({
      name: info.name,
      description: info.description,
      parameters: gatewayParameters(info.parameters),
      replay,
      execute: (args, api) => this.#invoke(info, args, api),
      reconcile: (_args, api) => this.#reconcile(info, api.idempotencyKey, api.ctx.signal),
    });
  }

  #headers(idempotencyKey?: string, fence?: string): Record<string, string> {
    const h: Record<string, string> = {
      authorization: `Bearer ${this.#options.serviceToken}`,
      accept: "application/json",
    };
    if (idempotencyKey !== undefined) h["idempotency-key"] = idempotencyKey;
    if (fence !== undefined) h["x-engine-fence"] = fence;
    if (this.#options.sessionId !== undefined) h["x-engine-session"] = this.#options.sessionId;
    return h;
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
      const fence = this.#options.fence();
      token = fence === null ? null : typeof fence === "object" ? fence.token : fence;
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
    });
    const init: RequestInit = {
      method: "POST",
      headers: {
        ...this.#headers(api.idempotencyKey, token === null ? undefined : String(token)),
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
        if (err instanceof Timeout) {
          return failure("timeout", `${info.name} timed out after ${info.timeoutMs} ms.`, {
            timeoutMs: info.timeoutMs,
          });
        }
        throw err;
      }
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
        if (res.status === 409 && errorOf(res.body) === "fence_rejected") {
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
      const retryable = res.kind === "network" || isRetryable(res.status);
      if (!retryable || attempt >= attempts) return httpFailure(info, res);
      try {
        await this.#backoff(attempt, api.signal, deadline);
      } catch (err) {
        if (err instanceof Timeout) {
          return failure("timeout", `${info.name} timed out after ${info.timeoutMs} ms.`, {
            timeoutMs: info.timeoutMs,
          });
        }
        throw err;
      }
    }
  }

  async #reconcile(
    info: GatewayToolInfo,
    key: string,
    signal: AbortSignal,
  ): Promise<ToolResult | null> {
    const url = `${this.#base}/internal/tools/invocations/${encodeURIComponent(key)}`;
    for (let attempt = 1; ; attempt++) {
      const res = await this.#send(
        url,
        { method: "GET", headers: this.#headers() },
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
      const retryable = res.kind === "network" || isRetryable(res.status);
      if (!retryable || attempt >= this.#retry.attempts) {
        const why =
          res.kind === "network" ? errorText(res.error) : `${res.status} ${messageOf(res.body)}`;
        throw new Error(`gateway reconcile for ${key} failed: ${why}`);
      }
      await this.#backoff(attempt, signal);
    }
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
    const headers = init.headers ?? this.#headers();
    try {
      const res = await this.#fetch(url, { ...init, headers, signal: combined });
      const text = await res.text();
      let body: JsonValue | null = null;
      if (text.length > 0) {
        try {
          body = JSON.parse(text) as JsonValue;
        } catch {
          body = text;
        }
      }
      return { kind: "response", status: res.status, body };
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
    deadline?: number,
  ): Promise<void> {
    const { baseDelayMs, maxDelayMs } = this.#retry;
    const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1));
    if (deadline !== undefined && this.#clock.monotonic() + delay >= deadline) throw new Timeout();
    await this.#clock.sleep(delay, signal);
  }
}

// ---------------------------------------------------------------------------

function isRetryable(status: number): boolean {
  return status >= 500 || status === 429;
}

function failure(code: GatewayErrorCode, content: string, extra: JsonObject): ToolResult {
  return { content, isError: true, details: { error: code, ...extra } };
}

function httpFailure(info: GatewayToolInfo, res: Attempt): ToolResult {
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

function errorText(err: unknown): string {
  if (err instanceof Error) {
    const cause = (err as { cause?: unknown }).cause;
    return cause instanceof Error ? `${err.message}: ${cause.message}` : err.message;
  }
  return String(err);
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
    const { name, description, parameters, sideEffect, idempotent, timeoutMs, requiresApproval } =
      raw;
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
    return {
      name,
      description,
      parameters: structuredClone(parameters),
      sideEffect,
      idempotent: idempotent === true,
      timeoutMs,
      requiresApproval: requiresApproval === true,
    };
  });
}
