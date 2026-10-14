/**
 * A worker process's wiring (KTD3, KTD4): one session, one lease, one HTTP
 * front. Shared by the production entry (`scripts/worker.ts`) and the
 * scenario test entry, which only swaps in its model, clock and extra routes.
 *
 * `startWorker` migrates, listens, then loops on the session lease:
 *
 * - **standby** while another worker holds the lease: health answers
 *   `owner: false`, every authenticated route `409 { error: "not_owner",
 *   ownerId }`, and the lease is retried every `leaseRetryMs`;
 * - **serving** once the lease is acquired: the gateway manifest is loaded
 *   with the lease fence, the harness opens over Postgres with the keeper's
 *   fence and signal, and requests go to {@link createEngineServer} with
 *   `isOwner` tied to the keeper;
 * - **lost** when the keeper self-fences (a missed heartbeat, or the gateway
 *   rejecting the fence token, which calls `fenceNow`): the runtime stops,
 *   the harness closes and {@link WorkerHandle.exited} resolves
 *   `lease_lost`; the process should exit and be restarted;
 * - **stopped** after {@link WorkerHandle.stop}: the front closes, the
 *   harness closes and the lease is released so a successor takes over at
 *   once.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import type { Pool } from "pg";
import { type AgentSettings, Harness } from "../agent/harness.js";
import type { HostPolicy } from "../agent/policy.js";
import { createSubagentTool } from "../agent/subagents.js";
import { type Clock, realClock } from "../core/clock.js";
import { NotOwner } from "../core/errors.js";
import type { ModelRegistry } from "../models/stream.js";
import { bearerMatches, headerValue } from "../server/auth.js";
import { createEngineServer, type EngineServer } from "../server/http.js";
import { matchRoute } from "../server/routes.js";
import { defaultOwnerId, LeaseKeeper, PostgresLeases } from "../storage/postgres/lease.js";
import { migrate } from "../storage/postgres/migrate.js";
import { PostgresStorageProvider } from "../storage/postgres/storage.js";
import type { StorageProvider } from "../storage/types.js";
import { createGatewayTools, type GatewayToolset } from "../tools/gateway.js";
import type { ToolDefinition } from "../tools/registry.js";

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
  /** Approval expiry for gateway tools that require approval; null for none. */
  approvalTtlMs?: number | null;
  /** Register a subagent tool under this name. */
  subagentTool?: string | null;
  // biome-ignore lint/suspicious/noExplicitAny: tools with any argument type
  tools?: ToolDefinition<any>[];
  /** Engine clock (approval expiry, deadlines, throttling). Leases use the database clock. */
  clock?: Clock;
  settings?: Partial<AgentSettings>;
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
 * The host policy a worker runs with: the model defaults decide every model
 * request, and a gateway tool that asks for approval requires one.
 */
export function workerHostPolicy(options: {
  modelDefaults: WorkerModelDefaults;
  gateway: Pick<GatewayToolset, "requiresApproval"> | null;
  approvalTtlMs: number | null;
  clock: Clock;
}): HostPolicy {
  const { modelDefaults: d, gateway, approvalTtlMs, clock } = options;
  return {
    resolveModelRequest(info) {
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

/** Start a worker; resolves once it listens (it may still be on standby). */
export async function startWorker(options: WorkerOptions): Promise<WorkerHandle> {
  const log = options.log ?? noLog;
  const clock = options.clock ?? realClock;
  const ownerId = options.ownerId ?? defaultOwnerId();
  const { sessionId, serviceToken, pool } = options;
  const retryMs = options.leaseRetryMs ?? Math.max(1, Math.floor(options.leaseTtlMs / 3));
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
      log("http_error", { error: errorText(err) });
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

  /** Close the front, engine and harness once; release the lease when stopping. */
  function shutdown(exit: WorkerExit): Promise<void> {
    if (teardown) return teardown;
    teardown = (async () => {
      wake?.();
      await new Promise<void>((resolve) => {
        front.close(() => resolve());
        front.closeAllConnections();
      });
      await engine?.close().catch(() => {});
      await harness?.close().catch(() => {});
      if (exit.reason === "stopped") await keeper?.release().catch(() => {});
      state = exit.reason === "lease_lost" ? "lost" : "stopped";
      log("exited", { reason: exit.reason });
      finish(exit);
    })();
    return teardown;
  }

  function onLost(error: unknown): void {
    if (state === "stopping" || state === "stopped" || state === "lost") return;
    state = "lost";
    log("lease_lost", { error: errorText(error) });
    void shutdown({ reason: "lease_lost", error });
  }

  async function serve(k: LeaseKeeper): Promise<void> {
    let gateway: GatewayToolset | null = null;
    if (options.gateway) {
      gateway = await createGatewayTools({
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
    const tools = [
      ...(gateway?.tools ?? []),
      ...(options.tools ?? []),
      ...(options.subagentTool ? [createSubagentTool({ name: options.subagentTool })] : []),
    ];
    const base = new PostgresStorageProvider(pool);
    const storage = options.wrapStorage ? options.wrapStorage(base) : base;
    const h = await Harness.open(storage, {
      sessionId,
      models: options.models,
      tools,
      policy: workerHostPolicy({
        modelDefaults: options.modelDefaults,
        gateway,
        approvalTtlMs: options.approvalTtlMs ?? null,
        clock,
      }),
      clock,
      fence: k.fence,
      signal: k.signal,
      ...(options.settings ? { settings: options.settings } : {}),
      onError: (err, info) => log("engine_error", { ...info, error: errorText(err) }),
    });
    harness = h;
    engine = createEngineServer({
      harness: h,
      sessionId,
      ownerId,
      serviceToken,
      isOwner: () => k.state === "held",
      currentOwner,
      onError: (err) => log("http_error", { error: errorText(err) }),
    });
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
        if (!(err instanceof NotOwner)) log("lease_error", { error: errorText(err) });
        else log("standby", { ownerId: err.ownerId ?? null });
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, retryMs);
          wake = () => {
            clearTimeout(timer);
            resolve();
          };
        });
        wake = null;
        continue;
      }
      keeper = k;
      log("lease_acquired", { token: k.token, ownerId });
      if (state !== "standby") {
        // Stopped (or fenced) while acquiring.
        if (state === "stopping") await k.release().catch(() => {});
        return;
      }
      await serve(k);
      if (state !== "standby") {
        // Stopped or fenced while opening: the teardown may have missed what serve opened.
        await teardown;
        await engine?.close().catch(() => {});
        await harness?.close().catch(() => {});
        if (state !== "lost") await k.release().catch(() => {});
        return;
      }
      state = "serving";
      log("serving", { token: k.token });
      markServing();
    }
  }

  run().catch((error: unknown) => {
    if (state === "stopping" || state === "stopped" || state === "lost") return;
    log("failed", { error: errorText(error) });
    state = "stopping";
    void keeper?.release().catch(() => {});
    void shutdown({ reason: "failed", error });
  });

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

function errorText(err: unknown): string {
  return err instanceof Error ? `${err.name}: ${err.message}` : String(err);
}
