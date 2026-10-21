/**
 * Backend-managed model policy and usage reporting (U9).
 *
 * With `ENGINE_POLICY=backend` the worker does not hold a model key. For each
 * model request it asks the backend (`POST /internal/models/policy`) which
 * provider, model, key and token budget to use, and after each response it
 * reports the spend (`POST /internal/models/usage`).
 *
 * - The request id is `runId:round:purpose` on both calls. The backend answers
 *   a repeated policy request the same way and records a usage request id once,
 *   so a retried report cannot double-count.
 * - A denial is HTTP 200 `{ ok: false, code, reason }` and becomes a
 *   `ModelResolution` denial with the backend's code. It is definitive and is
 *   never retried, nor are 401/403 or any other 4xx besides 408/429.
 * - A transient failure (network error, timeout, 408, 429, 5xx) is retried
 *   with capped exponential backoff and jitter, honouring `Retry-After`, for
 *   at most `retryMs` (default 60 s) in total, reusing the same `requestId`
 *   (the backend caches a decision per id) and stopping at once when the
 *   run's signal aborts. Each retry logs `policy_retry`. Only when the budget
 *   is spent does the request fail, with `policy_unavailable` marked
 *   `retryable` (a `network` model error, not `denied`). A non-transient
 *   unexpected answer (403, malformed body) fails at once, non-retryable.
 *   There is never a fallback to a local key.
 * - The key lives only in the returned resolution for that request. It is
 *   never logged, stored or cached here, and error text never includes a
 *   response body.
 * - Tool policy (`authorizeTool`, Approvals & Policy plan U5) delegates every
 *   tool decision to `POST /internal/tools/authorize`; the worker keeps no
 *   policy of its own beyond skipping the round trip for a manifest read
 *   (`sideEffect: "none"`) and for the internal native tools (except
 *   `ask_user`, which is sent so the question is indexed). The body carries
 *   the arguments redacted by `redact.ts` and `argumentsHash` over the
 *   unredacted final arguments. A transient failure retries on the same budget
 *   as above with the same `requestId`; once it is spent, or on a 401/403/other
 *   4xx or a malformed answer (immediately, no retry), the call is blocked with
 *   reason `policy_unavailable`. There is never a local fallback. The approval
 *   expiry is the backend's `approvalTtlMs` applied to the injected clock;
 *   `null` means the request never expires. Arguments and response bodies are
 *   never logged.
 * - Usage reporting never blocks the conversation: `afterResponse` starts the
 *   report in the background; network errors, 408, 429 and 5xx are retried
 *   with the same backoff and the same time budget as policy (`usage_retry`,
 *   then `usage_failed`); 401/403 are logged `usage_auth_error` and the outbox
 *   row is kept for redelivery after the token is fixed; other statuses are
 *   logged and dropped (`usage_dropped`).
 *   `flush` waits for reports in flight (each bounded by the budget) at shutdown.
 * - Durable outbox (U6, KTD6): with an outbox every report is enqueued (claimed)
 *   before its first attempt and deleted only on a 2xx, so a report survives a
 *   backend outage and a worker restart. The flusher reclaims undelivered rows
 *   (unclaimed or past the claim timeout, oldest first) at start and every
 *   `flushIntervalMs`; the backend ledger dedupes `(tenant, requestId)`, so a
 *   redelivery is counted once. A row older than `staleAgeMs` is logged
 *   `usage_stale` but still delivered. Without an outbox reports are memory-only.
 */
import type { AfterCompactionEvent, AfterResponseEvent, AgentHooks } from "../agent/hooks.js";
import type { ModelRequestInfo, ToolAuthorizationRequest, ToolVerdict } from "../agent/policy.js";
import { type Clock, realClock } from "../core/clock.js";
import type { JsonValue } from "../core/json.js";
import type { ModelResolution } from "../models/types.js";
import type { RedactTarget } from "../session/transaction.js";
import type { UsageOutbox, UsageOutboxRecord } from "../storage/types.js";
import { backendConversationIds, INTERNAL_NATIVE_TOOLS } from "../tools/registry.js";
import { argsHash } from "./args-hash.js";
import { maskSecrets } from "./redact.js";

