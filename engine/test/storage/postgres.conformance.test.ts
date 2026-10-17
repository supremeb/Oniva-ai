import assert from "node:assert/strict";
import type pg from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LeaseLost, StorageRequestError } from "../../src/core/errors.js";
import { newId, uuidv7 } from "../../src/core/ids.js";
import { defineStorageConformanceSuite } from "../../src/storage/conformance.js";
import { PostgresLeases } from "../../src/storage/postgres/lease.js";
import { migrate } from "../../src/storage/postgres/migrate.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type {
  CommitBatch,
  ConversationRecord,
  EntryRecord,
  Fence,
  TaskRecord,
} from "../../src/storage/types.js";
import { usePostgres } from "../support/pg.js";

const env = usePostgres();
const OWNER = "conformance-worker";

async function leaseFence(sessionId: string): Promise<Fence> {
  const r = await new PostgresLeases(env.pool).acquire(sessionId, OWNER, 600_000);
  if (!r.acquired) throw new Error(`fixture could not lease ${sessionId}`);
  return { token: r.lease.token };
}

describe("postgres backend", () => {
  defineStorageConformanceSuite({ describe, it, beforeEach, afterEach }, async () => ({
    provider: new PostgresStorageProvider(env.pool),
    fence: leaseFence,
  }));
});

const conversation = (o: Partial<ConversationRecord> = {}): ConversationRecord => ({
  id: newId("conversation"),
  tenantId: "tenant-1",
  projectId: "project-1",
  ownerTaskId: null,
  createdAt: 1,
  ...o,
});
const entry = (conversationId: string, o: Partial<EntryRecord> = {}): EntryRecord => ({
  id: newId("entry"),
  conversationId,
  taskId: null,
  kind: "message",
  payload: { text: "hi" },
  createdAt: 2,
  ...o,
});
const task = (conversationId: string, o: Partial<TaskRecord> = {}): TaskRecord => ({
  id: newId("task"),
  kind: "tool",
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
  createdAt: 3,
  updatedAt: 3,
  ...o,
});

