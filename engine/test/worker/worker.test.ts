import { afterEach, describe, expect, it } from "vitest";
import { uuidv7 } from "../../src/core/ids.js";
import { FakeProvider } from "../../src/models/fake.js";
import { ModelRegistry } from "../../src/models/stream.js";
import { PostgresLeases } from "../../src/storage/postgres/lease.js";
import type { SessionStorage, StorageProvider } from "../../src/storage/types.js";
import { startWorker, type WorkerHandle, type WorkerOptions } from "../../src/worker/worker.js";
import { usePostgres } from "../support/pg.js";

const env = usePostgres();

/** Wrap every opened session storage, overriding some of its methods. */
function wrapSessions(
  provider: StorageProvider,
  override: (target: SessionStorage) => Partial<SessionStorage>,
): StorageProvider {
  const wrap = (storage: SessionStorage): SessionStorage => {
    const extra = override(storage);
    return new Proxy(storage, {
      get(target, prop) {
        if (prop in extra) return extra[prop as keyof SessionStorage];
        const value = Reflect.get(target, prop, target);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
  };
  return {
    open: async (sessionId) => wrap(await provider.open(sessionId)),
    sessionOfConversation: (id) => provider.sessionOfConversation(id),
    sessionOfTask: (id) => provider.sessionOfTask(id),
    enqueue: (record) => provider.enqueue(record),
    claimPending: (claimTimeoutMs) => provider.claimPending(claimTimeoutMs),
    ack: (requestId) => provider.ack(requestId),
  };
}

const workers: WorkerHandle[] = [];
afterEach(async () => {
  for (const w of workers.splice(0)) await w.stop();
});

function options(
  sessionId: string,
  events: string[],
  extra: Partial<WorkerOptions> = {},
): WorkerOptions {
  return {
    pool: env.newPool(),
    sessionId,
    serviceToken: "tok",
    leaseTtlMs: 3_000,
    leaseRetryMs: 25,
    migrate: false,
    models: new ModelRegistry().register(new FakeProvider()),
    modelDefaults: {
      provider: "fake",
      model: "m",
      summaryModel: "s",
      maxTokens: 10,
      apiKey: () => "k",
    },
    log: (event) => events.push(event),
    ...extra,
  };
}

async function liveOwner(sessionId: string): Promise<string | null> {
  const lease = await new PostgresLeases(env.pool).current(sessionId);
  return lease?.live ? lease.ownerId : null;
}

describe("startWorker", () => {
  it("fails and releases the lease when its session fails on a storage error", async () => {
    const sessionId = `sess-${uuidv7()}`;
    const events: string[] = [];
    let broken = false;
    const w = await startWorker(
      options(sessionId, events, {
        wrapStorage: (p) =>
          wrapSessions(p, (target) => ({
            commit: (batch, fence) =>
              broken ? Promise.reject(new Error("disk on fire")) : target.commit(batch, fence),
          })),
      }),
    );
    workers.push(w);
    await w.serving;
    expect(await liveOwner(sessionId)).toBe(w.ownerId);

    broken = true;
    const harness = w.harness;
    if (!harness) throw new Error("no harness while serving");
    await expect(harness.createConversation({ tenantId: "t", projectId: "p" })).rejects.toThrow(
      /disk on fire/,
    );

    const exit = await w.exited;
    expect(exit.reason).toBe("failed");
    expect(String((exit as { error: unknown }).error)).toMatch(/disk on fire/);
    expect(w.state).toBe("stopped");
    expect(events).toContain("failed");
    // Released, not left to lapse: a successor can take over at once.
    expect(await liveOwner(sessionId)).toBeNull();
    const successor = await startWorker(options(sessionId, []));
    workers.push(successor);
    await successor.serving;
    expect(await liveOwner(sessionId)).toBe(successor.ownerId);
  });

  it("bounds a wedged harness close at shutdown and still releases the lease", async () => {
    const sessionId = `sess-${uuidv7()}`;
    const events: string[] = [];
    const w = await startWorker(
      options(sessionId, events, {
        closeTimeoutMs: 200,
        wrapStorage: (p) => wrapSessions(p, () => ({ close: () => new Promise<void>(() => {}) })),
      }),
    );
    await w.serving;
    const started = Date.now();
    await w.stop();
    expect(Date.now() - started).toBeLessThan(2_000);
    expect(await w.exited).toEqual({ reason: "stopped" });
    expect(events).toContain("close_timeout");
    expect(await liveOwner(sessionId)).toBeNull();
  });

  it("stays on standby through a gateway outage at startup, then serves", async () => {
    const sessionId = `sess-${uuidv7()}`;
    const events: string[] = [];
    let manifestCalls = 0;
    const fakeFetch: typeof fetch = async () => {
      manifestCalls++;
      if (manifestCalls <= 2) {
        return new Response(JSON.stringify({ error: "down" }), { status: 400 });
      }
      return new Response(JSON.stringify({ tools: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    const w = await startWorker(
      options(sessionId, events, {
        gateway: { baseUrl: "http://gateway.invalid", serviceToken: "g", fetch: fakeFetch },
      }),
    );
    workers.push(w);
    await w.serving;
    expect(w.state).toBe("serving");
    expect(manifestCalls).toBe(3);
    expect(events.filter((e) => e === "gateway_unavailable")).toHaveLength(2);
    expect(events).not.toContain("failed");
  });

  it("registers the workflow task kinds and advertises them in /v1/health taskKinds (U35)", async () => {
    const sessionId = `sess-${uuidv7()}`;
    const w = await startWorker(
      options(sessionId, [], {
        workflow: { baseUrl: "http://workflow.invalid", serviceToken: "w" },
        hostTasks: [],
      }),
    );
    workers.push(w);
    await w.serving;
    const res = await fetch(`${w.url}/v1/health`);
    const body = (await res.json()) as { taskKinds: string[] };
    for (const kind of ["workflow_run", "workflow_node", "workflow_fanout_lane", "workflow_gate"]) {
      expect(body.taskKinds).toContain(kind);
    }
  });

  it("registers no workflow kinds without a workflow backend", async () => {
    const w = await startWorker(options(`sess-${uuidv7()}`, []));
    workers.push(w);
    await w.serving;
    const body = (await (await fetch(`${w.url}/v1/health`)).json()) as { taskKinds: string[] };
    expect(body.taskKinds.some((k) => k.startsWith("workflow_"))).toBe(false);
  });
});
