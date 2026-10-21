import { describe, expect, it } from "vitest";
import { DEFAULT_QUESTION_TTL_MS } from "../../src/agent/questions.js";
import { ManualClock } from "../../src/core/clock.js";
import {
  assertNotTestHarnessInProduction,
  createWorkerPool,
  DEFAULT_APPROVAL_TTL_MS,
  DEFAULT_LEASE_TTL_MS,
  DEFAULT_SHUTDOWN_TIMEOUT_MS,
  defaultDbTimeouts,
  memoryBackendOptions,
  requireWorkflowBackend,
  WorkerConfigError,
  workerConfigFromEnv,
  workerPoolOptions,
} from "../../src/worker/config.js";
import { workerHostPolicy } from "../../src/worker/worker.js";

const BASE = {
  DATABASE_URL: "postgres://localhost/db",
  ENGINE_SESSION_ID: "s1",
  ENGINE_SERVICE_TOKEN: "tok",
};

describe("workerConfigFromEnv", () => {
  it("applies defaults", () => {
    const c = workerConfigFromEnv(BASE);
    expect(c).toMatchObject({
      sessionId: "s1",
      port: 8080,
      host: "0.0.0.0",
      leaseTtlMs: DEFAULT_LEASE_TTL_MS,
      leaseRetryMs: null,
      gatewayUrl: null,
      workflowBackendUrl: null,
      memoryBackendUrl: null,
      memoryCurate: false,
      approvalTtlMs: DEFAULT_APPROVAL_TTL_MS,
      questionTtlMs: DEFAULT_QUESTION_TTL_MS,
      progressIntervalMs: 100,
      dbSchema: null,
      subagentTool: null,
      subagentTools: [],
      shutdownTimeoutMs: DEFAULT_SHUTDOWN_TIMEOUT_MS,
      modelFirstByteTimeoutMs: 120_000,
      modelIdleTimeoutMs: 120_000,
      db: {
        connectTimeoutMs: 5_000,
        statementTimeoutMs: 10_000,
        idleInTransactionTimeoutMs: 10_000,
        queryTimeoutMs: 11_000,
      },
    });
  });

  it("collects subagent tools from ENGINE_SUBAGENT_TOOL and ENGINE_SUBAGENT_TOOLS, deduplicated", () => {
    const c = workerConfigFromEnv({
      ...BASE,
      ENGINE_SUBAGENT_TOOL: "delegate",
      ENGINE_SUBAGENT_TOOLS: "research_worker, reflector,delegate,",
    });
    expect(c.subagentTools).toEqual(["delegate", "research_worker", "reflector"]);
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_SUBAGENT_TOOLS: "bad name" })).toThrow(
      WorkerConfigError,
    );
  });

  it("registers steer_worker and complete_task only when asked (U21)", () => {
    expect(workerConfigFromEnv(BASE)).toMatchObject({ steerWorker: false, completeTask: false });
    expect(
      workerConfigFromEnv({ ...BASE, ENGINE_STEER_WORKER: "1", ENGINE_COMPLETE_TASK: "true" }),
    ).toMatchObject({ steerWorker: true, completeTask: true });
    expect(workerConfigFromEnv({ ...BASE, ENGINE_STEER_WORKER: "0" }).steerWorker).toBe(false);
  });

  it("registers memory_curate only with ENGINE_MEMORY_BACKEND_URL or ENGINE_MEMORY_CURATE (U30)", () => {
    expect(memoryBackendOptions(workerConfigFromEnv(BASE))).toBeNull();
    // A gateway URL alone does not register it: the default worker is unchanged.
    expect(
      memoryBackendOptions(workerConfigFromEnv({ ...BASE, ENGINE_GATEWAY_URL: "https://g" })),
    ).toBeNull();
    expect(
      memoryBackendOptions(
        workerConfigFromEnv({
          ...BASE,
          ENGINE_MEMORY_BACKEND_URL: "https://m",
          ENGINE_GATEWAY_TOKEN: "gt",
        }),
      ),
    ).toEqual({ baseUrl: "https://m", serviceToken: "gt", retryMs: 60_000 });
    expect(
      memoryBackendOptions(
        workerConfigFromEnv({
          ...BASE,
          ENGINE_MEMORY_CURATE: "1",
          ENGINE_GATEWAY_URL: "https://g",
        }),
      ),
    ).toMatchObject({ baseUrl: "https://g", serviceToken: "tok" });
    expect(() =>
      memoryBackendOptions(workerConfigFromEnv({ ...BASE, ENGINE_MEMORY_CURATE: "1" })),
    ).toThrow(WorkerConfigError);
  });

  it("derives database timeouts below a short lease TTL", () => {
    expect(defaultDbTimeouts(2_000)).toEqual({
      connectTimeoutMs: 666,
      statementTimeoutMs: 1_333,
      idleInTransactionTimeoutMs: 1_333,
      queryTimeoutMs: 1_999,
    });
    const c = workerConfigFromEnv({ ...BASE, ENGINE_LEASE_TTL_MS: "2000" });
    for (const v of Object.values(c.db)) expect(v).toBeLessThan(2_000);
    // A degenerate TTL still yields usable defaults.
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_LEASE_TTL_MS: "1" })).not.toThrow();
  });

  it("reads database and shutdown timeouts and keeps them below the lease TTL", () => {
    const c = workerConfigFromEnv({
      ...BASE,
      ENGINE_DB_CONNECT_TIMEOUT_MS: "1000",
      ENGINE_DB_STATEMENT_TIMEOUT_MS: "4000",
      ENGINE_DB_QUERY_TIMEOUT_MS: "5000",
      ENGINE_SHUTDOWN_TIMEOUT_MS: "2500",
    });
    expect(c.db).toEqual({
      connectTimeoutMs: 1_000,
      statementTimeoutMs: 4_000,
      idleInTransactionTimeoutMs: 4_000,
      queryTimeoutMs: 5_000,
    });
    expect(c.shutdownTimeoutMs).toBe(2_500);
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_DB_STATEMENT_TIMEOUT_MS: "15000" })).toThrow(
      /below ENGINE_LEASE_TTL_MS/,
    );
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_DB_IDLE_TX_TIMEOUT_MS: "0" })).toThrow(
      WorkerConfigError,
    );
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_SHUTDOWN_TIMEOUT_MS: "x" })).toThrow(
      WorkerConfigError,
    );
  });

  it("reads model request timeouts and rejects non-positive values", () => {
    const c = workerConfigFromEnv({
      ...BASE,
      ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS: "1000",
      ENGINE_MODEL_IDLE_TIMEOUT_MS: "2500",
    });
    expect(c.modelFirstByteTimeoutMs).toBe(1000);
    expect(c.modelIdleTimeoutMs).toBe(2500);
    for (const name of ["ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS", "ENGINE_MODEL_IDLE_TIMEOUT_MS"]) {
      expect(() => workerConfigFromEnv({ ...BASE, [name]: "0" })).toThrow(WorkerConfigError);
      expect(() => workerConfigFromEnv({ ...BASE, [name]: "-1" })).toThrow(WorkerConfigError);
      expect(() => workerConfigFromEnv({ ...BASE, [name]: "abc" })).toThrow(WorkerConfigError);
    }
  });

  it("requires the database, session and token", () => {
    for (const key of Object.keys(BASE)) {
      const env: Record<string, string> = { ...BASE };
      delete env[key];
      expect(() => workerConfigFromEnv(env)).toThrow(WorkerConfigError);
    }
  });

  it("validates numbers and the schema name", () => {
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_LEASE_TTL_MS: "0" })).toThrow(/>= 1/);
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_PORT: "abc" })).toThrow(WorkerConfigError);
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_DB_SCHEMA: "a;drop" })).toThrow(
      /identifier/,
    );
    expect(workerConfigFromEnv({ ...BASE, ENGINE_APPROVAL_TTL_MS: "0" }).approvalTtlMs).toBeNull();
    expect(workerConfigFromEnv({ ...BASE, ENGINE_QUESTION_TTL_MS: "0" }).questionTtlMs).toBeNull();
    expect(workerConfigFromEnv({ ...BASE, ENGINE_QUESTION_TTL_MS: "5000" }).questionTtlMs).toBe(
      5000,
    );
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_QUESTION_TTL_MS: "-1" })).toThrow(
      WorkerConfigError,
    );
  });

  it("refuses the test harness flag in production only", () => {
    expect(() =>
      assertNotTestHarnessInProduction({ NODE_ENV: "production", ENGINE_TEST_HARNESS: "1" }),
    ).toThrow(WorkerConfigError);
    expect(() => assertNotTestHarnessInProduction({ NODE_ENV: "production" })).not.toThrow();
    expect(() => assertNotTestHarnessInProduction({ ENGINE_TEST_HARNESS: "1" })).not.toThrow();
  });
});

