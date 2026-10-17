/**
 * Worker configuration from the environment (KTD4: one session per worker).
 *
 * | Variable | Default | Meaning |
 * | --- | --- | --- |
 * | `DATABASE_URL` | required | Postgres connection URL |
 * | `ENGINE_DB_SCHEMA` | connection default | schema (`search_path`) holding the engine tables |
 * | `ENGINE_SESSION_ID` | required | the one session this worker serves |
 * | `ENGINE_SERVICE_TOKEN` | required | bearer token the backend presents |
 * | `ENGINE_PORT` / `ENGINE_HOST` | `8080` / `0.0.0.0` | HTTP listen address |
 * | `ENGINE_OWNER_ID` | host:pid:uuid | lease owner id (unique per process) |
 * | `ENGINE_LEASE_TTL_MS` | `15000` | lease time-to-live; heartbeats every ttl/3 |
 * | `ENGINE_LEASE_RETRY_MS` | ttl/3 | how often a standby worker retries the lease |
 * | `ENGINE_GATEWAY_URL` / `ENGINE_GATEWAY_TOKEN` | none / service token | tool gateway |
 * | `ENGINE_POLICY` | `backend` when `ENGINE_POLICY_URL` is set, else `local` | `backend`: model, key and quota come from the backend per request; `local`: `ENGINE_MODEL` and `ANTHROPIC_API_KEY` |
 * | `ENGINE_POLICY_URL` | `ENGINE_GATEWAY_URL` under `ENGINE_POLICY=backend` | backend origin serving `/internal/models/{policy,usage}`; authenticated with the gateway token |
 * | `ENGINE_POLICY_TIMEOUT_MS` | `5000` | timeout of one policy or usage call |
 * | `ENGINE_POLICY_RETRY_MS` | `60000` | total time a transient policy or usage failure is retried; `0` for none |
 * | `ENGINE_APPROVAL_TTL_MS` | `86400000` | approval expiry; `0` for none |
 * | `ENGINE_MODEL` / `ENGINE_SUMMARY_MODEL` | Anthropic defaults | model ids |
 * | `ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS` | `120000` | abort an OpenRouter attempt with no data event by then (retried as `network`) |
 * | `ENGINE_MODEL_IDLE_TIMEOUT_MS` | `120000` | abort an OpenRouter attempt silent for this long between data events (retryable, partial kept) |
 * | `ENGINE_MAX_TOKENS` | `8192` | reply token budget per request |
 * | `ENGINE_PROGRESS_INTERVAL_MS` | `100` | progress commit throttle (KTD11) |
 * | `ENGINE_SUBAGENT_TOOL` | none | register a subagent tool under this name |
 * | `ENGINE_SUBAGENT_TOOLS` | none | comma-separated subagent tool names to register (with `ENGINE_SUBAGENT_TOOL`, deduplicated) |
 * | `ENGINE_SHUTDOWN_TIMEOUT_MS` | `10000` | bound on closing the engine at shutdown |
 * | `ENGINE_DB_CONNECT_TIMEOUT_MS` | min(5000, ttl/3) | new-connection timeout |
 * | `ENGINE_DB_STATEMENT_TIMEOUT_MS` | min(10000, 2·ttl/3) | server `statement_timeout` |
 * | `ENGINE_DB_IDLE_TX_TIMEOUT_MS` | statement timeout | `idle_in_transaction_session_timeout` |
 * | `ENGINE_DB_QUERY_TIMEOUT_MS` | min(statement + 1000, ttl - 1) | client-side query timeout |
 *
 * The database timeouts must be below the lease TTL: a query outliving the
 * lease is wasted (its commit would be fenced) and holds a pooled connection.
 *
 * `ANTHROPIC_API_KEY` is read per model request by the production entry.
 */
import pg from "pg";
import { DEFAULT_PROGRESS_INTERVAL_MS } from "../agent/progress.js";
import { DEFAULT_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_SUMMARY_MODEL } from "../models/anthropic.js";
import { DEFAULT_FIRST_BYTE_TIMEOUT_MS, DEFAULT_IDLE_TIMEOUT_MS } from "../models/retry.js";
import { DEFAULT_POLICY_TIMEOUT_MS, DEFAULT_RETRY_MS } from "./backend-policy.js";

