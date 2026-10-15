/**
 * Backends for the task runtime suite. A backend opens "workers" over one
 * shared store; crashing a worker closes its runtime and session without
 * letting anything finish, and the next worker reopens the same session.
 */
import { ManualClock } from "../../src/core/clock.js";
import { uuidv7 } from "../../src/core/ids.js";
import { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { LeaseKeeper, PostgresLeases } from "../../src/storage/postgres/lease.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type { Fence, StorageProvider } from "../../src/storage/types.js";
import type { TaskDefinition } from "../../src/tasks/define.js";
import { TaskRuntime, type TaskRuntimeOptions } from "../../src/tasks/runtime.js";
import type { PgTestEnv } from "../support/pg.js";

export interface Worker {
  session: Session;
  runtime: TaskRuntime;
  /** Lease loss / release for this worker (aborts its runtime's signal). */
  loseLease(): Promise<void>;
  /** Stop without finishing anything (a simulated crash). */
  crash(): Promise<void>;
}

export interface Fixture {
  clock: ManualClock;
  conversationId: string;
  /** Open a worker on the shared store with these task definitions. */
  open(
    tasks: TaskDefinition[],
    options?: Partial<Omit<TaskRuntimeOptions, "tasks" | "clock">>,
  ): Promise<Worker>;
}

export interface Backend {
  name: string;
  fixture(): Promise<Fixture>;
}

async function makeFixture(
  provider: StorageProvider,
  lease: (sessionId: string) => Promise<{
    fence: (() => Fence) | undefined;
    signal: AbortSignal | undefined;
    release(): Promise<void>;
  }>,
): Promise<Fixture> {
  const sessionId = `sess-${uuidv7()}`;
  const clock = new ManualClock(1_000_000);
  let conversationId = "";

  const open: Fixture["open"] = async (tasks, options = {}) => {
    const held = await lease(sessionId);
    const session = await Session.open(await provider.open(sessionId), {
      clock,
      ...(held.fence ? { fence: held.fence } : {}),
    });
    if (!conversationId) {
      conversationId = await session.commit(
        (tx) => tx.createConversation({ tenantId: "t1", projectId: "p1" }).id,
      );
    }
    const runtime = await TaskRuntime.open(session, {
      tasks,
      clock,
      ...(held.signal ? { signal: held.signal } : {}),
      ...options,
    });
    return {
      session,
      runtime,
      loseLease: () => held.release(),
      async crash() {
        await runtime.close();
        await session.close();
        await held.release();
      },
    };
  };

  return {
    clock,
    get conversationId() {
      return conversationId;
    },
    open,
  };
}

export const memoryBackend: Backend = {
  name: "memory",
  fixture() {
    const provider = new MemoryStorageProvider();
    return makeFixture(provider, async () => {
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

export function postgresBackend(env: PgTestEnv): Backend {
  return {
    name: "postgres",
    fixture() {
      const provider = new PostgresStorageProvider(env.pool);
      const leases = new PostgresLeases(env.pool);
      return makeFixture(provider, async (sessionId) => {
        const keeper = await LeaseKeeper.acquire({ store: leases, sessionId, ttlMs: 60_000 });
        return { fence: keeper.fence, signal: keeper.signal, release: () => keeper.release() };
      });
    },
  };
}

export function deferred<T = void>(): {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
} {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Resolve when `signal` aborts, rejecting with its reason (a handler that blocks until cancelled). */
export function untilAborted(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}
