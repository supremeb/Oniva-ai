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
 *   `ModelResolution` denial with the backend's code. A transport failure, a
 *   timeout or any other unexpected answer denies with `policy_unavailable`;
 *   there is never a fallback to a local key.
 * - The key lives only in the returned resolution for that request. It is
 *   never logged, stored or cached here, and error text never includes a
 *   response body.
 * - Usage reporting never blocks the conversation: `afterResponse` starts the
 *   report in the background; network errors, 429 and 5xx are retried a few
 *   times with backoff; other statuses are logged and dropped. `flush` waits
 *   for reports in flight (bounded by the caller) at shutdown.
 */
import type { AfterResponseEvent, AgentHooks } from "../agent/hooks.js";
import type { ModelRequestInfo } from "../agent/policy.js";
import type { ModelResolution } from "../models/types.js";
import { backendConversationIds } from "../tools/registry.js";

export type BackendLog = (event: string, fields?: Record<string, unknown>) => void;

export const DEFAULT_POLICY_TIMEOUT_MS = 5_000;

export interface BackendPolicyOptions {
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
}

export const requestIdFor = (runId: string, round: number, purpose: string): string =>
  `${runId}:${round}:${purpose}`;

const trimSlash = (s: string): string => s.replace(/\/+$/, "");

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Short, key-free description of a thrown value. */
function errorName(err: unknown): string {
  if (err instanceof Error) return err.name === "Error" ? err.message.slice(0, 200) : err.name;
  return "error";
}

function unavailable(reason: string): ModelResolution {
  return { ok: false, code: "policy_unavailable", reason: `model policy unavailable: ${reason}` };
}

/** Asks the backend for each model request's resolution. */
export class BackendPolicyClient {
  readonly #url: string;
  readonly #token: string;
  readonly #providers: () => string[];
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;
  readonly #log: BackendLog;

  constructor(options: BackendPolicyOptions) {
    this.#url = `${trimSlash(options.baseUrl)}/internal/models/policy`;
    this.#token = options.serviceToken;
    this.#providers = options.supportedProviders;
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_POLICY_TIMEOUT_MS;
    this.#fetch = options.fetch ?? fetch;
    this.#log = options.log ?? (() => {});
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
    const timeout = AbortSignal.timeout(this.#timeoutMs);
    const signal = AbortSignal.any([info.ctx.signal, timeout]);
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
      json = res.ok ? await res.json() : null;
    } catch (err) {
      const why = timeout.aborted ? "timed out" : errorName(err);
      this.#log("policy_error", { requestId, error: why });
      return unavailable(why);
    }
    if (!res.ok) {
      this.#log("policy_error", { requestId, status: res.status });
      return unavailable(`backend answered ${res.status}`);
    }
    return this.#parse(json, requestId);
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
      return {
        ok: true,
        provider: json.provider,
        model: json.model,
        apiKey: json.apiKey,
        maxTokens: json.maxTokens,
      };
    }
    this.#log("policy_error", { requestId, error: "malformed response" });
    return unavailable("malformed response");
  }
}

export interface UsageReporterOptions {
  baseUrl: string;
  serviceToken: string;
  /** Attempts per report (first try included). Default 4. */
  attempts?: number;
  /** First retry delay; doubles each time. Default 250 ms. */
  baseDelayMs?: number;
  timeoutMs?: number;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  log?: BackendLog;
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Posts each model response's usage to the backend ledger, in the background. */
export class UsageReporter {
  readonly #url: string;
  readonly #token: string;
  readonly #attempts: number;
  readonly #baseDelayMs: number;
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;
  readonly #sleep: (ms: number) => Promise<void>;
  readonly #log: BackendLog;
  readonly #pending = new Set<Promise<void>>();
  #closed = false;

  constructor(options: UsageReporterOptions) {
    this.#url = `${trimSlash(options.baseUrl)}/internal/models/usage`;
    this.#token = options.serviceToken;
    this.#attempts = Math.max(1, options.attempts ?? 4);
    this.#baseDelayMs = options.baseDelayMs ?? 250;
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_POLICY_TIMEOUT_MS;
    this.#fetch = options.fetch ?? fetch;
    this.#sleep = options.sleep ?? realSleep;
    this.#log = options.log ?? (() => {});
  }

  /** Hooks to register with the harness. */
  get hooks(): AgentHooks {
    return {
      afterResponse: (event) => {
        this.report(event);
      },
    };
  }

  /** Start reporting one response; returns at once. */
  report(event: AfterResponseEvent): void {
    if (this.#closed) return;
    const requestId = requestIdFor(event.runId, event.round, "generation");
    const body = JSON.stringify({
      tenantId: event.conversation.tenantId,
      projectId: event.conversation.projectId,
      ...backendConversationIds(event.conversation),
      runId: event.runId,
      requestId,
      provider: event.message.provider,
      model: event.message.model,
      usage: {
        inputTokens: event.usage.inputTokens,
        outputTokens: event.usage.outputTokens,
        cacheReadTokens: event.usage.cacheReadTokens,
        cacheWriteTokens: event.usage.cacheWriteTokens,
      },
      status: "success",
    });
    const job = this.#send(requestId, body).finally(() => this.#pending.delete(job));
    this.#pending.add(job);
  }

  /** Reports still in flight. */
  get pending(): number {
    return this.#pending.size;
  }

  /** Wait for in-flight reports to finish (each is already bounded by its retries). */
  async flush(): Promise<void> {
    while (this.#pending.size > 0) await Promise.allSettled([...this.#pending]);
  }

  /** Stop accepting reports and wait for the ones in flight. */
  async close(): Promise<void> {
    this.#closed = true;
    await this.flush();
  }

  async #send(requestId: string, body: string): Promise<void> {
    for (let attempt = 1; attempt <= this.#attempts; attempt++) {
      let status: number | null = null;
      let why = "";
      try {
        const res = await this.#fetch(this.#url, {
          method: "POST",
          headers: {
            authorization: `Bearer ${this.#token}`,
            "content-type": "application/json",
          },
          body,
          signal: AbortSignal.timeout(this.#timeoutMs),
        });
        await res.body?.cancel().catch(() => {});
        if (res.ok) return;
        status = res.status;
        why = `status ${res.status}`;
        // A client error other than 429 will not change on retry.
        if (res.status < 500 && res.status !== 429) {
          this.#log("usage_dropped", { requestId, status });
          return;
        }
      } catch (err) {
        why = errorName(err);
      }
      if (attempt === this.#attempts) {
        this.#log("usage_failed", { requestId, attempts: attempt, error: why });
        return;
      }
      this.#log("usage_retry", { requestId, attempt, error: why });
      await this.#sleep(this.#baseDelayMs * 2 ** (attempt - 1));
    }
  }
}