export type WorkerEnv = Readonly<Record<string, string | undefined>>;

export interface WorkerConfig {
  databaseUrl: string;
  dbSchema: string | null;
  sessionId: string;
  serviceToken: string;
  port: number;
  host: string;
  ownerId: string | null;
  leaseTtlMs: number;
  leaseRetryMs: number | null;
  gatewayUrl: string | null;
  gatewayToken: string | null;
  /** `backend`: the backend resolves every model request and records usage. */
  policy: "local" | "backend";
  /** Backend origin for the policy and usage endpoints; set iff `policy` is `backend`. */
  policyUrl: string | null;
  policyTimeoutMs: number;
  policyRetryMs: number;
  /** Null: approvals never expire. */
  approvalTtlMs: number | null;
  model: string;
  summaryModel: string;
  /** Abort an OpenRouter attempt that has produced no data event within this long. */
  modelFirstByteTimeoutMs: number;
  /** Abort an OpenRouter attempt silent for this long between data events. */
  modelIdleTimeoutMs: number;
  maxTokens: number;
  progressIntervalMs: number;
  subagentTool: string | null;
  /** Every subagent tool to register: `ENGINE_SUBAGENT_TOOL` then `ENGINE_SUBAGENT_TOOLS`, deduplicated. */
  subagentTools: string[];
  /** Bound on closing the engine and releasing the lease at shutdown. */
  shutdownTimeoutMs: number;
  db: WorkerDbTimeouts;
}

/** Postgres timeouts for the worker pool, all below the lease TTL. */
export interface WorkerDbTimeouts {
  connectTimeoutMs: number;
  statementTimeoutMs: number;
  idleInTransactionTimeoutMs: number;
  queryTimeoutMs: number;
}

export const DEFAULT_LEASE_TTL_MS = 15_000;
export const DEFAULT_APPROVAL_TTL_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_MAX_TOKENS = 8192;
export const DEFAULT_SHUTDOWN_TIMEOUT_MS = 10_000;

/** Database timeout defaults for a lease TTL (each below it). */
export function defaultDbTimeouts(leaseTtlMs: number): WorkerDbTimeouts {
  const below = (ms: number) => Math.max(1, Math.min(Math.floor(ms), leaseTtlMs - 1));
  const statement = below(Math.min(10_000, (leaseTtlMs * 2) / 3));
  return {
    connectTimeoutMs: below(Math.min(5_000, leaseTtlMs / 3)),
    statementTimeoutMs: statement,
    idleInTransactionTimeoutMs: statement,
    queryTimeoutMs: below(statement + 1_000),
  };
}

/** A configuration problem; the entry points print it and exit non-zero. */
export class WorkerConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkerConfigError";
  }
}

function text(env: WorkerEnv, name: string): string | null {
  const v = env[name]?.trim();
  return v ? v : null;
}

const SUBAGENT_TOOL_NAME = /^[A-Za-z0-9_-]{1,64}$/;

function subagentTools(env: WorkerEnv): string[] {
  const names = [
    text(env, "ENGINE_SUBAGENT_TOOL"),
    ...(text(env, "ENGINE_SUBAGENT_TOOLS")?.split(",") ?? []),
  ]
    .map((n) => n?.trim() ?? "")
    .filter((n) => n !== "");
  for (const name of names) {
    if (!SUBAGENT_TOOL_NAME.test(name)) {
      throw new WorkerConfigError(
        `ENGINE_SUBAGENT_TOOL(S) must be tool names (letters, digits, _ and -), got ${JSON.stringify(name)}`,
      );
    }
  }
  return [...new Set(names)];
}

function required(env: WorkerEnv, name: string): string {
  const v = text(env, name);
  if (v === null) throw new WorkerConfigError(`${name} is required`);
  return v;
}

