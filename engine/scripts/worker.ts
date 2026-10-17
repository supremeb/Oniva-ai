/**
 * Production worker entry point: one session per process (KTD4).
 *
 * Configured from the environment (see `src/worker/config.ts`); serves the
 * engine HTTP API once it holds the session lease and answers `409` with the
 * owner id while another worker does. Exit codes: 0 after SIGTERM/SIGINT,
 * 2 for bad configuration, 3 when the lease was lost (restart the process),
 * 1 for any other failure (including the engine failing while serving, and a
 * shutdown that overran its deadline and was forced).
 *
 * Every stored payload passes through the default secret-masking redactor
 * (`src/worker/redact.ts`).
 */
import {
  assertNotTestHarnessInProduction,
  createWorkerPool,
  requireWorkflowBackend,
  WorkerConfigError,
  workerConfigFromEnv,
} from "../src/worker/config.js";
import { defaultModelRegistry } from "../src/worker/providers.js";
import { maskSecrets } from "../src/worker/redact.js";
import { startWorker, type WorkerLog } from "../src/worker/worker.js";

const log: WorkerLog = (event, fields = {}) => {
  process.stdout.write(`${JSON.stringify({ t: Date.now(), event, ...fields })}\n`);
};

async function main(): Promise<number> {
  let config: ReturnType<typeof workerConfigFromEnv>;
  let workflow: ReturnType<typeof requireWorkflowBackend>;
  try {
    assertNotTestHarnessInProduction(process.env);
    config = workerConfigFromEnv(process.env);
    workflow = requireWorkflowBackend(config);
  } catch (err) {
    if (err instanceof WorkerConfigError) {
      process.stderr.write(`engine worker: ${err.message}\n`);
      return 2;
    }
    throw err;
  }

  const pool = createWorkerPool(config, log);
  const models = defaultModelRegistry(process.env, log, {
    firstByteTimeoutMs: config.modelFirstByteTimeoutMs,
    idleTimeoutMs: config.modelIdleTimeoutMs,
  });
  const worker = await startWorker({
    pool,
    sessionId: config.sessionId,
    serviceToken: config.serviceToken,
    port: config.port,
    host: config.host,
    ...(config.ownerId ? { ownerId: config.ownerId } : {}),
    leaseTtlMs: config.leaseTtlMs,
    ...(config.leaseRetryMs === null ? {} : { leaseRetryMs: config.leaseRetryMs }),
    models,
    modelDefaults: {
      provider: "anthropic",
      model: config.model,
      summaryModel: config.summaryModel,
      maxTokens: config.maxTokens,
      apiKey: () => process.env.ANTHROPIC_API_KEY,
    },
    gateway: config.gatewayUrl
      ? { baseUrl: config.gatewayUrl, serviceToken: config.gatewayToken ?? config.serviceToken }
      : null,
    backendPolicy:
      config.policy === "backend" && config.policyUrl
        ? {
            baseUrl: config.policyUrl,
            serviceToken: config.gatewayToken ?? config.serviceToken,
            timeoutMs: config.policyTimeoutMs,
            retryMs: config.policyRetryMs,
          }
        : null,
    workflow,
    approvalTtlMs: config.approvalTtlMs,
    subagentTool: config.subagentTool,
    subagentTools: config.subagentTools,
    settings: { progressIntervalMs: config.progressIntervalMs },
    redact: maskSecrets,
    closeTimeoutMs: config.shutdownTimeoutMs,
    log,
  });

  // Fallback: if shutdown wedges past its own bounds, exit anyway. Unref'd so
  // it never keeps an otherwise finished process alive.
  const forceExitAfter = (ms: number, code: number) => {
    setTimeout(() => {
      log("force_exit", { code, afterMs: ms });
      process.exit(code);
    }, ms).unref();
  };
  // Closing the engine and releasing the lease are each bounded by the timeout.
  const shutdownBudgetMs = config.shutdownTimeoutMs * 2 + 5_000;

  const stop = () => {
    log("signal", { action: "stop" });
    forceExitAfter(shutdownBudgetMs, 1);
    void worker.stop();
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);

  const exit = await worker.exited;
  const code = exit.reason === "stopped" ? 0 : exit.reason === "lease_lost" ? 3 : 1;
  // `pool.end()` waits for checked-out clients; do not let a stuck one hold the exit.
  forceExitAfter(config.shutdownTimeoutMs, code);
  await pool.end().catch((err: unknown) => {
    log("pool_end_error", { error: err instanceof Error ? err.message : String(err) });
  });
  return code;
}

main().then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stderr.write(`engine worker failed: ${err instanceof Error ? err.stack : err}\n`);
    process.exit(1);
  },
);
