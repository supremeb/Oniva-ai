/**
 * A worker process's wiring (KTD3, KTD4): one session, one lease, one HTTP
 * front. Shared by the production entry (`scripts/worker.ts`) and the
 * scenario test entry, which only swaps in its model, clock and extra routes.
 *
 * `startWorker` migrates, listens, then loops on the session lease:
 *
 * - **standby** while another worker holds the lease: health answers
 *   `owner: false`, every authenticated route `409 { error: "not_owner",
 *   ownerId }`, and the lease is retried every `leaseRetryMs`. A worker that
 *   acquires the lease but cannot load the gateway manifest gives the lease
 *   back and stays on standby, retrying, instead of exiting;
 * - **serving** once the lease is acquired: the gateway manifest is loaded
 *   with the lease fence, the harness opens over Postgres with the keeper's
 *   fence and signal, and requests go to {@link createEngineServer} with
 *   `isOwner` tied to the keeper;
 * - **lost** when the keeper self-fences (a missed heartbeat, or the gateway
 *   rejecting the fence token, which calls `fenceNow`): the runtime stops,
 *   the harness closes and {@link WorkerHandle.exited} resolves
 *   `lease_lost`; the process should exit and be restarted;
 * - **failed** when the engine stops on its own while serving (its session
 *   failed on a storage error): the lease is released so a successor takes
 *   over at once, and {@link WorkerHandle.exited} resolves `failed`; the
 *   process should exit non-zero and be restarted;
 * - **stopped** after {@link WorkerHandle.stop}: the front closes, the
 *   harness closes and the lease is released so a successor takes over at
 *   once.
 *
 * Every shutdown bounds closing the engine (and releasing the lease) by
 * `closeTimeoutMs`, so a wedged handler or connection cannot hold the process.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import type { Pool } from "pg";
import { askUserTool } from "../agent/ask-user-tool.js";
import { type AgentSettings, Harness } from "../agent/harness.js";
import type { AgentHooks } from "../agent/hooks.js";
import type { HostPolicy } from "../agent/policy.js";
import { createSubagentTool } from "../agent/subagents.js";
import { type Clock, realClock } from "../core/clock.js";
import { errorText, NotOwner } from "../core/errors.js";
import type { ModelRegistry } from "../models/stream.js";
import { bearerMatches, headerValue } from "../server/auth.js";
import { createEngineServer, type EngineServer } from "../server/http.js";
import { matchRoute } from "../server/routes.js";
import type { Redact } from "../session/transaction.js";
import { defaultOwnerId, LeaseKeeper, PostgresLeases } from "../storage/postgres/lease.js";
import { migrate } from "../storage/postgres/migrate.js";
import { PostgresStorageProvider } from "../storage/postgres/storage.js";
import type { StorageProvider } from "../storage/types.js";
import { createGatewayTools, type GatewayToolset } from "../tools/gateway.js";
import type { ToolDefinition } from "../tools/registry.js";
import { BackendPolicyClient, UsageReporter } from "./backend-policy.js";

export type WorkerLog = (event: string, fields?: Record<string, unknown>) => void;

export interface WorkerModelDefaults {
  /** Registered provider name the policy routes to. */
  provider: string;
  /** Model for a conversation without a model hint. */
  model: string;
  /** Model for compaction summaries. */
  summaryModel: string;
  maxTokens: number;
  /** The API key, read per request; undefined denies the request. */
  apiKey: () => string | undefined;
}

/** Backend-managed model policy and usage ledger (U9); replaces `modelDefaults` for requests. */
export interface WorkerBackendPolicyOptions {
  baseUrl: string;
  serviceToken: string;
  timeoutMs?: number;
  /** Total time to keep retrying a transient policy or usage failure; default 60 s, `0` for none. */
  retryMs?: number;
  fetch?: typeof fetch;
}

export interface WorkerGatewayOptions {
  baseUrl: string;
  serviceToken: string;
  fetch?: typeof fetch;
}