export type BackendLog = (event: string, fields?: Record<string, unknown>) => void;

export const DEFAULT_POLICY_TIMEOUT_MS = 5_000;
/** Total time a policy request or usage report keeps retrying a transient failure. */
export const DEFAULT_RETRY_MS = 60_000;
const DEFAULT_BASE_DELAY_MS = 250;
const DEFAULT_MAX_DELAY_MS = 5_000;

/** Test seams shared by the policy client and the usage reporter. */
export interface RetryOptions {
  /** Total retry budget in ms; `0` disables retrying. Default 60 s. */
  retryMs?: number;
  /** First retry delay; doubles each time up to `maxDelayMs`. Default 250 ms. */
  baseDelayMs?: number;
  maxDelayMs?: number;
  /** Wait; must resolve early when `signal` aborts. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  now?: () => number;
  /** Jitter source in [0, 1). */
  random?: () => number;
}

export interface BackendPolicyOptions extends RetryOptions {
  /** Backend origin, e.g. `https://api.example.com`; `/internal/models/...` is appended. */
  baseUrl: string;
  /** The `ENGINE_GATEWAY_TOKEN` bearer. */
  serviceToken: string;
  /** Provider names registered in this worker, sent so the backend picks a usable one. */
  supportedProviders: () => string[];
  /** Per-request timeout. Default 5 s. */
  timeoutMs?: number;
  fetch?: typeof fetch;
  log?: BackendLog;
  /** Clock for approval expiry (`expiresAt = now + approvalTtlMs`). Default: real time. */
  clock?: Clock;
}

/** What the worker knows about a gateway tool from its manifest. */
export interface ToolManifestLookup {
  describe?(name: string): { sideEffect: "none" | "write" } | undefined;
}

export const requestIdFor = (runId: string, round: number, purpose: string): string =>
  `${runId}:${round}:${purpose}`;

export const trimSlash = (s: string): string => s.replace(/\/+$/, "");

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Short, key-free description of a thrown value. */
export function errorName(err: unknown): string {
  if (err instanceof Error) return err.name === "Error" ? err.message.slice(0, 200) : err.name;
  return "error";
}

function unavailable(reason: string, transient = false): ModelResolution {
  return {
    ok: false,
    code: "policy_unavailable",
    reason: `model policy unavailable: ${reason}`,
    ...(transient ? { transient: true } : {}),
  };
}

/** Redaction target of the arguments sent to the backend (same masking as stored task input). */
const ARGS_TARGET: RedactTarget = { record: "task", kind: "agent.tool", field: "input" };

/** Tool authorization when the backend cannot be reached or answers nonsense: closed. */
const UNAVAILABLE: ToolVerdict = { decision: "block", reason: "policy_unavailable" };

/** Network errors, timeouts and these statuses are worth another try. */
export const isTransientStatus = (status: number): boolean =>
  status === 408 || status === 429 || status >= 500;

/** `Retry-After` as ms (delta seconds or an HTTP date), or null when absent or unusable. */
export function retryAfterMs(header: string | null, now: number): number | null {
  if (header === null) return null;
  const v = header.trim();
  if (/^\d+(\.\d+)?$/.test(v)) return Math.round(Number(v) * 1000);
  const at = Date.parse(v);
  return Number.isNaN(at) ? null : Math.max(0, at - now);
}

/** Abortable wait: resolves when the time is up or `signal` aborts. */
const realSleep = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise<void>((resolve) => {
    if (signal?.aborted) return resolve();
    const done = (): void => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  });

/** Backoff schedule and budget shared by both clients. */
export class Backoff {
  readonly retryMs: number;
  readonly sleep: (ms: number, signal?: AbortSignal) => Promise<void>;
  readonly now: () => number;
  readonly #base: number;
  readonly #max: number;
  readonly #random: () => number;

