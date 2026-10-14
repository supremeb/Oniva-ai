/**
 * Backends for the agent suite. A fixture shares one store, one manual clock
 * and one scripted model provider across "workers"; crashing a worker closes
 * its harness without letting anything finish, and the next worker reopens
 * the same session.
 */

import { Harness, type HarnessOptions } from "../../src/agent/harness.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import { ManualClock } from "../../src/core/clock.js";
import { uuidv7 } from "../../src/core/ids.js";
import { FakeProvider } from "../../src/models/fake.js";
import { ModelRegistry } from "../../src/models/stream.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { LeaseKeeper, PostgresLeases } from "../../src/storage/postgres/lease.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type { Fence, StorageProvider } from "../../src/storage/types.js";
import type { PgTestEnv } from "../support/pg.js";

export interface AgentWorker {
  harness: Harness;
  /** Stop without finishing anything (a simulated crash). */
  crash(): Promise<void>;
}

export type WorkerOptions = Partial<Omit<HarnessOptions, "sessionId" | "clock" | "models">>;

export interface AgentFixture {
  clock: ManualClock;
  provider: FakeProvider;
  open(options?: WorkerOptions): Promise<AgentWorker>;
}

export interface AgentBackend {
  name: string;
  fixture(): Promise<AgentFixture>;
}

export const allowAll: HostPolicy = {
  resolveModelRequest: () => ({
    ok: true,
    provider: "fake",
    model: "fake-1",
    apiKey: "test-key",
    maxTokens: 1024,
  }),
  authorizeTool: () => ({ decision: "allow" }),
};

type LeaseFn = (sessionId: string) => Promise<{
  fence: (() => Fence) | undefined;
  signal: AbortSignal | undefined;
  release(): Promise<void>;
}>;

function makeFixture(storage: StorageProvider, lease: LeaseFn): AgentFixture {
  const sessionId = `sess-${uuidv7()}`;
  const clock = new ManualClock(1_000_000);
  const provider = new FakeProvider();
  return {
    clock,
    provider,
    async open(options = {}) {
      const held = await lease(sessionId);
      const models = new ModelRegistry().register(provider);
      const harness = await Harness.open(storage, {
        sessionId,
        clock,
        models,
        tools: [],
        policy: allowAll,
        ...(held.fence ? { fence: held.fence } : {}),
        ...(held.signal ? { signal: held.signal } : {}),
        ...options,
      });
      return {
        harness,
        async crash() {
          await harness.close();
          await held.release();
        },
      };
    },
  };
}

export const memoryBackend: AgentBackend = {
  name: "memory",
  async fixture() {
    return makeFixture(new MemoryStorageProvider(), async () => {
      const controller = new AbortController();
      return {
        fence: undefined,
        signal: controller.signal,
        async release() {
          controller.abort(new Error("lease released"));
        },
      };
    });
  },
};

export function postgresBackend(env: PgTestEnv): AgentBackend {
  return {
    name: "postgres",
    async fixture() {
      const leases = new PostgresLeases(env.pool);
      return makeFixture(new PostgresStorageProvider(env.pool), async (sessionId) => {
        const keeper = await LeaseKeeper.acquire({ store: leases, sessionId, ttlMs: 60_000 });
        return { fence: keeper.fence, signal: keeper.signal, release: () => keeper.release() };
      });
    },
  };
}

export async function waitFor(
  predicate: () => boolean | Promise<boolean>,
  timeoutMs = 5_000,
): Promise<void> {
  const start = Date.now();
  while (!(await predicate())) {
    if (Date.now() - start > timeoutMs) throw new Error("condition not reached");
    await new Promise((r) => setTimeout(r, 5));
  }
}

/** Reject with the signal's reason once it aborts. */
export function untilAborted(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}
