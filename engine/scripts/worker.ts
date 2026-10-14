/**
 * Production worker entry point: one session per process (KTD4).
 *
 * Configured from the environment (see `src/worker/config.ts`); serves the
 * engine HTTP API once it holds the session lease and answers `409` with the
 * owner id while another worker does. Exit codes: 0 after SIGTERM/SIGINT,
 * 2 for bad configuration, 3 when the lease was lost (restart the process),
 * 1 for any other failure.
 */
import { AnthropicProvider } from "../src/models/anthropic.js";
import { ModelRegistry } from "../src/models/stream.js";
import {
  assertNotTestHarnessInProduction,
  createWorkerPool,
  WorkerConfigError,
  workerConfigFromEnv,
} from "../src/worker/config.js";
import { startWorker, type WorkerLog } from "../src/worker/worker.js";

const log: WorkerLog = (event, fields = {}) => {
  process.stdout.write(`${JSON.stringify({ t: Date.now(), event, ...fields })}\n`);
};

async function main(): Promise<number> {
  let config: ReturnType<typeof workerConfigFromEnv>;
  try {
    assertNotTestHarnessInProduction(process.env);
    config = workerConfigFromEnv(process.env);
  } catch (err) {
    if (err instanceof WorkerConfigError) {
      process.stderr.write(`engine worker: ${err.message}\n`);
      return 2;
    }
    throw err;
  }

  const pool = createWorkerPool(config);
  const models = new ModelRegistry().register(new AnthropicProvider());
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
    approvalTtlMs: config.approvalTtlMs,
    subagentTool: config.subagentTool,
    settings: { progressIntervalMs: config.progressIntervalMs },
    log,
  });

  const stop = () => {
    log("signal", { action: "stop" });
    void worker.stop();
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);

  const exit = await worker.exited;
  await pool.end().catch(() => {});
  if (exit.reason === "stopped") return 0;
  return exit.reason === "lease_lost" ? 3 : 1;
}

main().then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stderr.write(`engine worker failed: ${err instanceof Error ? err.stack : err}\n`);
    process.exit(1);
  },
);
