import { z } from "zod";
import { ManualClock } from "../../src/core/clock.js";
import type { JsonValue } from "../../src/core/json.js";
import { defineDoc } from "../../src/session/documents.js";
import { Session, type SessionOptions } from "../../src/session/session.js";
import type { Transaction } from "../../src/session/transaction.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import type { CommitBatch, Fence, SessionStorage, TaskRecord } from "../../src/storage/types.js";

/** A storage handle whose commit can be made to fail on demand. */
export class FlakyStorage {
  failNextCommit: unknown = null;
  readonly batches: CommitBatch[] = [];
  readonly fences: Array<Fence | null> = [];

  constructor(readonly inner: SessionStorage) {}

  handle(): SessionStorage {
    const self = this;
    return new Proxy(this.inner, {
      get(target, prop, receiver) {
        if (prop === "commit") {
          return async (batch: CommitBatch, fence: Fence | null) => {
            if (self.failNextCommit !== null) {
              const err = self.failNextCommit;
              self.failNextCommit = null;
              throw err;
            }
            const result = await target.commit(batch, fence);
            self.batches.push(structuredClone(batch));
            self.fences.push(fence);
            return result;
          };
        }
        const value = Reflect.get(target, prop, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
  }
}

export interface Harness {
  provider: MemoryStorageProvider;
  flaky: FlakyStorage;
  session: Session;
  clock: ManualClock;
  conversationId: string;
}

export async function openHarness(
  options: Partial<SessionOptions> = {},
  provider = new MemoryStorageProvider(),
): Promise<Harness> {
  const flaky = new FlakyStorage(await provider.open("sess-1"));
  const clock = new ManualClock(1_000);
  const session = await Session.open(flaky.handle(), { clock, ...options });
  const conversationId = await session.commit(
    (tx) => tx.createConversation({ tenantId: "t1", projectId: "p1" }).id,
  );
  return { provider, flaky, session, clock, conversationId };
}

export function newTask(tx: Transaction, conversationId: string, kind = "demo"): TaskRecord {
  return tx.createTask({
    kind,
    conversationId,
    parentTaskId: null,
    status: "pending",
    phase: "start",
    checkpoint: null,
    input: null,
    waiting: null,
    signals: {},
    outcome: null,
    abandonOnRestart: false,
  });
}

export const counterDoc = defineDoc({
  kind: "counter",
  scope: "conversation",
  initial: { count: 0, notes: [] as string[] },
  schema: z.object({ count: z.number().int().nonnegative(), notes: z.array(z.string()) }),
});

export function payloadOf(batch: CommitBatch): JsonValue[] {
  return (batch.appendEntries ?? []).map((e) => e.payload);
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