describe("postgres specifics", () => {
  it("reads back tricky JSON identically through a brand-new pool", async () => {
    const sid = `sess-${uuidv7()}`;
    const fence = await leaseFence(sid);
    const writer = await new PostgresStorageProvider(env.pool).open(sid);
    const c = conversation({ createdAt: 1_760_000_000_123.5 });
    const payload = {
      nul: "a\u0000b",
      unicode: "héllo \u{1F600}   中文",
      quote: 'say "hi" \\ there',
      nums: [0, -1, 1.25, 1e300, -1e-300, Number.MAX_SAFE_INTEGER],
      deep: { a: { b: { c: [[[null, true, false, ""]]] } } },
      empty: {},
      emptyList: [],
    };
    const t = task(c.id, {
      status: "waiting",
      waiting: { on: [], policy: "failFast", signal: "approval", until: 9_999 },
      signals: { "weird key \u0000": [1, { x: null }] },
      checkpoint: payload,
    });
    const e = entry(c.id, { taskId: t.id, payload });
    const batch: CommitBatch = {
      createConversations: [c],
      createTasks: [t],
      appendEntries: [e],
      putDocuments: [{ kind: "plan", scopeId: c.id, data: payload, updatedAt: 4 }],
    };
    assert.deepEqual(await writer.commit(batch, fence), { seq: 1 });
    await writer.close();

    const pool = env.newPool();
    const reader = await new PostgresStorageProvider(pool).open(sid);
    assert.equal(await reader.lastSeq(), 1);
    assert.deepEqual(await reader.getConversation(c.id), { ...c, seq: 1 });
    assert.deepEqual(await reader.getTask(t.id), { ...t, seq: 1 });
    assert.deepEqual(await reader.getEntry(e.id), { ...e, seq: 1 });
    assert.deepEqual(await reader.getDocument("plan", c.id), {
      kind: "plan",
      scopeId: c.id,
      data: payload,
      updatedAt: 4,
      seq: 1,
    });
    assert.deepEqual((await reader.listEntries(c.id)).items, [{ ...e, seq: 1 }]);
    await reader.close();
    await pool.end();
  });

  it("leaves no partial batch when the connection drops mid-commit", async () => {
    const sid = `sess-${uuidv7()}`;
    const fence = await leaseFence(sid);
    const victimPool = env.newPool({ max: 1, application_name: `victim-${sid}` });
    const victim = await new PostgresStorageProvider(victimPool).open(sid);
    const c = conversation();
    await victim.commit({ createConversations: [c] }, fence);

    // Hold the (session, seq 2) commit-row slot so the victim's transaction
    // blocks on its final insert, after writing every record in the batch.
    const blocker = await env.pool.connect();
    try {
      await blocker.query("BEGIN");
      await blocker.query(
        "INSERT INTO engine_commits (session_id, seq, fence_token, record_count) VALUES ($1, 2, NULL, 0)",
        [sid],
      );

      const c2 = conversation();
      const t = task(c2.id);
      const es = [entry(c.id), entry(c2.id, { taskId: t.id })];
      const pending = victim.commit(
        {
          createConversations: [c2],
          createTasks: [t],
          appendEntries: es,
          putDocuments: [{ kind: "plan", scopeId: c.id, data: [1], updatedAt: 1 }],
        },
        fence,
      );
      const outcome = pending.then(
        () => null,
        (err: unknown) => err,
      );

      const pid = await waitForBlockedBackend(`victim-${sid}`);
      await env.pool.query("SELECT pg_terminate_backend($1)", [pid]);
      const err = await outcome;
      assert.ok(err instanceof Error, "the commit must fail");
      assert.ok(!(err instanceof StorageRequestError), "a dropped connection is fatal");
      await blocker.query("ROLLBACK");

      const fresh = await new PostgresStorageProvider(env.pool).open(sid);
      assert.equal(await fresh.lastSeq(), 1);
      assert.equal(await fresh.getConversation(c2.id), null);
      assert.equal(await fresh.getTask(t.id), null);
      for (const e of es) assert.equal(await fresh.getEntry(e.id), null);
      assert.equal(await fresh.getDocument("plan", c.id), null);
      assert.equal(await new PostgresStorageProvider(env.pool).sessionOfConversation(c2.id), null);
      const { rows } = await env.pool.query(
        "SELECT count(*)::int AS n FROM engine_commits WHERE session_id = $1",
        [sid],
      );
      assert.equal(rows[0].n, 1);
      // The session continues gap-free.
      assert.deepEqual(await fresh.commit({ createConversations: [c2] }, fence), { seq: 2 });
    } finally {
      blocker.release();
    }
  });

  it("rejects a stale or missing fence with LeaseLost and writes nothing", async () => {
    const sid = `sess-${uuidv7()}`;
    const fence = await leaseFence(sid);
    const s = await new PostgresStorageProvider(env.pool).open(sid);
    const c = conversation();
    const stale = { token: Number(fence.token) + 1 };
    await expect(s.commit({ createConversations: [c] }, stale)).rejects.toBeInstanceOf(LeaseLost);
    await expect(s.commit({ createConversations: [c] }, null)).rejects.toBeInstanceOf(LeaseLost);
    const unleased = await new PostgresStorageProvider(env.pool).open(`sess-${uuidv7()}`);
    await expect(
      unleased.commit({ createConversations: [c] }, { token: 1 }),
    ).rejects.toBeInstanceOf(LeaseLost);
    assert.equal(await s.lastSeq(), 0);
    assert.equal(await s.getConversation(c.id), null);
    assert.deepEqual(await s.commit({ createConversations: [c] }, fence), { seq: 1 });
  });

  it("commits a document-only batch in five round trips", async () => {
    const pool = env.newPool();
    let statements = 0;
    // Count every statement sent on a pooled client (commits run on one).
    pool.on("connect", (client: pg.PoolClient) => {
      const query = client.query.bind(client) as (...args: unknown[]) => unknown;
      (client as unknown as { query: (...args: unknown[]) => unknown }).query = (
        ...args: unknown[]
      ) => {
        statements++;
        return query(...args);
      };
    });
    const sid = `sess-${uuidv7()}`;
    const fence = await leaseFence(sid);
    const s = await new PostgresStorageProvider(pool).open(sid);
    const c = conversation();
    await s.commit({ createConversations: [c] }, fence);
    statements = 0;
    const doc = { kind: "live", scopeId: c.id, data: { n: 1 }, updatedAt: 5 };
    assert.deepEqual(await s.commit({ putDocuments: [doc] }, fence), { seq: 2 });
    // BEGIN, lease+counters lock, document upsert, counters+commit row, COMMIT.
    assert.equal(statements, 5);
    const { rows } = await pool.query(
      "SELECT seq, fence_token, record_count FROM engine_commits WHERE session_id = $1 ORDER BY seq",
      [sid],
    );
    assert.deepEqual(
      rows.map((r) => [Number(r.seq), Number(r.fence_token), r.record_count]),
      [
        [1, Number(fence.token), 1],
        [2, Number(fence.token), 1],
      ],
    );
  });

  it("runs migrations idempotently and records them", async () => {
    assert.deepEqual(await migrate(env.pool), []);
    await Promise.all([migrate(env.pool), migrate(env.pool)]);
    const { rows } = await env.pool.query("SELECT version FROM engine_migrations ORDER BY version");
    assert.deepEqual(
      rows.map((r) => r.version),
      ["001", "002", "004"],
    );
  });
});

async function waitForBlockedBackend(applicationName: string): Promise<number> {
  for (let i = 0; i < 200; i++) {
    const { rows } = await env.pool.query(
      `SELECT pid FROM pg_stat_activity
        WHERE application_name = $1 AND wait_event_type = 'Lock'`,
      [applicationName],
    );
    if (rows[0]) return rows[0].pid as number;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error("victim backend never blocked");
}

describe("postgres usage outbox claiming", () => {
  it("two concurrent claimers get disjoint rows covering every pending row", async () => {
    await env.pool.query("DELETE FROM engine_usage_outbox");
    const provider = new PostgresStorageProvider(env.pool);
    const ids = Array.from({ length: 40 }, (_, i) => `req-${String(i).padStart(2, "0")}`);
    for (const [i, requestId] of ids.entries()) {
      await provider.enqueue({ requestId, body: { i }, createdAt: i, claimedAt: null });
    }
    for (let round = 0; round < 5; round++) {
      await env.pool.query("UPDATE engine_usage_outbox SET claimed_at = NULL");
      const [a, b] = await Promise.all([
        provider.claimPending(60_000),
        provider.claimPending(60_000),
      ]);
      const idsA = a.map((r) => r.requestId);
      const idsB = b.map((r) => r.requestId);
      expect(idsA.filter((id) => idsB.includes(id))).toEqual([]);
      expect([...idsA, ...idsB].sort()).toEqual(ids);
    }
  });
});
