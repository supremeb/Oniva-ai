import { describe, expect, it } from "vitest";
import { ManualClock } from "../../src/core/clock.js";
import {
  assertNotTestHarnessInProduction,
  DEFAULT_APPROVAL_TTL_MS,
  DEFAULT_LEASE_TTL_MS,
  WorkerConfigError,
  workerConfigFromEnv,
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
      approvalTtlMs: DEFAULT_APPROVAL_TTL_MS,
      progressIntervalMs: 100,
      dbSchema: null,
      subagentTool: null,
    });
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
  });

  it("refuses the test harness flag in production only", () => {
    expect(() =>
      assertNotTestHarnessInProduction({ NODE_ENV: "production", ENGINE_TEST_HARNESS: "1" }),
    ).toThrow(WorkerConfigError);
    expect(() => assertNotTestHarnessInProduction({ NODE_ENV: "production" })).not.toThrow();
    expect(() => assertNotTestHarnessInProduction({ ENGINE_TEST_HARNESS: "1" })).not.toThrow();
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