describe("createWorkerPool", () => {
  const db = {
    connectTimeoutMs: 1_000,
    statementTimeoutMs: 4_000,
    idleInTransactionTimeoutMs: 3_000,
    queryTimeoutMs: 5_000,
  };

  it("merges the schema with the server-side timeouts", () => {
    expect(workerPoolOptions({ dbSchema: "eng", db })).toBe(
      "-c search_path=eng -c statement_timeout=4000 -c idle_in_transaction_session_timeout=3000",
    );
    expect(workerPoolOptions({ dbSchema: null, db })).toBe(
      "-c statement_timeout=4000 -c idle_in_transaction_session_timeout=3000",
    );
    expect(workerPoolOptions({ dbSchema: "eng" })).toBe("-c search_path=eng");
    expect(workerPoolOptions({ dbSchema: null })).toBeUndefined();
  });

  it("configures client timeouts and keepalive, and logs pool errors", async () => {
    const events: Array<[string, Record<string, unknown> | undefined]> = [];
    const pool = createWorkerPool(
      { databaseUrl: "postgres://localhost:1/db", dbSchema: "eng", db },
      (event, fields) => events.push([event, fields]),
    );
    // biome-ignore lint/suspicious/noExplicitAny: pg keeps the resolved config on `options`
    const opts = (pool as any).options;
    expect(opts).toMatchObject({
      connectionTimeoutMillis: 1_000,
      query_timeout: 5_000,
      keepAlive: true,
      options:
        "-c search_path=eng -c statement_timeout=4000 -c idle_in_transaction_session_timeout=3000",
    });
    pool.emit("error", new Error("terminating connection"), undefined);
    expect(events).toEqual([["pool_error", { error: "Error: terminating connection" }]]);
    await pool.end();
  });
});