export interface WorkerOptions {
  /** Pool on the engine schema; the caller owns (and ends) it. */
  pool: Pool;
  sessionId: string;
  serviceToken: string;
  port?: number;
  host?: string;
  ownerId?: string;
  leaseTtlMs: number;
  leaseSafetyMarginMs?: number;
  /** Standby retry interval. Default `leaseTtlMs / 3` (one heartbeat). */
  leaseRetryMs?: number;
  /** Run migrations first. Default true. */
  migrate?: boolean;
  models: ModelRegistry;
  modelDefaults: WorkerModelDefaults;
  gateway?: WorkerGatewayOptions | null;
  /**
   * When set, every model request is resolved by the backend and every
   * response's usage is reported to it; `modelDefaults.apiKey` is never read.
   * Unset: the local `modelDefaults` decide.
   */
  backendPolicy?: WorkerBackendPolicyOptions | null;
  /** Approval expiry for gateway tools that require approval; null for none. */
  approvalTtlMs?: number | null;
  /** `ask_user` question expiry; null or `0` for none. Default 7 days. */
  questionTtlMs?: number | null;
  /** Register a subagent tool under this name. */
  subagentTool?: string | null;
  /** Register a subagent tool under each of these names (with `subagentTool`, deduplicated). */
  subagentTools?: readonly string[];
  // biome-ignore lint/suspicious/noExplicitAny: tools with any argument type
  tools?: ToolDefinition<any>[];
  /** Engine clock (approval expiry, deadlines, throttling). Leases use the database clock. */
  clock?: Clock;
  settings?: Partial<AgentSettings>;
  /** Host redaction (R20) for every stored payload. */
  redact?: Redact;
  hooks?: AgentHooks | readonly AgentHooks[];
  /** Bound on closing the front, engine and harness, and on releasing the lease. Default 10s. */
  closeTimeoutMs?: number;
  /** Wrap the storage provider (instrumentation). */
  wrapStorage?: (provider: StorageProvider) => StorageProvider;
  /** Sees every request first; return true when it answered it. */
  intercept?: (req: IncomingMessage, res: ServerResponse) => boolean | Promise<boolean>;
  log?: WorkerLog;
}

export type WorkerState = "standby" | "serving" | "lost" | "stopping" | "stopped";

export type WorkerExit =
  | { reason: "stopped" }
  | { reason: "lease_lost"; error: unknown }
  | { reason: "failed"; error: unknown };

export interface WorkerHandle {
  readonly url: string;
  readonly port: number;
  readonly ownerId: string;
  readonly state: WorkerState;
  /** The open harness while serving. */
  readonly harness: Harness | null;
  /** Resolves once the worker serves (never rejects; may stay pending). */
  readonly serving: Promise<void>;
  /** Resolves when the worker has ended, saying why. */
  readonly exited: Promise<WorkerExit>;
  /** Graceful shutdown: close the front and harness, release the lease. Idempotent. */
  stop(): Promise<void>;
}

/**
 * The host policy a worker runs with. Without a backend the model defaults
 * decide every model request and a gateway tool that asks for approval
 * requires one (local development, `approvalTtlMs` applies). With a backend,
 * model requests and every tool call that is not a manifest read or an
 * internal native tool are decided by Python.
 */
export function workerHostPolicy(options: {
  modelDefaults: WorkerModelDefaults;
  gateway:
    | (Pick<GatewayToolset, "requiresApproval"> & Partial<Pick<GatewayToolset, "describe">>)
    | null;
  approvalTtlMs: number | null;
  clock: Clock;
  /** Resolves model requests instead of `modelDefaults`; no local fallback. */
  backend?: Pick<BackendPolicyClient, "resolveModelRequest" | "authorizeTool"> | null;
}): HostPolicy {
  const { modelDefaults: d, gateway, approvalTtlMs, clock, backend } = options;
  return {
    resolveModelRequest(info) {
      if (backend) return backend.resolveModelRequest(info);
      const apiKey = d.apiKey();
      if (!apiKey) return { ok: false, reason: "no model API key is configured", code: "no_key" };
      return {
        ok: true,
        provider: d.provider,
        model: info.purpose === "compaction" ? d.summaryModel : (info.agent.model ?? d.model),
        apiKey,
        maxTokens: d.maxTokens,
      };
    },
    authorizeTool(request) {
      // With a backend the worker holds no tool policy: Python decides, the
      // manifest only spares the round trip for reads. `approvalTtlMs` is the
      // local-mode value; the backend's TTL applies otherwise.
      if (backend) return backend.authorizeTool(request, gateway);
      if (gateway?.requiresApproval(request.toolName)) {
        return {
          decision: "requireApproval",
          reason: `${request.toolName} requires approval`,
          ...(approvalTtlMs === null ? {} : { expiresAt: clock.now() + approvalTtlMs }),
        };
      }
      return { decision: "allow" };
    },
  };
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  if (res.headersSent) return;
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": String(Buffer.byteLength(text)),
    "cache-control": "no-store",
  });
  res.end(text);
}

