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
 * | `ENGINE_APPROVAL_TTL_MS` | `86400000` | approval expiry; `0` for none |
 * | `ENGINE_MODEL` / `ENGINE_SUMMARY_MODEL` | Anthropic defaults | model ids |
 * | `ENGINE_MAX_TOKENS` | `8192` | reply token budget per request |
 * | `ENGINE_PROGRESS_INTERVAL_MS` | `100` | progress commit throttle (KTD11) |
 * | `ENGINE_SUBAGENT_TOOL` | none | register a subagent tool under this name |
 *
 * `ANTHROPIC_API_KEY` is read per model request by the production entry.
 */
import pg from "pg";
import { DEFAULT_PROGRESS_INTERVAL_MS } from "../agent/progress.js";
import { DEFAULT_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_SUMMARY_MODEL } from "../models/anthropic.js";

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
  /** Null: approvals never expire. */
  approvalTtlMs: number | null;
  model: string;
  summaryModel: string;
  maxTokens: number;
  progressIntervalMs: number;
  subagentTool: string | null;
}

export const DEFAULT_LEASE_TTL_MS = 15_000;
export const DEFAULT_APPROVAL_TTL_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_MAX_TOKENS = 8192;

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
    gatewayUrl: text(env, "ENGINE_GATEWAY_URL"),
    gatewayToken: text(env, "ENGINE_GATEWAY_TOKEN"),
    approvalTtlMs: approval === 0 ? null : approval,
    model: text(env, "ENGINE_MODEL") ?? DEFAULT_ANTHROPIC_MODEL,
    summaryModel: text(env, "ENGINE_SUMMARY_MODEL") ?? DEFAULT_ANTHROPIC_SUMMARY_MODEL,
    maxTokens: integer(env, "ENGINE_MAX_TOKENS", DEFAULT_MAX_TOKENS, 1),
    progressIntervalMs: integer(
      env,
      "ENGINE_PROGRESS_INTERVAL_MS",
      DEFAULT_PROGRESS_INTERVAL_MS,
      0,
    ),
    subagentTool: text(env, "ENGINE_SUBAGENT_TOOL"),
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

/** A pool for the configured database and schema. */
export function createWorkerPool(config: Pick<WorkerConfig, "databaseUrl" | "dbSchema">): pg.Pool {
  const pool = new pg.Pool({
    connectionString: config.databaseUrl,
    ...(config.dbSchema ? { options: `-c search_path=${config.dbSchema}` } : {}),
    max: 10,
  });
  // A dropped idle connection is replaced on next use; it must not crash the worker.
  pool.on("error", () => {});
  return pool;
}