describe("workerHostPolicy", () => {
  const defaults = {
    provider: "p",
    model: "m",
    summaryModel: "s",
    maxTokens: 10,
    apiKey: () => "k" as string | undefined,
  };

  it("requires approval for gateway tools that ask for it, with expiry", () => {
    const clock = new ManualClock(1000);
    const policy = workerHostPolicy({
      modelDefaults: defaults,
      gateway: { requiresApproval: (name) => name === "danger" },
      approvalTtlMs: 500,
      clock,
    });
    const req = {
      conversation: { id: "c", tenantId: "t", projectId: "p" },
      runId: "r",
      toolCallId: "x",
      toolTaskId: "task-x",
      toolSource: "gateway" as const,
      taint: { tainted: false, total: 0, sources: [] },
      arguments: {},
      replay: "unsafe" as const,
      // biome-ignore lint/suspicious/noExplicitAny: agent config is not consulted
      agent: {} as any,
    };
    expect(policy.authorizeTool({ ...req, toolName: "danger" })).toEqual({
      decision: "requireApproval",
      reason: "danger requires approval",
      expiresAt: 1500,
    });
    expect(policy.authorizeTool({ ...req, toolName: "safe" })).toEqual({ decision: "allow" });
  });

  it("local mode (no backend) makes no HTTP call and keeps the local rule for native tools", () => {
    const policy = workerHostPolicy({
      modelDefaults: defaults,
      gateway: { requiresApproval: () => false },
      approvalTtlMs: 500,
      clock: new ManualClock(),
    });
    // biome-ignore lint/suspicious/noExplicitAny: only the tool name is read locally
    const req = { toolName: "mystery", toolSource: "native" } as any;
    expect(policy.authorizeTool(req)).toEqual({ decision: "allow" });
  });

  it("routes models by purpose and denies without a key", () => {
    const policy = workerHostPolicy({
      modelDefaults: defaults,
      gateway: null,
      approvalTtlMs: null,
      clock: new ManualClock(),
    });
    // biome-ignore lint/suspicious/noExplicitAny: only purpose and agent.model are read
    const info = (purpose: string, model: string | null) => ({ purpose, agent: { model } }) as any;
    expect(policy.resolveModelRequest(info("generation", null))).toMatchObject({ model: "m" });
    expect(policy.resolveModelRequest(info("generation", "hint"))).toMatchObject({ model: "hint" });
    expect(policy.resolveModelRequest(info("compaction", "hint"))).toMatchObject({ model: "s" });
    const keyless = workerHostPolicy({
      modelDefaults: { ...defaults, apiKey: () => undefined },
      gateway: null,
      approvalTtlMs: null,
      clock: new ManualClock(),
    });
    expect(keyless.resolveModelRequest(info("generation", null))).toMatchObject({ ok: false });
  });
});

