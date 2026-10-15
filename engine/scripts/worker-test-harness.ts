/**
 * Scenario-test worker entry point. NEVER deploy this.
 *
 * Runs the same wiring as `scripts/worker.ts` (`src/worker/worker.ts`) with
 * test doubles swapped in:
 * - the model is a rule-scripted fake (`ENGINE_TEST_MODEL_SCRIPT`, a JSON file
 *   re-read per request; see `test/support/scripted-model.ts`);
 * - the engine clock is the database-backed shared test clock
 *   (`engine_test_clock`, created here), so every worker sees one scenario time;
 * - `ENGINE_TEST_INVOKE_PAUSE` (`{"<tool>": ms}`) models a gateway invoke of
 *   those tools that is already on the wire: it logs `invoke_pending` (a
 *   marked point to SIGKILL or SIGSTOP the process at), and `ms` later the
 *   request is delivered whatever the worker did meanwhile (it is detached
 *   from the caller's abort signal, and the process waits for it before
 *   exiting, as bytes already handed to the kernel would still arrive);
 * - storage commits are timed for the load scenario.
 *
 * Test-only routes (bearer token required): `GET|POST /v1/test/clock`
 * (`{ advanceMs }`) and `GET /v1/test/stats`. They exist only here.
 *
 * Refuses to start when `NODE_ENV=production`.
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import { ModelRegistry } from "../src/models/stream.js";
import { bearerMatches, headerValue } from "../src/server/auth.js";
import type { CommitBatch, SessionStorage, StorageProvider } from "../src/storage/types.js";
import { createWorkerPool, WorkerConfigError, workerConfigFromEnv } from "../src/worker/config.js";
import { startWorker, type WorkerLog } from "../src/worker/worker.js";
import { ScriptedModel } from "../test/support/scripted-model.js";
import { SharedTestClock } from "../test/support/shared-clock.js";

const log: WorkerLog = (event, fields = {}) => {
  process.stdout.write(`${JSON.stringify({ t: Date.now(), event, ...fields })}\n`);
};

interface CommitSample {
  ms: number;
  bytes: number;
}

/** Time every storage commit and record its batch size. */
function timedStorage(provider: StorageProvider, samples: CommitSample[]): StorageProvider {
  const wrap = (storage: SessionStorage): SessionStorage =>
    new Proxy(storage, {
      get(target, prop) {
        if (prop === "commit") {
          return async (batch: CommitBatch, fence: Parameters<SessionStorage["commit"]>[1]) => {
            const bytes = Buffer.byteLength(JSON.stringify(batch));
            const start = performance.now();
            const result = await target.commit(batch, fence);
            samples.push({ ms: performance.now() - start, bytes });
            return result;
          };
        }
        const value = Reflect.get(target, prop, target);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
  return {
    open: async (sessionId) => wrap(await provider.open(sessionId)),
    sessionOfConversation: (id) => provider.sessionOfConversation(id),
    sessionOfTask: (id) => provider.sessionOfTask(id),
  };
}

/** Delayed deliveries still on the wire; the process drains them before exiting. */
const onTheWire = new Set<Promise<unknown>>();

/** Deliver gateway invokes of the listed tools late, as if delayed on the wire. */
function delayedFetch(pauses: Record<string, number>): typeof fetch {
  return (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const tool = /\/internal\/tools\/([^/]+)\/invoke$/.exec(url)?.[1];
    const ms = tool === undefined ? undefined : pauses[decodeURIComponent(tool)];
    if (tool === undefined || ms === undefined) return fetch(input, init);
    const delivery = deliverLate(tool, ms, input, init);
    onTheWire.add(delivery);
    const drop = () => onTheWire.delete(delivery);
    delivery.then(drop, drop);
    return delivery;
  };
}

async function deliverLate(
  tool: string,
  ms: number,
  input: Parameters<typeof fetch>[0],
  init: Parameters<typeof fetch>[1],
): Promise<Response> {
  const fence = new Headers(init?.headers).get("x-engine-fence");
  log("invoke_pending", { tool, fence });
  await new Promise((resolve) => setTimeout(resolve, ms));
  log("invoke_sending", { tool, fence });
  const { signal: _detached, ...rest } = init ?? {};
  const res = await fetch(input, rest);
  log("invoke_answered", { tool, fence, status: res.status });
  return res;
}

function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      try {
        const text = Buffer.concat(chunks).toString("utf8");
        resolve(text ? (JSON.parse(text) as Record<string, unknown>) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(text);
}

async function main(): Promise<number> {
  if (process.env.NODE_ENV === "production") {
    process.stderr.write("engine test harness: refusing to run with NODE_ENV=production\n");
    return 2;
  }
  let config: ReturnType<typeof workerConfigFromEnv>;
  try {
    config = workerConfigFromEnv(process.env);
  } catch (err) {
    if (err instanceof WorkerConfigError) {
      process.stderr.write(`engine test harness: ${err.message}\n`);
      return 2;
    }
    throw err;
  }
  const scriptPath = process.env.ENGINE_TEST_MODEL_SCRIPT;
  if (!scriptPath) {
    process.stderr.write("engine test harness: ENGINE_TEST_MODEL_SCRIPT is required\n");
    return 2;
  }
  const pauses = JSON.parse(process.env.ENGINE_TEST_INVOKE_PAUSE ?? "{}") as Record<string, number>;

  const pool = createWorkerPool(config);
  const clock = await SharedTestClock.open(pool);
  const samples: CommitSample[] = [];
  const models = new ModelRegistry().register(new ScriptedModel(scriptPath, "fake"));

  const intercept = async (req: IncomingMessage, res: ServerResponse): Promise<boolean> => {
    const path = (req.url ?? "/").split("?")[0];
    if (!path?.startsWith("/v1/test/")) return false;
    if (!bearerMatches(headerValue(req, "authorization") ?? undefined, config.serviceToken)) {
      send(res, 401, { error: "unauthorized" });
      return true;
    }
    if (path === "/v1/test/clock" && req.method === "GET") {
      send(res, 200, { offsetMs: clock.offsetMs, now: clock.now() });
    } else if (path === "/v1/test/clock" && req.method === "POST") {
      const body = await readJson(req);
      const advanceMs = Number(body.advanceMs);
      const offsetMs = await clock.advance(advanceMs);
      log("clock_advanced", { advanceMs, offsetMs });
      send(res, 200, { offsetMs, now: clock.now() });
    } else if (path === "/v1/test/stats" && req.method === "GET") {
      send(res, 200, { commits: samples });
    } else {
      send(res, 404, { error: "not_found" });
    }
    return true;
  };

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
      provider: "fake",
      model: "fake-model",
      summaryModel: "fake-model",
      maxTokens: config.maxTokens,
      apiKey: () => "test-key",
    },
    gateway: config.gatewayUrl
      ? {
          baseUrl: config.gatewayUrl,
          serviceToken: config.gatewayToken ?? config.serviceToken,
          fetch: delayedFetch(pauses),
        }
      : null,
    approvalTtlMs: config.approvalTtlMs,
    subagentTool: config.subagentTool,
    clock,
    settings: { progressIntervalMs: config.progressIntervalMs },
    wrapStorage: (provider) => timedStorage(provider, samples),
    intercept,
    log,
  });

  const stop = () => {
    log("signal", { action: "stop" });
    void worker.stop();
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);

  const exit = await worker.exited;
  await Promise.race([
    Promise.allSettled([...onTheWire]),
    new Promise((resolve) => setTimeout(resolve, 10_000).unref()),
  ]);
  clock.close();
  await pool.end().catch(() => {});
  if (exit.reason === "stopped") return 0;
  return exit.reason === "lease_lost" ? 3 : 1;
}

main().then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stderr.write(`engine test harness failed: ${err instanceof Error ? err.stack : err}\n`);
    process.exit(1);
  },
);