function integer(env: WorkerEnv, name: string, fallback: number, min: number): number {
  const v = text(env, name);
  if (v === null) return fallback;
  const n = Number(v);
  if (!Number.isSafeInteger(n) || n < min) {
    throw new WorkerConfigError(`${name} must be an integer >= ${min}, got ${JSON.stringify(v)}`);
  }
  return n;
}

/** Read and validate the worker configuration. Throws `WorkerConfigError`. */
export function workerConfigFromEnv(env: WorkerEnv = process.env): WorkerConfig {
  const schema = text(env, "ENGINE_DB_SCHEMA");
  if (schema !== null && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(schema)) {
    throw new WorkerConfigError("ENGINE_DB_SCHEMA must be a plain identifier");
  }
  const leaseTtlMs = integer(env, "ENGINE_LEASE_TTL_MS", DEFAULT_LEASE_TTL_MS, 1);
  const retry = text(env, "ENGINE_LEASE_RETRY_MS");
  const approval = integer(env, "ENGINE_APPROVAL_TTL_MS", DEFAULT_APPROVAL_TTL_MS, 0);
  const dbDefaults = defaultDbTimeouts(leaseTtlMs);
  const belowTtl = (name: string, fallback: number): number => {
    const v = integer(env, name, fallback, 1);
    if (text(env, name) !== null && v >= leaseTtlMs) {
      throw new WorkerConfigError(
        `${name} (${v}) must be below ENGINE_LEASE_TTL_MS (${leaseTtlMs})`,
      );
    }
    return v;
  };
  const statementTimeoutMs = belowTtl(
    "ENGINE_DB_STATEMENT_TIMEOUT_MS",
    dbDefaults.statementTimeoutMs,
  );
  const db: WorkerDbTimeouts = {
    connectTimeoutMs: belowTtl("ENGINE_DB_CONNECT_TIMEOUT_MS", dbDefaults.connectTimeoutMs),
    statementTimeoutMs,
    idleInTransactionTimeoutMs: belowTtl("ENGINE_DB_IDLE_TX_TIMEOUT_MS", statementTimeoutMs),
    queryTimeoutMs: belowTtl(
      "ENGINE_DB_QUERY_TIMEOUT_MS",
      Math.max(1, Math.min(statementTimeoutMs + 1_000, leaseTtlMs - 1)),
    ),
  };
  const policyMode = text(env, "ENGINE_POLICY");
  if (policyMode !== null && policyMode !== "local" && policyMode !== "backend") {
    throw new WorkerConfigError(
      `ENGINE_POLICY must be "local" or "backend", got ${JSON.stringify(policyMode)}`,
    );
  }
  const gatewayUrl = text(env, "ENGINE_GATEWAY_URL");
  const policyUrlEnv = text(env, "ENGINE_POLICY_URL");
  const policy = policyMode ?? (policyUrlEnv === null ? "local" : "backend");
  const policyUrl = policy === "backend" ? (policyUrlEnv ?? gatewayUrl) : null;
  if (policy === "backend" && policyUrl === null) {
    throw new WorkerConfigError(
      "ENGINE_POLICY=backend requires ENGINE_POLICY_URL or ENGINE_GATEWAY_URL",
    );
  }
  if (policy === "backend" && text(env, "ENGINE_GATEWAY_TOKEN") === null) {
    throw new WorkerConfigError("ENGINE_POLICY=backend requires ENGINE_GATEWAY_TOKEN");
  }
  return {
    databaseUrl: required(env, "DATABASE_URL"),
    dbSchema: schema,
    sessionId: required(env, "ENGINE_SESSION_ID"),
    serviceToken: required(env, "ENGINE_SERVICE_TOKEN"),
    port: integer(env, "ENGINE_PORT", 8080, 0),
    host: text(env, "ENGINE_HOST") ?? "0.0.0.0",
    ownerId: text(env, "ENGINE_OWNER_ID"),
    leaseTtlMs,
    leaseRetryMs: retry === null ? null : integer(env, "ENGINE_LEASE_RETRY_MS", 0, 1),
    gatewayUrl,
    gatewayToken: text(env, "ENGINE_GATEWAY_TOKEN"),
    policy,
    policyUrl,
    policyTimeoutMs: integer(env, "ENGINE_POLICY_TIMEOUT_MS", DEFAULT_POLICY_TIMEOUT_MS, 1),
    policyRetryMs: integer(env, "ENGINE_POLICY_RETRY_MS", DEFAULT_RETRY_MS, 0),
    approvalTtlMs: approval === 0 ? null : approval,
    model: text(env, "ENGINE_MODEL") ?? DEFAULT_ANTHROPIC_MODEL,
    summaryModel: text(env, "ENGINE_SUMMARY_MODEL") ?? DEFAULT_ANTHROPIC_SUMMARY_MODEL,
    modelFirstByteTimeoutMs: integer(
      env,
      "ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS",
      DEFAULT_FIRST_BYTE_TIMEOUT_MS,
      1,
    ),
    modelIdleTimeoutMs: integer(env, "ENGINE_MODEL_IDLE_TIMEOUT_MS", DEFAULT_IDLE_TIMEOUT_MS, 1),
    maxTokens: integer(env, "ENGINE_MAX_TOKENS", DEFAULT_MAX_TOKENS, 1),
    progressIntervalMs: integer(
      env,
      "ENGINE_PROGRESS_INTERVAL_MS",
      DEFAULT_PROGRESS_INTERVAL_MS,
      0,
    ),
    subagentTool: text(env, "ENGINE_SUBAGENT_TOOL"),
    subagentTools: subagentTools(env),
    shutdownTimeoutMs: integer(env, "ENGINE_SHUTDOWN_TIMEOUT_MS", DEFAULT_SHUTDOWN_TIMEOUT_MS, 1),
    db,
  };
}

