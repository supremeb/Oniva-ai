/**
 * Run-finished push (phase 6 U5). When a submission settles on a conversation
 * whose agent `metadata.notify` is `"backend"` (set by the backend for Fleet
 * and workflow runs), the notifier parks a `run_finished` row in the durable
 * outbox, posts it to `POST /internal/runs/finished` with the usage retry
 * policy, and deletes it on a 2xx. A backend outage or a restart leaves the row
 * for the flusher; the backend's reconciler is the second net for a row that
 * was never parked (a crash between settle and enqueue). Chat and Deep Work
 * conversations carry no tag and are untouched.
 */
import type { Harness, SettledEvent } from "../agent/harness.js";
import type { JsonValue } from "../core/json.js";
import type { UsageOutbox, UsageOutboxRecord } from "../storage/types.js";
import {
  type BackendLog,
  Backoff,
  DEFAULT_POLICY_TIMEOUT_MS,
  errorName,
  isTransientStatus,
  type RetryOptions,
  retryAfterMs,
  trimSlash,
} from "./backend-policy.js";

export interface RunNotifierOptions extends RetryOptions {
  baseUrl: string;
  serviceToken: string;
  /** The durable outbox. Without one a row lives only as long as its first delivery attempt. */
  outbox?: UsageOutbox | null;
  timeoutMs?: number;
  /** How often the flusher re-drives undelivered rows. Default 30 s. */
  flushIntervalMs?: number;
  /** A claim older than this may be taken again. Default 60 s. */
  claimTimeoutMs?: number;
  fetch?: typeof fetch;
  log?: BackendLog;
}

const NOTIFY_TAG = "backend";

/** Statuses that a retry can fix by an operator (a wrong or rotated token): keep the row. */
const isAuthStatus = (status: number): boolean => status === 401 || status === 403;

export class RunNotifier {
  readonly #url: string;
  readonly #token: string;
  readonly #outbox: UsageOutbox | null;
  readonly #backoff: Backoff;
  readonly #timeoutMs: number;
  readonly #fetch: typeof fetch;
  readonly #log: BackendLog;
  readonly #flushIntervalMs: number;
  readonly #claimTimeoutMs: number;
  readonly #pending = new Set<Promise<void>>();
  #harness: Harness | null = null;
  readonly #ready: Promise<Harness>;
  #resolveReady!: (h: Harness) => void;
  #flushTimer: ReturnType<typeof setInterval> | null = null;
  #closed = false;

  constructor(options: RunNotifierOptions) {
    this.#url = `${trimSlash(options.baseUrl)}/internal/runs/finished`;
    this.#token = options.serviceToken;
    this.#outbox = options.outbox ?? null;
    this.#backoff = new Backoff(options);
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_POLICY_TIMEOUT_MS;
    this.#fetch = options.fetch ?? fetch;
    this.#log = options.log ?? (() => {});
    this.#flushIntervalMs = Math.max(1, options.flushIntervalMs ?? 30_000);
    this.#claimTimeoutMs = Math.max(0, options.claimTimeoutMs ?? 60_000);
    this.#ready = new Promise<Harness>((resolve) => {
      this.#resolveReady = resolve;
    });
  }

  /**
   * The harness `onSettled` listener. Events that fire before {@link attach}
   * (orphans settled while the harness opens) wait for it.
   */
  readonly listener = (event: SettledEvent): void => {
    if (this.#closed) return;
    const job = this.#handle(event)
      .catch((err: unknown) => this.#log("run_finished_error", { error: errorName(err) }))
      .finally(() => this.#pending.delete(job));
    this.#pending.add(job);
  };

  /** Give the notifier the open harness so it can read conversation config and usage. */
  attach(harness: Harness): void {
    this.#harness = harness;
    this.#resolveReady(harness);
  }

  /** Start re-driving the outbox: once now, then every interval. Unref'd timer. */
  startFlushing(): void {
    if (!this.#outbox || this.#flushTimer) return;
    this.#flushSafely();
    this.#flushTimer = setInterval(() => this.#flushSafely(), this.#flushIntervalMs);
    this.#flushTimer.unref?.();
  }

  /** Claim and deliver every due `run_finished` row once. */
  async flushOutbox(): Promise<void> {
    const outbox = this.#outbox;
    if (!outbox) return;
    for (const record of await outbox.claimPending(this.#claimTimeoutMs, "run_finished")) {
      const job = this.#deliver(record, false).finally(() => this.#pending.delete(job));
      this.#pending.add(job);
    }
    await Promise.allSettled([...this.#pending]);
  }

  /** Wait for deliveries in flight. */
  async flush(): Promise<void> {
    while (this.#pending.size > 0) await Promise.allSettled([...this.#pending]);
  }

  async close(): Promise<void> {
    this.#closed = true;
    if (this.#flushTimer) {
      clearInterval(this.#flushTimer);
      this.#flushTimer = null;
    }
    await this.flush();
  }

  async #handle(event: SettledEvent): Promise<void> {
    const harness = this.#harness ?? (await this.#ready);
    const conv = harness.conversation(event.conversationId);
    const agent = await conv.agent();
    if (agent.metadata.notify !== NOTIFY_TAG) return;
    // A child conversation (a workflow LLM node, a subagent) inherits its parent's tag; only the run's own
    // conversation is a run the backend knows.
    if ((await conv.info()).ownerTaskId !== null) return;
    const usage = await conv.usage();
    const body: JsonValue = {
      kind: "run_finished",
      conversationId: event.conversationId,
      submissionId: event.submissionId,
      status: event.status,
      ...(event.reason === undefined ? {} : { reason: event.reason }),
      usage: usage as unknown as JsonValue,
    };
    const now = this.#backoff.now();
    const record: UsageOutboxRecord = {
      requestId: `run_finished:${event.conversationId}:${event.submissionId}`,
      kind: "run_finished",
      body,
      createdAt: now,
      claimedAt: now,
    };
    await this.#deliver(record, true);
  }

  async #deliver(record: UsageOutboxRecord, fresh: boolean): Promise<void> {
    if (fresh) await this.#outbox?.enqueue(record);
    if (await this.#send(record)) await this.#outbox?.ack(record.requestId);
  }

  /** Retry to the budget. True when the row may be deleted, false to keep it. */
  async #send(record: UsageOutboxRecord): Promise<boolean> {
    const payload = JSON.stringify(record.body);
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
        if (isAuthStatus(res.status)) {
          this.#log("run_finished_unauthorized", {
            requestId: record.requestId,
            status: res.status,
          });
          return false;
        }
        if (!isTransientStatus(res.status)) {
          this.#log("run_finished_dropped", { requestId: record.requestId, status: res.status });
          return true;
        }
        if (res.status === 429 || res.status === 503) {
          retryAfter = retryAfterMs(res.headers.get("retry-after"), this.#backoff.now());
        }
      } catch (err) {
        why = errorName(err);
      }
      const delay = this.#backoff.delay(attempt, deadline, retryAfter);
      if (delay === null) {
        this.#log("run_finished_failed", {
          requestId: record.requestId,
          attempts: attempt,
          error: why,
        });
        return false;
      }
      this.#log("run_finished_retry", {
        requestId: record.requestId,
        attempt,
        delayMs: delay,
        error: why,
      });
      await this.#backoff.sleep(delay);
    }
  }

  #flushSafely(): void {
    void this.flushOutbox().catch((err: unknown) => {
      this.#log("run_finished_outbox_error", { error: errorName(err) });
    });
  }
}