describe("backend policy configuration", () => {
  it("defaults to the local policy", () => {
    expect(workerConfigFromEnv(BASE)).toMatchObject({
      policy: "local",
      policyUrl: null,
      policyTimeoutMs: 5_000,
      policyRetryMs: 60_000,
    });
  });

  it("reads ENGINE_POLICY_RETRY_MS and rejects a negative one", () => {
    expect(workerConfigFromEnv({ ...BASE, ENGINE_POLICY_RETRY_MS: "0" }).policyRetryMs).toBe(0);
    expect(workerConfigFromEnv({ ...BASE, ENGINE_POLICY_RETRY_MS: "1500" }).policyRetryMs).toBe(
      1500,
    );
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_POLICY_RETRY_MS: "-1" })).toThrow(
      WorkerConfigError,
    );
  });

  it("selects the backend policy by ENGINE_POLICY_URL or ENGINE_POLICY=backend", () => {
    const token = { ...BASE, ENGINE_GATEWAY_TOKEN: "gw" };
    expect(workerConfigFromEnv({ ...token, ENGINE_POLICY_URL: "https://b" })).toMatchObject({
      policy: "backend",
      policyUrl: "https://b",
    });
    expect(
      workerConfigFromEnv({ ...token, ENGINE_POLICY: "backend", ENGINE_GATEWAY_URL: "https://g" }),
    ).toMatchObject({ policy: "backend", policyUrl: "https://g" });
    expect(
      workerConfigFromEnv({ ...token, ENGINE_POLICY: "local", ENGINE_POLICY_URL: "https://b" }),
    ).toMatchObject({ policy: "local", policyUrl: null });
  });

  it("rejects an unknown mode, a missing URL or a missing gateway token", () => {
    expect(() => workerConfigFromEnv({ ...BASE, ENGINE_POLICY: "x" })).toThrow(WorkerConfigError);
    expect(() =>
      workerConfigFromEnv({ ...BASE, ENGINE_POLICY: "backend", ENGINE_GATEWAY_TOKEN: "gw" }),
    ).toThrow(/ENGINE_POLICY_URL/);
    expect(() =>
      workerConfigFromEnv({ ...BASE, ENGINE_POLICY: "backend", ENGINE_POLICY_URL: "https://b" }),
    ).toThrow(/ENGINE_GATEWAY_TOKEN/);
  });
});

describe("workflow backend (U35)", () => {
  it("ENGINE_WORKFLOW_BACKEND_URL falls back to ENGINE_GATEWAY_URL and wins when set", () => {
    expect(workerConfigFromEnv(BASE).workflowBackendUrl).toBeNull();
    expect(
      workerConfigFromEnv({ ...BASE, ENGINE_GATEWAY_URL: "https://g" }).workflowBackendUrl,
    ).toBe("https://g");
    expect(
      workerConfigFromEnv({
        ...BASE,
        ENGINE_GATEWAY_URL: "https://g",
        ENGINE_WORKFLOW_BACKEND_URL: "https://w",
      }).workflowBackendUrl,
    ).toBe("https://w");
  });

  it("requireWorkflowBackend returns the origin and token, or fails fast with a clear message", () => {
    const withUrl = workerConfigFromEnv({
      ...BASE,
      ENGINE_GATEWAY_URL: "https://g",
      ENGINE_GATEWAY_TOKEN: "gw",
    });
    expect(requireWorkflowBackend(withUrl)).toEqual({
      baseUrl: "https://g",
      serviceToken: "gw",
      retryMs: 60_000,
    });
    expect(
      requireWorkflowBackend(
        workerConfigFromEnv({ ...BASE, ENGINE_WORKFLOW_BACKEND_URL: "https://w" }),
      ),
    ).toEqual({
      baseUrl: "https://w",
      serviceToken: "tok",
      retryMs: 60_000,
    });
    const without = workerConfigFromEnv(BASE);
    expect(() => requireWorkflowBackend(without)).toThrow(WorkerConfigError);
    expect(() => requireWorkflowBackend(without)).toThrow(
      /ENGINE_WORKFLOW_BACKEND_URL or ENGINE_GATEWAY_URL/,
    );
  });
});