/**
 * Refuse test-harness configuration in production: `ENGINE_TEST_HARNESS` set
 * while `NODE_ENV=production` is a deployment mistake, never a valid setup.
 */
export function assertNotTestHarnessInProduction(env: WorkerEnv = process.env): void {
  if (text(env, "ENGINE_TEST_HARNESS") !== null && env.NODE_ENV === "production") {
    throw new WorkerConfigError(
      "ENGINE_TEST_HARNESS is set while NODE_ENV=production; refusing to start",
    );
  }
}

/** Server-side session settings, passed as libpq `options` (`-c name=value`). */
export function workerPoolOptions(
  config: Pick<WorkerConfig, "dbSchema"> & { db?: WorkerDbTimeouts | undefined },
): string | undefined {
  const settings: string[] = [];
  if (config.dbSchema) settings.push(`-c search_path=${config.dbSchema}`);
  if (config.db) {
    settings.push(`-c statement_timeout=${config.db.statementTimeoutMs}`);
    settings.push(`-c idle_in_transaction_session_timeout=${config.db.idleInTransactionTimeoutMs}`);
  }
  return settings.length ? settings.join(" ") : undefined;
}

/**
 * A pool for the configured database and schema, with connect, query and
 * server-side statement / idle-in-transaction timeouts and TCP keepalive, so a
 * hung or half-open connection is given up instead of holding a pooled client
 * past the lease. Pool errors (an idle client dropped) go to `log`; the pool
 * replaces the client on next use.
 */
export function createWorkerPool(
  config: Pick<WorkerConfig, "databaseUrl" | "dbSchema"> & { db?: WorkerDbTimeouts | undefined },
  log?: (event: string, fields?: Record<string, unknown>) => void,
): pg.Pool {
  const options = workerPoolOptions(config);
  const pool = new pg.Pool({
    connectionString: config.databaseUrl,
    ...(options ? { options } : {}),
    max: 10,
    keepAlive: true,
    ...(config.db
      ? {
          connectionTimeoutMillis: config.db.connectTimeoutMs,
          query_timeout: config.db.queryTimeoutMs,
        }
      : {}),
  });
  // A dropped idle connection is replaced on next use; it must not crash the worker.
  pool.on("error", (err) => {
    log?.("pool_error", {
      error: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
    });
  });
  return pool;
}