const noLog: WorkerLog = () => {};

export const DEFAULT_CLOSE_TIMEOUT_MS = 10_000;

const TIMED_OUT = Symbol("timed out");

/** Settle `work` or give up after `ms` (real time); never rejects. */
async function within(
  work: Promise<unknown>,
  ms: number,
): Promise<{ ok: true } | { ok: false; error: unknown } | typeof TIMED_OUT> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<typeof TIMED_OUT>((resolve) => {
    timer = setTimeout(() => resolve(TIMED_OUT), Math.max(0, ms));
    timer.unref?.();
  });
  try {
    return await Promise.race([
      work.then(
        () => ({ ok: true }) as const,
        (error: unknown) => ({ ok: false, error }) as const,
      ),
      deadline,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

/** Start a worker; resolves once it listens (it may still be on standby). */
export async function startWorker(options: WorkerOptions): Promise<WorkerHandle> {
  const log = options.log ?? noLog;
  const clock = options.clock ?? realClock;
  const ownerId = options.ownerId ?? defaultOwnerId();
  const { sessionId, serviceToken, pool } = options;
  const retryMs = options.leaseRetryMs ?? Math.max(1, Math.floor(options.leaseTtlMs / 3));
  const closeTimeoutMs = options.closeTimeoutMs ?? DEFAULT_CLOSE_TIMEOUT_MS;
  if (options.migrate !== false) await migrate(pool);
  const leases = new PostgresLeases(pool);

  let state: WorkerState = "standby";
  let keeper: LeaseKeeper | null = null;
  let harness: Harness | null = null;
  let engine: EngineServer | null = null;
  let markServing!: () => void;
  const serving = new Promise<void>((resolve) => {
    markServing = resolve;
  });
  let finish!: (exit: WorkerExit) => void;
  const exited = new Promise<WorkerExit>((resolve) => {
    finish = resolve;
  });
  let wake: (() => void) | null = null;
  let teardown: Promise<void> | null = null;
  const backendPolicy = options.backendPolicy
    ? new BackendPolicyClient({
        ...options.backendPolicy,
        supportedProviders: () => options.models.names(),
        clock,
        log,
      })
    : null;
  const usageReporter = options.backendPolicy
    ? new UsageReporter({ ...options.backendPolicy, log })
    : null;

  const currentOwner = async (): Promise<string | null> => {
    const lease = await leases.current(sessionId);
    return lease?.live ? lease.ownerId : null;
  };

  async function notServing(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://worker.invalid");
    const match = matchRoute(req.method ?? "GET", url.pathname);
    if (match && "route" in match && !match.route.auth) {
      sendJson(res, 200, { status: state, sessionId, ownerId, owner: false });
      return;
    }
    if (!bearerMatches(headerValue(req, "authorization") ?? undefined, serviceToken)) {
      sendJson(res, 401, { error: "unauthorized" });
      return;
    }
    if (match === null) {
      sendJson(res, 404, { error: "not_found", reason: "route" });
      return;
    }
    sendJson(res, 409, { error: "not_owner", ownerId: await currentOwner() });
  }

  const front: Server = createServer((req, res) => {
    void (async () => {
      if (options.intercept && (await options.intercept(req, res))) return;
      if (state === "serving" && engine) {
        engine.server.emit("request", req, res);
        return;
      }
      await notServing(req, res);
    })().catch((err: unknown) => {
      log("http_error", { error: logErrorText(err) });
      sendJson(res, 500, { error: "internal" });
    });
  });
  const address = await new Promise<AddressInfo>((resolve, reject) => {
    front.once("error", reject);
    front.listen(options.port ?? 0, options.host ?? "127.0.0.1", () => {
      front.off("error", reject);
      resolve(front.address() as AddressInfo);
    });
  });
  const host = address.family === "IPv6" ? `[${address.address}]` : address.address;
  const url = `http://${host}:${address.port}`;
  log("listening", { url, port: address.port, ownerId, sessionId });

  /** Run one shutdown step within `ms`, logging a failure or timeout. */
  async function step(what: string, work: Promise<unknown> | undefined, ms: number): Promise<void> {
    if (!work) return;
    const result = await within(work, ms);
    if (result === TIMED_OUT) log("close_timeout", { step: what, timeoutMs: closeTimeoutMs });
    else if (!result.ok) log("close_error", { step: what, error: logErrorText(result.error) });
  }

  /**
   * Close the front, engine and harness once (together bounded by
   * `closeTimeoutMs`); then, unless the lease was lost, release it (bounded
   * the same) so a successor takes over at once.
   */
  function shutdown(exit: WorkerExit): Promise<void> {
    if (teardown) return teardown;
    teardown = (async () => {
      wake?.();
      const deadline = Date.now() + closeTimeoutMs;
      const left = () => deadline - Date.now();
      await step(
        "front",
        new Promise<void>((resolve) => {
          front.close(() => resolve());
          front.closeAllConnections();
        }),
        left(),
      );
      await step("engine", engine?.close(), left());
      await step("harness", harness?.close(), left());
      await step("usage", usageReporter?.close(), left());
      if (exit.reason !== "lease_lost") await step("lease", keeper?.release(), closeTimeoutMs);
      state = exit.reason === "lease_lost" ? "lost" : "stopped";
      log("exited", { reason: exit.reason });
      finish(exit);
    })();
    return teardown;
  }

  function onLost(error: unknown): void {
    if (state === "stopping" || state === "stopped" || state === "lost") return;
    state = "lost";
    log("lease_lost", { error: logErrorText(error) });
    void shutdown({ reason: "lease_lost", error });
  }

  function onFailed(error: unknown): void {
    if (state === "stopping" || state === "stopped" || state === "lost") return;
    log("failed", { error: logErrorText(error) });
    state = "stopping";
    void shutdown({ reason: "failed", error });
  }

  /**
   * The runtime stopped while this worker still wanted to serve: after a lost
   * lease `onLost` has already run (the keeper calls it synchronously as it
   * aborts its signal); otherwise the session failed on its own (a storage
   * error) and the worker fails, releasing the lease.
   */
  function onEngineStopped(k: LeaseKeeper, reason: unknown): void {
    if (state !== "serving" && state !== "standby") return;
    if (k.state === "lost") onLost(reason);
    else onFailed(reason);
  }

  async function loadGateway(k: LeaseKeeper): Promise<GatewayToolset | null> {
    if (!options.gateway) return null;
    return createGatewayTools({
      baseUrl: options.gateway.baseUrl,
      serviceToken: options.gateway.serviceToken,
      fence: k.fence,
      sessionId,
      signal: k.signal,
      ...(options.gateway.fetch ? { fetch: options.gateway.fetch } : {}),
      onFenceRejected: (rejection) => {
        log("fence_rejected", { tool: rejection.tool, token: String(rejection.token) });
        k.fenceNow("the tool gateway rejected this worker's fence token");
      },
    });
  }

  async function serve(k: LeaseKeeper, gateway: GatewayToolset | null): Promise<void> {
    // `ask_user` is native: always registered, usable only by conversations
    // whose agent tools list it. A caller-supplied tool of that name wins.
    const supplied = [...(gateway?.tools ?? []), ...(options.tools ?? [])];
    const tools = [
      ...supplied,
      ...(supplied.some((t) => t.name === askUserTool.name) ? [] : [askUserTool]),
      ...[
        ...new Set([
          ...(options.subagentTool ? [options.subagentTool] : []),
          ...(options.subagentTools ?? []),
        ]),
      ].map((name) => createSubagentTool({ name })),
    ];
    const base = new PostgresStorageProvider(pool);
    const storage = options.wrapStorage ? options.wrapStorage(base) : base;
    const h = await Harness.open(storage, {
      sessionId,
      models: options.models,
      tools,
      ...(options.questionTtlMs === undefined ? {} : { questionTtlMs: options.questionTtlMs }),
      policy: workerHostPolicy({
        modelDefaults: options.modelDefaults,
        gateway,
        approvalTtlMs: options.approvalTtlMs ?? null,
        clock,
        backend: backendPolicy,
      }),
      clock,
      fence: k.fence,
      signal: k.signal,
      ...(options.settings ? { settings: options.settings } : {}),
      ...(options.redact ? { redact: options.redact } : {}),
      ...(options.hooks || usageReporter
        ? {
            hooks: [
              ...(options.hooks ? [options.hooks].flat() : []),
              ...(usageReporter ? [usageReporter.hooks] : []),
            ],
          }
        : {}),
      onError: (err, info) => log("engine_error", { ...info, error: logErrorText(err) }),
    });
    harness = h;
    void h.runtime.closed.then((reason) => onEngineStopped(k, reason));
    engine = createEngineServer({
      harness: h,
      sessionId,
      ownerId,
      serviceToken,
      isOwner: () => k.state === "held",
      currentOwner,
      onError: (err) => log("http_error", { error: logErrorText(err) }),
    });
  }

  /** Wait `retryMs` on standby; `shutdown` cuts it short. */
  async function standbyPause(): Promise<void> {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, retryMs);
      wake = () => {
        clearTimeout(timer);
        resolve();
      };
    });
    wake = null;
  }

  async function run(): Promise<void> {
    while (state === "standby") {
      let k: LeaseKeeper;
      try {
        k = await LeaseKeeper.acquire({
          store: leases,
          sessionId,
          ownerId,
          ttlMs: options.leaseTtlMs,
          ...(options.leaseSafetyMarginMs === undefined
            ? {}
            : { safetyMarginMs: options.leaseSafetyMarginMs }),
          onLost,
        });
      } catch (err) {
        if (!(err instanceof NotOwner)) log("lease_error", { error: logErrorText(err) });
        else log("standby", { ownerId: err.ownerId ?? null });
        await standbyPause();
        continue;
      }
      keeper = k;
      log("lease_acquired", { token: k.token, ownerId });
      if (state !== "standby") {
        // Stopped (or fenced) while acquiring.
        if (state === "stopping") await k.release().catch(() => {});
        return;
      }
      let gateway: GatewayToolset | null;
      try {
        gateway = await loadGateway(k);
      } catch (err) {
        if (state !== "standby") return; // stopped or fenced while loading
        // A gateway outage: give the lease back and retry from standby rather
        // than exiting into a crash loop.
        log("gateway_unavailable", { error: logErrorText(err), retryMs });
        keeper = null;
        await k.release().catch(() => {});
        if (state !== "standby") return;
        await standbyPause();
        continue;
      }
      await serve(k, gateway);
      if (state !== "standby") {
        // Stopped or fenced while opening: the teardown may have missed what serve opened.
        await teardown;
        await step("engine", engine?.close(), closeTimeoutMs);
        await step("harness", harness?.close(), closeTimeoutMs);
        if (state !== "lost") await step("lease", k.release(), closeTimeoutMs);
        return;
      }
      state = "serving";
      log("serving", { token: k.token });
      markServing();
    }
  }

  run().catch(onFailed);

  return {
    url,
    port: address.port,
    ownerId,
    get state() {
      return state;
    },
    get harness() {
      return harness;
    },
    serving,
    exited,
    async stop() {
      if (state !== "lost" && state !== "stopped") state = "stopping";
      await shutdown({ reason: "stopped" });
    },
  };
}

/** Log form of a thrown value: the error's name, then {@link errorText}. */
function logErrorText(err: unknown): string {
  return err instanceof Error ? `${err.name}: ${errorText(err)}` : String(err);
}