  constructor(o: RetryOptions) {
    this.retryMs = Math.max(0, o.retryMs ?? DEFAULT_RETRY_MS);
    this.#base = Math.max(1, o.baseDelayMs ?? DEFAULT_BASE_DELAY_MS);
    this.#max = Math.max(this.#base, o.maxDelayMs ?? DEFAULT_MAX_DELAY_MS);
    this.sleep = o.sleep ?? realSleep;
    this.now = o.now ?? Date.now;
    this.#random = o.random ?? Math.random;
  }

  /**
   * Delay before retry number `attempt` (1-based): the server's `Retry-After`
   * when it sent one, else exponential growth capped at the maximum with
   * jitter in [50%, 100%]. Null when waiting would pass the deadline.
   */
  delay(attempt: number, deadline: number, retryAfter: number | null): number | null {
    const capped = Math.min(this.#max, this.#base * 2 ** Math.min(attempt - 1, 30));
    const delay = retryAfter ?? Math.max(1, Math.round(capped * (0.5 + this.#random() * 0.5)));
    return delay > deadline - this.now() ? null : delay;
  }
}

/** Asks the backend for each model request's resolution. */
export class BackendPolicyClient {
  readonly #url: string;
  readonly #token: string;
  readonly #providers: () => string[];
  /** Context windows the backend reported, by resolved model id. */
  readonly #windows = new Map<string, number>();
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;
  readonly #log: BackendLog;
  readonly #backoff: Backoff;
  readonly #authorizeUrl: string;
  readonly #clock: Clock;

  constructor(options: BackendPolicyOptions) {
    this.#url = `${trimSlash(options.baseUrl)}/internal/models/policy`;
    this.#authorizeUrl = `${trimSlash(options.baseUrl)}/internal/tools/authorize`;
    this.#clock = options.clock ?? realClock;
    this.#token = options.serviceToken;
    this.#providers = options.supportedProviders;
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_POLICY_TIMEOUT_MS;
    this.#fetch = options.fetch ?? fetch;
    this.#log = options.log ?? (() => {});
    this.#backoff = new Backoff(options);
  }

  /** The context window the backend last reported for `model`, if any. */
  contextWindowFor(model: string): number | undefined {
    return this.#windows.get(model);
  }

  async resolveModelRequest(info: ModelRequestInfo): Promise<ModelResolution> {
    const requestId = requestIdFor(info.runId, info.round, info.purpose);
    const body = JSON.stringify({
      tenantId: info.conversation.tenantId,
      projectId: info.conversation.projectId,
      ...backendConversationIds(info.conversation),
      runId: info.runId,
      round: info.round,
      purpose: info.purpose,
      requestId,
      agent: info.agent.model ? { model: info.agent.model } : {},
      supportedProviders: this.#providers(),
    });
    const signal = info.ctx.signal;
    const deadline = this.#backoff.now() + this.#backoff.retryMs;
    for (let attempt = 1; ; attempt++) {
      if (signal.aborted) return unavailable("cancelled");
      const outcome = await this.#attempt(body, requestId, signal);
      if (outcome.done) return outcome.resolution;
      if (signal.aborted) return unavailable("cancelled");
      const delay = this.#backoff.delay(attempt, deadline, outcome.retryAfterMs);
      if (delay === null) {
        const why = attempt === 1 ? outcome.why : `${outcome.why} (after ${attempt} attempts)`;
        this.#log("policy_error", { requestId, error: outcome.why, attempts: attempt });
        return unavailable(why, true);
      }
      this.#log("policy_retry", { requestId, attempt, delayMs: delay, error: outcome.why });
      await this.#backoff.sleep(delay, signal);
    }
  }

  /**
   * The verdict for one tool call. `manifest` is the gateway manifest, used
   * only to skip the round trip for a read.
   */
  async authorizeTool(
    request: ToolAuthorizationRequest,
    manifest?: ToolManifestLookup | null,
  ): Promise<ToolVerdict> {
    const info =
      request.toolSource === "gateway" ? manifest?.describe?.(request.toolName) : undefined;
    if (info?.sideEffect === "none") return { decision: "allow" };
    if (
      request.toolSource === "native" &&
      request.toolName !== "ask_user" &&
      (INTERNAL_NATIVE_TOOLS.includes(request.toolName) || request.toolInternal === true)
    ) {
      return { decision: "allow" }; // includes every subagent-spawn tool (flagged `internal`)
    }
    const requestId = `${request.runId}:${request.toolCallId}`;
    const body = JSON.stringify({
      requestId,
      tenantId: request.conversation.tenantId,
      projectId: request.conversation.projectId,
      ...backendConversationIds(request.conversation),
      ...(request.conversation.agentRole !== undefined
        ? { agentRole: request.conversation.agentRole }
        : {}),
      runId: request.runId,
      toolCallId: request.toolCallId,
      toolTaskId: request.toolTaskId,
      toolName: request.toolName,
      toolSource: request.toolSource,
      arguments: maskSecrets(request.arguments, ARGS_TARGET),
      argumentsHash: argsHash(request.arguments),
      ...(info ? { sideEffect: info.sideEffect } : {}),
      replay: request.replay,
      taint: {
        tainted: request.taint.tainted,
        sources: request.taint.sources,
      },
    });
    const deadline = this.#backoff.now() + this.#backoff.retryMs;
    for (let attempt = 1; ; attempt++) {
      const outcome = await this.#authorizeAttempt(body, requestId);
      if (outcome.done) return outcome.verdict;
      const delay = this.#backoff.delay(attempt, deadline, outcome.retryAfterMs);
      if (delay === null) {
        this.#log("policy_error", { requestId, error: outcome.why, attempts: attempt });
        return UNAVAILABLE;
      }
      this.#log("policy_retry", { requestId, attempt, delayMs: delay, error: outcome.why });
      await this.#backoff.sleep(delay);
    }
  }

  async #authorizeAttempt(
    body: string,
    requestId: string,
  ): Promise<
    { done: true; verdict: ToolVerdict } | { done: false; why: string; retryAfterMs: number | null }
  > {
    const timeout = AbortSignal.timeout(this.#timeoutMs);
    let res: Response;
    let json: unknown;
    try {
      res = await this.#fetch(this.#authorizeUrl, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.#token}`,
          "content-type": "application/json",
        },
        body,
        signal: timeout,
      });
      if (res.ok) json = await res.json();
      else await res.body?.cancel().catch(() => {});
    } catch (err) {
      return {
        done: false,
        why: timeout.aborted ? "timed out" : errorName(err),
        retryAfterMs: null,
      };
    }
    if (!res.ok) {
      const why = `backend answered ${res.status}`;
      if (isTransientStatus(res.status)) {
        const after =
          res.status === 429 || res.status === 503
            ? retryAfterMs(res.headers.get("retry-after"), this.#backoff.now())
            : null;
        return { done: false, why, retryAfterMs: after };
      }
      this.#log("policy_error", { requestId, status: res.status });
      return { done: true, verdict: UNAVAILABLE };
    }
    return { done: true, verdict: this.#parseVerdict(json, requestId) };
  }

  #parseVerdict(json: unknown, requestId: string): ToolVerdict {
    if (isRecord(json)) {
      if (json.decision === "allow") return { decision: "allow" };
      if (json.decision === "block") {
        return {
          decision: "block",
          reason:
            typeof json.reason === "string" && json.reason !== ""
              ? json.reason
              : "blocked by policy",
        };
      }
      const ttl = json.approvalTtlMs;
      if (
        json.decision === "requireApproval" &&
        (ttl === null || (typeof ttl === "number" && Number.isFinite(ttl) && ttl >= 0))
      ) {
        return {
          decision: "requireApproval",
          ...(typeof json.reason === "string" ? { reason: json.reason } : {}),
          ...(ttl === null ? {} : { expiresAt: this.#clock.now() + (ttl as number) }),
        };
      }
    }
    this.#log("policy_error", { requestId, error: "malformed response" });
    return UNAVAILABLE;
  }

  /** One POST: a final resolution, or a transient failure to retry. */
  async #attempt(
    body: string,
    requestId: string,
    ctxSignal: AbortSignal,
  ): Promise<
    | { done: true; resolution: ModelResolution }
    | { done: false; why: string; retryAfterMs: number | null }
  > {
    const timeout = AbortSignal.timeout(this.#timeoutMs);
    const signal = AbortSignal.any([ctxSignal, timeout]);
    let res: Response;
    let json: unknown;
    try {
      res = await this.#fetch(this.#url, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.#token}`,
          "content-type": "application/json",
        },
        body,
        signal,
      });
      if (res.ok) json = await res.json();
      else await res.body?.cancel().catch(() => {});
    } catch (err) {
      const why = timeout.aborted ? "timed out" : errorName(err);
      if (ctxSignal.aborted) return { done: true, resolution: unavailable("cancelled") };
      return { done: false, why, retryAfterMs: null };
    }
    if (!res.ok) {
      const why = `backend answered ${res.status}`;
      if (isTransientStatus(res.status)) {
        const after =
          res.status === 429 || res.status === 503
            ? retryAfterMs(res.headers.get("retry-after"), this.#backoff.now())
            : null;
        return { done: false, why, retryAfterMs: after };
      }
      this.#log("policy_error", { requestId, status: res.status });
      return { done: true, resolution: unavailable(why) };
    }
    return { done: true, resolution: this.#parse(json, requestId) };
  }

  #parse(json: unknown, requestId: string): ModelResolution {
    if (isRecord(json) && json.ok === false) {
      const code = typeof json.code === "string" ? json.code : "policy_denied";
      const reason = typeof json.reason === "string" ? json.reason : "the model request was denied";
      return { ok: false, code, reason };
    }
    if (
      isRecord(json) &&
      json.ok === true &&
      typeof json.provider === "string" &&
      typeof json.model === "string" &&
      typeof json.apiKey === "string" &&
      json.apiKey !== "" &&
      typeof json.maxTokens === "number" &&
      Number.isInteger(json.maxTokens) &&
      json.maxTokens > 0
    ) {
      const resolution: ModelResolution = {
        ok: true,
        provider: json.provider,
        model: json.model,
        apiKey: json.apiKey,
        maxTokens: json.maxTokens,
      };
      // Optional: older backends omit it. Remember it per model for compaction thresholds.
      if (
        typeof json.contextWindow === "number" &&
        Number.isInteger(json.contextWindow) &&
        json.contextWindow > 0
      ) {
        resolution.contextWindow = json.contextWindow;
        this.#windows.set(json.model, json.contextWindow);
      }
      return resolution;
    }
    this.#log("policy_error", { requestId, error: "malformed response" });
    return unavailable("malformed response");
  }
}

export interface UsageReporterOptions extends RetryOptions {
  baseUrl: string;
  serviceToken: string;
  /** Optional cap on attempts per report (first try included); default: limited by `retryMs` only. */
  attempts?: number;
  timeoutMs?: number;
  /**
   * Durable outbox (U6, KTD6). Set: every report is parked before its first
   * attempt and deleted on a 2xx, and the flusher re-drives anything left.
   * Unset: reports are held in memory only.
   */
  outbox?: UsageOutbox | null;
  /** How often the flusher re-drives undelivered reports. Default 30 s. */
  flushIntervalMs?: number;
  /** A claim older than this may be taken again by the flusher. Default 60 s. */
  claimTimeoutMs?: number;
  /** A report older than this is logged `usage_stale` but still delivered. Default 7 days. */
  staleAgeMs?: number;
  fetch?: typeof fetch;
  log?: BackendLog;
}

export const DEFAULT_USAGE_FLUSH_INTERVAL_MS = 30_000;
export const DEFAULT_USAGE_CLAIM_TIMEOUT_MS = 60_000;
export const DEFAULT_USAGE_STALE_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/** Posts each model response's usage to the backend ledger, in the background. */
export class UsageReporter {
  readonly #url: string;
  readonly #token: string;
  readonly #attempts: number;
  readonly #backoff: Backoff;
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;
  readonly #log: BackendLog;
  readonly #outbox: UsageOutbox | null;
  readonly #flushIntervalMs: number;
  readonly #claimTimeoutMs: number;
  readonly #staleAgeMs: number;
  readonly #pending = new Set<Promise<void>>();
  #flushTimer: ReturnType<typeof setInterval> | null = null;
  #closed = false;

  constructor(options: UsageReporterOptions) {
    this.#url = `${trimSlash(options.baseUrl)}/internal/models/usage`;
    this.#token = options.serviceToken;
    this.#attempts = Math.max(1, options.attempts ?? Number.POSITIVE_INFINITY);
    this.#backoff = new Backoff(options);
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_POLICY_TIMEOUT_MS;
    this.#fetch = options.fetch ?? fetch;
    this.#log = options.log ?? (() => {});
    this.#outbox = options.outbox ?? null;
    this.#flushIntervalMs = Math.max(1, options.flushIntervalMs ?? DEFAULT_USAGE_FLUSH_INTERVAL_MS);
    this.#claimTimeoutMs = Math.max(0, options.claimTimeoutMs ?? DEFAULT_USAGE_CLAIM_TIMEOUT_MS);
    this.#staleAgeMs = Math.max(0, options.staleAgeMs ?? DEFAULT_USAGE_STALE_AGE_MS);
  }

  /** Hooks to register with the harness. */
  get hooks(): AgentHooks {
    return {
      afterResponse: (event) => {
        this.report(event);
      },
      afterCompaction: (event) => {
        this.reportCompaction(event);
      },
    };
  }

  /** Start reporting one response; returns at once. */
  report(event: AfterResponseEvent): void {
    if (this.#closed) return;
    this.#start(requestIdFor(event.runId, event.round, "generation"), "generation", event);
  }

  /**
   * Start reporting one compaction summary request. The request id carries the
   * compaction task id, so a retried or re-run task posts the same id (and the
   * ledger records it once) while two compactions in one round stay distinct.
   */
  reportCompaction(event: AfterCompactionEvent): void {
    this.#start(
      `${requestIdFor(event.runId, event.round, "compaction")}:${event.taskId}`,
      "compaction",
      event,
    );
  }

  /**
   * Begin re-driving the outbox: once now, then every `flushIntervalMs`. A
   * no-op without an outbox. The timer is unref'd so it never holds the process.
   */
  startFlushing(): void {
    if (!this.#outbox || this.#flushTimer) return;
    this.#flushSafely();
    this.#flushTimer = setInterval(() => this.#flushSafely(), this.#flushIntervalMs);
    this.#flushTimer.unref?.();
  }

  /** Claim and deliver every due outbox row once (the worker's start-up flush). */
  async flushOutbox(): Promise<void> {
    const outbox = this.#outbox;
    if (!outbox) return;
    for (const record of await outbox.claimPending(this.#claimTimeoutMs, "usage")) {
      const job = this.#deliver(record, false).finally(() => this.#pending.delete(job));
      this.#pending.add(job);
    }
    await Promise.allSettled([...this.#pending]);
  }

  /** Reports still in flight. */
  get pending(): number {
    return this.#pending.size;
  }

  /** Wait for in-flight reports to finish (each is already bounded by its retries). */
  async flush(): Promise<void> {
    while (this.#pending.size > 0) await Promise.allSettled([...this.#pending]);
  }

  /** Stop accepting reports and the flusher timer, then wait for the ones in flight. */
  async close(): Promise<void> {
    this.#closed = true;
    if (this.#flushTimer) {
      clearInterval(this.#flushTimer);
      this.#flushTimer = null;
    }
    await this.flush();
  }

  #start(
    requestId: string,
    purpose: "generation" | "compaction",
    event: AfterResponseEvent | AfterCompactionEvent,
  ): void {
    if (this.#closed) return;
    const body = {
      tenantId: event.conversation.tenantId,
      projectId: event.conversation.projectId,
      ...backendConversationIds(event.conversation),
      runId: event.runId,
      requestId,
      purpose,
      ...(event.conversation.agentRole !== undefined
        ? { agentRole: event.conversation.agentRole }
        : {}),
      provider: event.message.provider,
      model: event.message.model,
      usage: {
        inputTokens: event.usage.inputTokens,
        outputTokens: event.usage.outputTokens,
        cacheReadTokens: event.usage.cacheReadTokens,
        cacheWriteTokens: event.usage.cacheWriteTokens,
      },
      status: "success",
    };
    const now = this.#backoff.now();
    const record: UsageOutboxRecord = { requestId, body, createdAt: now, claimedAt: now };
    const job = this.#deliver(record, true).finally(() => this.#pending.delete(job));
    this.#pending.add(job);
  }

  /**
   * Enqueue a fresh report, then send it; keep the row when the send gives up.
   * Never rejects: the job runs in the background with no handler. A failed
   * enqueue still sends (the report is just not durable); a failed ack leaves
   * the row, and the backend dedupes the redelivery.
   */
  async #deliver(record: UsageOutboxRecord, fresh: boolean): Promise<void> {
    const outbox = this.#outbox;
    try {
      if (fresh && outbox) {
        try {
          await outbox.enqueue(record);
        } catch (err) {
          this.#log("usage_outbox_error", {
            requestId: record.requestId,
            op: "enqueue",
            error: errorName(err),
          });
        }
      }
      const ageMs = this.#backoff.now() - record.createdAt;
      if (ageMs > this.#staleAgeMs) {
        this.#log("usage_stale", { requestId: record.requestId, ageMs });
      }
      if (await this.#send(record.requestId, record.body)) {
        try {
          await outbox?.ack(record.requestId);
        } catch (err) {
          this.#log("usage_outbox_error", {
            requestId: record.requestId,
            op: "ack",
            error: errorName(err),
          });
        }
      }
    } catch (err) {
      this.#log("usage_outbox_error", { requestId: record.requestId, error: errorName(err) });
    }
  }

  /** One report: retry to the budget. True when the row may be deleted, false to keep it. */
  async #send(requestId: string, body: JsonValue): Promise<boolean> {
    const payload = JSON.stringify(body);
    const deadline = this.#backoff.now() + this.#backoff.retryMs;
    for (let attempt = 1; ; attempt++) {
      let why = "";
      let retryAfter: number | null = null;
      try {
        const res = await this.#fetch(this.#url, {
          method: "POST",
          headers: {
            authorization: `Bearer ${this.#token}`,
            "content-type": "application/json",
          },
          body: payload,
          signal: AbortSignal.timeout(this.#timeoutMs),
        });
        await res.body?.cancel().catch(() => {});
        if (res.ok) return true;
        why = `status ${res.status}`;
        // Auth failures are fixed by rotating the token, so keep the row for redelivery.
        if (res.status === 401 || res.status === 403) {
          this.#log("usage_auth_error", { requestId, status: res.status });
          return false;
        }
        // A client error other than 408/429 will not change on retry.
        if (!isTransientStatus(res.status)) {
          this.#log("usage_dropped", { requestId, status: res.status });
          return true;
        }
        if (res.status === 429 || res.status === 503) {
          retryAfter = retryAfterMs(res.headers.get("retry-after"), this.#backoff.now());
        }
      } catch (err) {
        why = errorName(err);
      }
      const delay =
        attempt >= this.#attempts ? null : this.#backoff.delay(attempt, deadline, retryAfter);
      if (delay === null) {
        this.#log("usage_failed", { requestId, attempts: attempt, error: why });
        return false;
      }
      this.#log("usage_retry", { requestId, attempt, delayMs: delay, error: why });
      await this.#backoff.sleep(delay);
    }
  }

  #flushSafely(): void {
    void this.flushOutbox().catch((err: unknown) => {
      this.#log("usage_outbox_error", { error: errorName(err) });
    });
  }
}
