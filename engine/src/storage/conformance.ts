/**
 * Backend-agnostic storage conformance suite (R15).
 *
 * Every storage backend must pass this suite. It takes the test runner's
 * `describe`/`it`/`beforeEach`/`afterEach` and an async fixture factory, and
 * asserts with `node:assert`, so it has no runtime dependency on a test
 * framework:
 *
 * ```ts
 * import { afterEach, beforeEach, describe, it } from "vitest";
 * defineStorageConformanceSuite({ describe, it, beforeEach, afterEach }, async () => ({
 *   provider: new MemoryStorageProvider(),
 *   fence: async () => null,
 * }));
 * ```
 *
 * Each test uses fresh random session ids, so fixtures may share one database.
 */
import assert from "node:assert/strict";
import { StorageRequestError } from "../core/errors.js";
import { type IdKind, isId, newId, uuidv7 } from "../core/ids.js";
import type {
  CommitBatch,
  ConversationRecord,
  DocumentRecord,
  EntryRecord,
  Fence,
  Page,
  PageOptions,
  SessionStorage,
  StorageProvider,
  SubmissionRecord,
  TaskRecord,
  UsageOutboxRecord,
} from "./types.js";
import { MAX_PAGE_LIMIT } from "./types.js";

/** The subset of a test runner's API the suite needs. */
export interface ConformanceApi {
  describe(name: string, body: () => void): void;
  it(name: string, body: () => Promise<void>): void;
  beforeEach(body: () => Promise<void>): void;
  afterEach(body: () => Promise<void>): void;
}

/** A provider under test plus how to obtain a valid commit fence for a session. */
export interface StorageFixture {
  provider: StorageProvider;
  /** A fence that the provider accepts for commits to `sessionId` (null if unfenced). */
  fence(sessionId: string): Promise<Fence | null>;
  /** Release fixture resources after each test. */
  close?(): Promise<void>;
}

export function defineStorageConformanceSuite(
  api: ConformanceApi,
  factory: () => Promise<StorageFixture>,
): void {
  const { describe, it, beforeEach, afterEach } = api;

  describe("storage conformance", () => {
    let fixture: StorageFixture;
    let handles: SessionStorage[];
    let sid: string;
    let s: SessionStorage;
    let fence: Fence | null;

    const open = async (sessionId: string): Promise<SessionStorage> => {
      const h = await fixture.provider.open(sessionId);
      handles.push(h);
      return h;
    };
    const commit = (batch: CommitBatch, on: SessionStorage = s) =>
      on.commit(batch, on === s ? fence : null);
    const commitIn = async (h: SessionStorage, batch: CommitBatch) =>
      h.commit(batch, await fixture.fence(h.sessionId));
    const id = (kind: IdKind) => newId(kind);

    beforeEach(async () => {
      fixture = await factory();
      handles = [];
      sid = `sess-${uuidv7()}`;
      s = await open(sid);
      fence = await fixture.fence(sid);
    });

    afterEach(async () => {
      for (const h of handles) await h.close();
      await fixture.close?.();
    });

    /** Commit one conversation and return it. */
    const newConversation = async (
      overrides: Partial<ConversationRecord> = {},
    ): Promise<ConversationRecord> => {
      const c = conversation(id("conversation"), overrides);
      await commit({ createConversations: [c] });
      return c;
    };

    describe("sessions and seq", () => {
      it("starts an empty session at seq 0 and numbers commits 1, 2, 3", async () => {
        assert.equal(s.sessionId, sid);
        assert.equal(await s.lastSeq(), 0);
        const c = conversation(id("conversation"));
        assert.deepEqual(await commit({ createConversations: [c] }), { seq: 1 });
        assert.deepEqual(await commit({ appendEntries: [entry(id("entry"), c.id)] }), { seq: 2 });
        assert.deepEqual(await commit({ appendEntries: [entry(id("entry"), c.id)] }), { seq: 3 });
        assert.equal(await s.lastSeq(), 3);
      });

      it("keeps seq strictly increasing and gap-free across a reopen", async () => {
        const c = await newConversation();
        await commit({ appendEntries: [entry(id("entry"), c.id)] });
        await s.close();
        const again = await open(sid);
        assert.equal(await again.lastSeq(), 2);
        const r = await commitIn(again, { appendEntries: [entry(id("entry"), c.id)] });
        assert.equal(r.seq, 3);
        assert.equal(await again.lastSeq(), 3);
      });

      it("reads back every committed record identically after a reopen", async () => {
        const c = conversation(id("conversation"));
        const t = task(id("task"), c.id, { status: "waiting", waiting: waitSpec() });
        const e = entry(id("entry"), c.id, { taskId: t.id, payload: { nested: [1, "two", null] } });
        const sub = submission(id("submission"), c.id, "req-1");
        const d = doc("settings", c.id, { a: { b: [true, 1.5] } });
        await commit({
          createConversations: [c],
          createTasks: [t],
          appendEntries: [e],
          createSubmissions: [sub],
          putDocuments: [d],
        });
        await s.close();
        const again = await open(sid);
        assert.deepEqual(await again.getConversation(c.id), { ...c, seq: 1 });
        assert.deepEqual(await again.getTask(t.id), { ...t, seq: 1 });
        assert.deepEqual(await again.getEntry(e.id), { ...e, seq: 1 });
        assert.deepEqual(await again.getSubmission(sub.id), { ...sub, seq: 1 });
        assert.deepEqual(await again.submissionByRequestId(c.id, "req-1"), { ...sub, seq: 1 });
        assert.deepEqual(await again.getDocument("settings", c.id), { ...d, seq: 1 });
      });

      it("serializes concurrent commits on one handle into distinct consecutive seqs", async () => {
        const c = await newConversation();
        const results = await Promise.all(
          Array.from({ length: 6 }, () => commit({ appendEntries: [entry(id("entry"), c.id)] })),
        );
        const seqs = results.map((r) => r.seq).sort((a, b) => a - b);
        assert.deepEqual(seqs, [2, 3, 4, 5, 6, 7]);
        assert.equal(await s.lastSeq(), 7);
      });

      it("isolates sessions: own seq line, no cross-session reads", async () => {
        const other = await open(`sess-${uuidv7()}`);
        const c = await newConversation();
        assert.equal(await other.lastSeq(), 0);
        assert.equal(await other.getConversation(c.id), null);
        const c2 = conversation(id("conversation"));
        assert.equal((await commitIn(other, { createConversations: [c2] })).seq, 1);
        assert.equal(await s.getConversation(c2.id), null);
        assert.deepEqual(await fixture.provider.sessionOfConversation(c.id), sid);
        assert.deepEqual(await fixture.provider.sessionOfConversation(c2.id), other.sessionId);
        assert.equal(await fixture.provider.sessionOfConversation("conv_missing"), null);
      });

      it("maps tasks to their session", async () => {
        const c = conversation(id("conversation"));
        const t = task(id("task"), c.id);
        await commit({ createConversations: [c], createTasks: [t] });
        assert.equal(await fixture.provider.sessionOfTask(t.id), sid);
        assert.equal(await fixture.provider.sessionOfTask("task_missing"), null);
      });

      it("rejects conversation and task ids already used by another session", async () => {
        const c = conversation(id("conversation"));
        const t = task(id("task"), c.id);
        await commit({ createConversations: [c], createTasks: [t] });
        const other = await open(`sess-${uuidv7()}`);
        await rejectsRequest(commitIn(other, { createConversations: [{ ...c }] }));
        const c2 = conversation(id("conversation"));
        await rejectsRequest(
          commitIn(other, {
            createConversations: [c2],
            createTasks: [{ ...t, conversationId: c2.id }],
          }),
        );
        assert.equal(await other.lastSeq(), 0);
      });

      it("mints distinct ids of the requested kind", async () => {
        const kinds: IdKind[] = ["conversation", "entry", "task", "submission", "approval"];
        const seen = new Set<string>();
        for (const kind of kinds) {
          for (let i = 0; i < 20; i++) {
            const v = s.mintId(kind);
            assert.ok(isId(kind, v), `${v} is not a ${kind} id`);
            seen.add(v);
          }
        }
        assert.equal(seen.size, kinds.length * 20);
      });

      it("rejects calls on a closed handle", async () => {
        await s.close();
        await s.close();
        await assert.rejects(s.lastSeq());
        await assert.rejects(s.getConversation("conv_x"));
        await assert.rejects(
          s.commit({ createConversations: [conversation(id("conversation"))] }, fence),
        );
      });
    });

    describe("atomic commits", () => {
      it("makes a batch of entry, task and document visible at once, stamped with its seq", async () => {
        const c = await newConversation();
        const t = task(id("task"), c.id);
        const e = entry(id("entry"), c.id, { taskId: t.id });
        const d = doc("plan", t.id, { steps: ["a"] });
        assert.equal(await s.getTask(t.id), null);
        assert.equal(await s.getEntry(e.id), null);
        assert.equal(await s.getDocument("plan", t.id), null);
        const { seq } = await commit({ appendEntries: [e], createTasks: [t], putDocuments: [d] });
        assert.equal(seq, 2);
        assert.deepEqual(await s.getEntry(e.id), { ...e, seq: 2 });
        assert.deepEqual(await s.getTask(t.id), { ...t, seq: 2 });
        assert.deepEqual(await s.getDocument("plan", t.id), { ...d, seq: 2 });
      });

      it("leaves nothing behind when validation rejects a batch, and stays open", async () => {
        const c = conversation(id("conversation"));
        const t = task(id("task"), c.id);
        const e = entry(id("entry"), c.id);
        const bad = entry(id("entry"), "conv_unknown");
        await rejectsRequest(
          commit({
            createConversations: [c],
            createTasks: [t],
            appendEntries: [e, bad],
            putDocuments: [doc("plan", c.id, 1)],
          }),
        );
        assert.equal(await s.lastSeq(), 0);
        assert.equal(await s.getConversation(c.id), null);
        assert.equal(await s.getTask(t.id), null);
        assert.equal(await s.getEntry(e.id), null);
        assert.equal(await s.getDocument("plan", c.id), null);
        assert.equal(await fixture.provider.sessionOfConversation(c.id), null);
        assert.equal(await fixture.provider.sessionOfTask(t.id), null);
        // The session is still usable and the rejected batch consumed no seq.
        assert.equal((await commit({ createConversations: [c], createTasks: [t] })).seq, 1);
      });

      it("allows records in one batch to reference each other", async () => {
        const c = conversation(id("conversation"));
        const parent = task(id("task"), c.id);
        const child = task(id("task"), c.id, { parentTaskId: parent.id });
        const sub = conversation(id("conversation"), { ownerTaskId: child.id });
        await commit({ createConversations: [c, sub], createTasks: [parent, child] });
        assert.equal((await s.getConversation(sub.id))?.ownerTaskId, child.id);
        assert.equal((await s.getTask(child.id))?.parentTaskId, parent.id);
      });

      it("rejects an empty batch", async () => {
        await rejectsRequest(commit({}));
        await rejectsRequest(commit({ appendEntries: [], createTasks: [] }));
        assert.equal(await s.lastSeq(), 0);
      });
    });

    describe("validation", () => {
      it("rejects rewriting an existing entry id, in a later batch or the same one", async () => {
        const c = await newConversation();
        const e = entry(id("entry"), c.id, { payload: "first" });
        await commit({ appendEntries: [e] });
        await rejectsRequest(commit({ appendEntries: [{ ...e, payload: "second" }] }));
        const twice = entry(id("entry"), c.id);
        await rejectsRequest(commit({ appendEntries: [twice, { ...twice }] }));
        assert.deepEqual((await s.getEntry(e.id))?.payload, "first");
        assert.equal(await s.getEntry(twice.id), null);
      });

      it("rejects a duplicate requestId within a conversation but allows it across conversations", async () => {
        const c1 = await newConversation();
        const c2 = await newConversation();
        const first = submission(id("submission"), c1.id, "req-1");
        await commit({ createSubmissions: [first] });
        const dup = submission(id("submission"), c1.id, "req-1");
        await rejectsRequest(commit({ createSubmissions: [dup] }));
        const a = submission(id("submission"), c1.id, "req-2");
        const b = submission(id("submission"), c1.id, "req-2");
        await rejectsRequest(commit({ createSubmissions: [a, b] }));
        assert.equal(await s.getSubmission(dup.id), null);
        assert.equal(await s.submissionByRequestId(c1.id, "req-2"), null);

        const other = submission(id("submission"), c2.id, "req-1");
        await commit({ createSubmissions: [other] });
        assert.equal((await s.submissionByRequestId(c1.id, "req-1"))?.id, first.id);
        assert.equal((await s.submissionByRequestId(c2.id, "req-1"))?.id, other.id);
      });

      it("rejects a duplicate submission id", async () => {
        const c = await newConversation();
        const sub = submission(id("submission"), c.id, "req-1");
        await commit({ createSubmissions: [sub] });
        await rejectsRequest(commit({ createSubmissions: [{ ...sub, requestId: "req-2" }] }));
      });

      it("replaces submissions whole and protects their immutable fields", async () => {
        const c = await newConversation();
        const sub = submission(id("submission"), c.id, "req-1");
        await commit({ createSubmissions: [sub] });
        const done = { ...sub, status: "done" as const, result: { ok: true }, updatedAt: 5 };
        const { seq } = await commit({ replaceSubmissions: [done] });
        assert.deepEqual(await s.getSubmission(sub.id), { ...done, seq });
        await rejectsRequest(commit({ replaceSubmissions: [{ ...done, requestId: "req-9" }] }));
        await rejectsRequest(commit({ replaceSubmissions: [{ ...done, input: "changed" }] }));
        await rejectsRequest(commit({ replaceSubmissions: [{ ...done, createdAt: 99 }] }));
        await rejectsRequest(
          commit({ replaceSubmissions: [submission(id("submission"), c.id, "req-3")] }),
        );
      });

      it("creates tasks once, replaces them whole, and protects immutable fields", async () => {
        const c = await newConversation();
        const t = task(id("task"), c.id);
        await commit({ createTasks: [t] });
        await rejectsRequest(commit({ createTasks: [{ ...t }] }));
        await rejectsRequest(commit({ replaceTasks: [task(id("task"), c.id)] }));

        const next: TaskRecord = {
          ...t,
          status: "terminal",
          phase: "final",
          checkpoint: { n: 2 },
          outcome: { type: "done", value: "ok" },
          signals: { approval: { approved: true } },
          updatedAt: 10,
        };
        const { seq } = await commit({ replaceTasks: [next] });
        assert.deepEqual(await s.getTask(t.id), { ...next, seq });

        for (const change of [
          { kind: "other" },
          { conversationId: (await newConversation()).id },
          { parentTaskId: t.id },
          { createdAt: 12345 },
        ]) {
          await rejectsRequest(commit({ replaceTasks: [{ ...next, ...change }] }));
        }
        await rejectsRequest(commit({ createTasks: [t], replaceTasks: [t] }));
      });

      it("enforces task status invariants", async () => {
        const c = await newConversation();
        const bad: TaskRecord[] = [
          task(id("task"), c.id, { status: "terminal" }),
          task(id("task"), c.id, { outcome: { type: "aborted", reason: "x" } }),
          task(id("task"), c.id, { status: "waiting" }),
          task(id("task"), c.id, { waiting: waitSpec() }),
          task(id("task"), c.id, { status: "sleeping" as never }),
          task(id("task"), c.id, { parentTaskId: "task_missing" }),
          task(id("task"), c.id, {
            status: "terminal",
            outcome: { type: "exploded" } as never,
          }),
        ];
        for (const t of bad) await rejectsRequest(commit({ createTasks: [t] }));
        assert.equal(await s.lastSeq(), 1);
      });

      it("rejects references to records that do not exist", async () => {
        const c = await newConversation();
        await rejectsRequest(commit({ createTasks: [task(id("task"), "conv_unknown")] }));
        await rejectsRequest(
          commit({ appendEntries: [entry(id("entry"), c.id, { taskId: "task_missing" })] }),
        );
        await rejectsRequest(
          commit({ createSubmissions: [submission(id("submission"), "conv_unknown", "r")] }),
        );
        await rejectsRequest(
          commit({
            createConversations: [conversation(id("conversation"), { ownerTaskId: "task_x" })],
          }),
        );
        assert.equal(await s.lastSeq(), 1);
      });

      it("rejects records that are not strict JSON or have the wrong shape", async () => {
        const c = await newConversation();
        const bad: CommitBatch[] = [
          { appendEntries: [entry(id("entry"), c.id, { payload: undefined as never })] },
          { appendEntries: [entry(id("entry"), c.id, { payload: new Date() as never })] },
          { appendEntries: [entry(id("entry"), c.id, { payload: Number.NaN })] },
          { appendEntries: [{ ...entry(id("entry"), c.id), extra: 1 } as EntryRecord] },
          { appendEntries: [{ ...entry(id("entry"), c.id), kind: 7 } as never] },
          { appendEntries: [{ ...entry(id("entry"), c.id), id: "" }] },
          {
            createConversations: [{ ...conversation(id("conversation")), tenantId: null } as never],
          },
          { putDocuments: [doc("", c.id, 1)] },
          { putDocuments: [{ ...doc("k", c.id, 1), data: () => 1 } as never] },
          { createTasks: [{ ...task(id("task"), c.id), signals: [] } as never] },
          { unknownList: [] } as never,
        ];
        for (const batch of bad) await rejectsRequest(commit(batch));
        assert.equal(await s.lastSeq(), 1);
      });

      it("replaces documents by (kind, scopeId)", async () => {
        const c = await newConversation();
        await commit({ putDocuments: [doc("plan", c.id, { v: 1 }), doc("notes", c.id, "x")] });
        const { seq } = await commit({ putDocuments: [doc("plan", c.id, { v: 2 }, 7)] });
        assert.deepEqual(await s.getDocument("plan", c.id), {
          ...doc("plan", c.id, { v: 2 }, 7),
          seq,
        });
        assert.equal((await s.getDocument("notes", c.id))?.data, "x");
        assert.equal(await s.getDocument("plan", "other-scope"), null);
        await rejectsRequest(
          commit({ putDocuments: [doc("plan", c.id, 1), doc("plan", c.id, 2)] }),
        );
      });

      it("reads many documents of one kind at once, aligned with the scope ids", async () => {
        const c = await newConversation();
        const t = task(id("task"), c.id);
        await commit({
          createTasks: [t],
          putDocuments: [
            doc("plan", c.id, { v: 1 }),
            doc("plan", t.id, [1]),
            doc("notes", t.id, 2),
          ],
        });
        const { seq } = await commit({ putDocuments: [doc("plan", t.id, [2], 9)] });
        const other = await open(`sess-${uuidv7()}`);
        await commitIn(other, {
          createConversations: [conversation(id("conversation"))],
          putDocuments: [doc("plan", "shared-scope", "theirs")],
        });

        assert.deepEqual(await s.getDocuments("plan", []), []);
        const got = await s.getDocuments("plan", [t.id, "missing", c.id, "shared-scope", t.id]);
        assert.deepEqual(got, [
          { ...doc("plan", t.id, [2], 9), seq },
          null,
          { ...doc("plan", c.id, { v: 1 }), seq: seq - 1 },
          null,
          { ...doc("plan", t.id, [2], 9), seq },
        ]);
        assert.deepEqual(await s.getDocuments("notes", [c.id, t.id]), [
          null,
          { ...doc("notes", t.id, 2), seq: seq - 1 },
        ]);
        // Returned values are copies.
        (must(got[0]).data as number[]).push(3);
        assert.deepEqual((await s.getDocuments("plan", [t.id]))[0]?.data, [2]);

        await rejectsRequest(s.getDocuments("plan", "x" as never));
        await rejectsRequest(s.getDocuments("plan", [1 as never]));
        const tooMany = Array.from({ length: MAX_PAGE_LIMIT + 1 }, (_, i) => `scope-${i}`);
        await rejectsRequest(s.getDocuments("plan", tooMany));
        await s.close();
        await assert.rejects(s.getDocuments("plan", [c.id]));
      });
    });

    describe("copy semantics", () => {
      it("is not affected by mutating committed inputs or returned values", async () => {
        const c = conversation(id("conversation"));
        const t = task(id("task"), c.id, { checkpoint: { list: [1, 2] } });
        const e = entry(id("entry"), c.id, { payload: { text: "hello", parts: [{ a: 1 }] } });
        const d = doc("plan", c.id, { steps: ["one"] });
        const batch: CommitBatch = {
          createConversations: [c],
          createTasks: [t],
          appendEntries: [e],
          putDocuments: [d],
        };
        await commit(batch);
        (t.checkpoint as { list: number[] }).list.push(3);
        (e.payload as { text: string }).text = "mutated";
        (d.data as { steps: string[] }).steps.length = 0;
        batch.appendEntries?.push(entry(id("entry"), c.id));

        const readTask = must(await s.getTask(t.id));
        assert.deepEqual(readTask.checkpoint, { list: [1, 2] });
        (readTask.checkpoint as { list: number[] }).list.push(9);
        assert.deepEqual((await s.getTask(t.id))?.checkpoint, { list: [1, 2] });

        const readEntry = must(await s.getEntry(e.id));
        assert.deepEqual(readEntry.payload, { text: "hello", parts: [{ a: 1 }] });
        ((readEntry.payload as { parts: Array<{ a: number }> }).parts[0] as { a: number }).a = 5;
        const listed = await s.listEntries(c.id);
        assert.equal(listed.items.length, 1);
        assert.deepEqual(listed.items[0]?.payload, { text: "hello", parts: [{ a: 1 }] });
        (listed.items[0]?.payload as { text: string }).text = "x";
        assert.deepEqual((await s.getEntry(e.id))?.payload, {
          text: "hello",
          parts: [{ a: 1 }],
        });

        const readDoc = must(await s.getDocument("plan", c.id));
        (readDoc.data as { steps: string[] }).steps.push("two");
        assert.deepEqual((await s.getDocument("plan", c.id))?.data, { steps: ["one"] });
      });
    });

    describe("scans", () => {
      it("pages entries in both orders with no duplicates or gaps", async () => {
        const c = await newConversation();
        const noise = await newConversation();
        const ids: string[] = [];
        for (let batch = 0; batch < 5; batch++) {
          const es = Array.from({ length: 5 }, () => entry(id("entry"), c.id));
          ids.push(...es.map((e) => e.id));
          await commit({ appendEntries: [...es, entry(id("entry"), noise.id)] });
        }
        for (const limit of [1, 3, 7, 25, 100]) {
          const asc = await drain((o) => s.listEntries(c.id, o), { order: "asc", limit });
          assert.deepEqual(
            asc.map((e) => e.id),
            ids,
          );
          const desc = await drain((o) => s.listEntries(c.id, o), { order: "desc", limit });
          assert.deepEqual(
            desc.map((e) => e.id),
            [...ids].reverse(),
          );
        }
        const first = await s.listEntries(c.id, { limit: 25 });
        assert.equal(first.items.length, 25);
        assert.equal(first.nextCursor, null);
        const all = await s.listEntries(c.id);
        assert.equal(all.items.length, 25, "default order is asc and default limit fits");
        assert.equal(all.items[0]?.id, ids[0]);
      });

      it("continues a scan correctly when records are added between pages", async () => {
        const c = await newConversation();
        const es = Array.from({ length: 4 }, () => entry(id("entry"), c.id));
        await commit({ appendEntries: es });
        const p1 = await s.listEntries(c.id, { limit: 2 });
        const late = entry(id("entry"), c.id);
        await commit({ appendEntries: [late] });
        const rest = await drain((o) => s.listEntries(c.id, o), {
          limit: 2,
          cursor: p1.nextCursor,
        });
        assert.deepEqual(
          [...p1.items, ...rest].map((e) => e.id),
          [...es, late].map((e) => e.id),
        );
      });

      it("pages tasks with conversation and status filters, keeping creation order on replace", async () => {
        const c1 = await newConversation();
        const c2 = await newConversation();
        const ts = [
          task(id("task"), c1.id),
          task(id("task"), c2.id),
          task(id("task"), c1.id, { status: "running" }),
          task(id("task"), c1.id, {
            status: "terminal",
            outcome: { type: "faulted", message: "boom", detail: null },
          }),
          task(id("task"), c2.id, { status: "waiting", waiting: waitSpec() }),
        ];
        await commit({ createTasks: ts.slice(0, 3) });
        await commit({ createTasks: ts.slice(3) });
        // Replacing the first task must not move it to the end.
        await commit({ replaceTasks: [{ ...(ts[0] as TaskRecord), status: "running" }] });

        const all = await drain((o) => s.listTasks(o), { limit: 2 });
        assert.deepEqual(
          all.map((t) => t.id),
          ts.map((t) => t.id),
        );
        const desc = await drain((o) => s.listTasks(o), { limit: 2, order: "desc" });
        assert.deepEqual(
          desc.map((t) => t.id),
          ts.map((t) => t.id).reverse(),
        );
        const inC1 = await drain((o) => s.listTasks({ ...o, conversationId: c1.id }), { limit: 1 });
        assert.deepEqual(
          inC1.map((t) => t.id),
          [ts[0], ts[2], ts[3]].map((t) => t?.id),
        );
        const live = await drain((o) => s.listTasks({ ...o, statuses: ["running", "waiting"] }), {
          limit: 1,
        });
        assert.deepEqual(
          live.map((t) => t.id),
          [ts[0], ts[2], ts[4]].map((t) => t?.id),
        );
        const none = await s.listTasks({ statuses: [] });
        assert.deepEqual(none, { items: [], nextCursor: null });
      });

      it("pages conversations, submissions and documents", async () => {
        const cs = [
          conversation(id("conversation")),
          conversation(id("conversation")),
          conversation(id("conversation")),
        ];
        await commit({ createConversations: cs });
        const conv = await drain((o) => s.listConversations(o), { limit: 2 });
        assert.deepEqual(
          conv.map((c) => c.id),
          cs.map((c) => c.id),
        );

        const c = cs[0] as ConversationRecord;
        const subs = ["a", "b", "c", "d"].map((r) => submission(id("submission"), c.id, r));
        await commit({ createSubmissions: subs });
        await commit({
          replaceSubmissions: [{ ...(subs[1] as SubmissionRecord), status: "done" }],
        });
        const listed = await drain((o) => s.listSubmissions(c.id, o), { limit: 3 });
        assert.deepEqual(
          listed.map((x) => x.requestId),
          ["a", "b", "c", "d"],
        );
        const pending = await drain(
          (o) => s.listSubmissions(c.id, { ...o, statuses: ["pending"] }),
          { limit: 1, order: "desc" },
        );
        assert.deepEqual(
          pending.map((x) => x.requestId),
          ["d", "c", "a"],
        );
        assert.deepEqual(await s.listSubmissions((cs[1] as ConversationRecord).id), {
          items: [],
          nextCursor: null,
        });

        await commit({ putDocuments: [doc("plan", "s1", 1), doc("notes", "s1", 2)] });
        await commit({ putDocuments: [doc("plan", "s2", 3), doc("plan", "s1", 4)] });
        const docs = await drain((o) => s.listDocuments(o), { limit: 2 });
        assert.deepEqual(
          docs.map((d) => `${d.kind}/${d.scopeId}=${String(d.data)}`),
          ["plan/s1=4", "notes/s1=2", "plan/s2=3"],
        );
        const plans = await drain((o) => s.listDocuments({ ...o, kind: "plan" }), { limit: 1 });
        assert.deepEqual(
          plans.map((d) => d.scopeId),
          ["s1", "s2"],
        );
      });

      it("returns an empty page for an empty scan", async () => {
        const c = await newConversation();
        assert.deepEqual(await s.listEntries(c.id), { items: [], nextCursor: null });
        assert.deepEqual(await s.listDocuments(), { items: [], nextCursor: null });
      });
    });

    describe("usage outbox", () => {
      const record = (
        requestId: string,
        o: Partial<UsageOutboxRecord> = {},
      ): UsageOutboxRecord => ({
        requestId,
        body: { requestId, tenantId: "tenant-1" },
        createdAt: 1_000,
        claimedAt: null,
        ...o,
      });

      it("parks a report, hides it inside the claim timeout, and takes it after", async () => {
        const p = fixture.provider;
        const now = Date.now();
        const r = record(`req-${uuidv7()}`, { claimedAt: now });
        await p.enqueue(r);
        // The outbox is one shared table with no per-conversation scope, so a
        // test acks whatever it parks to stay independent of the next one.
        try {
          assert.deepEqual(await p.claimPending(60_000), []);
          const claimed = await p.claimPending(0);
          assert.equal(claimed.length, 1);
          assert.equal(claimed[0]?.requestId, r.requestId);
          assert.deepEqual(claimed[0]?.body, r.body);
          assert.ok((claimed[0]?.claimedAt ?? 0) >= now);
        } finally {
          await p.ack(r.requestId);
        }
      });

      it("is idempotent per requestId and delivers oldest first", async () => {
        const p = fixture.provider;
        const first = record(`req-${uuidv7()}`, { createdAt: 10 });
        const second = record(`req-${uuidv7()}`, { createdAt: 20 });
        await p.enqueue(second);
        await p.enqueue(first);
        await p.enqueue({ ...first, body: { ignored: true } });
        try {
          const claimed = await p.claimPending(0);
          assert.deepEqual(
            claimed.map((r) => r.requestId),
            [first.requestId, second.requestId],
          );
          assert.deepEqual(claimed[0]?.body, first.body);
        } finally {
          await p.ack(first.requestId);
          await p.ack(second.requestId);
        }
      });

      it("reclaims a row past the claim timeout, then ack removes it", async () => {
        const p = fixture.provider;
        const stale = record(`req-${uuidv7()}`, { createdAt: 10, claimedAt: 20 });
        const fresh = record(`req-${uuidv7()}`, { createdAt: 30, claimedAt: Date.now() });
        await p.enqueue(stale);
        await p.enqueue(fresh);
        try {
          const claimed = await p.claimPending(1_000);
          assert.deepEqual(
            claimed.map((r) => r.requestId),
            [stale.requestId],
          );
          await p.ack(stale.requestId);
          await p.ack(stale.requestId); // idempotent
          const remaining = await p.claimPending(0);
          assert.deepEqual(
            remaining.map((r) => r.requestId),
            [fresh.requestId],
          );
        } finally {
          await p.ack(fresh.requestId);
        }
      });
    });

    describe("request errors", () => {
      it("raises StorageRequestError for an unknown conversation", async () => {
        await rejectsRequest(s.listEntries("conv_unknown"));
        await rejectsRequest(s.listSubmissions("conv_unknown"));
        await rejectsRequest(s.submissionByRequestId("conv_unknown", "req"));
        assert.equal(await s.getConversation("conv_unknown"), null);
      });

      it("treats another session's conversation as unknown", async () => {
        const c = await newConversation();
        const other = await open(`sess-${uuidv7()}`);
        await rejectsRequest(other.listEntries(c.id));
        await rejectsRequest(commitIn(other, { appendEntries: [entry(id("entry"), c.id)] }));
      });

      it("raises StorageRequestError for a malformed cursor or bad page options", async () => {
        const c = await newConversation();
        await commit({ appendEntries: [entry(id("entry"), c.id), entry(id("entry"), c.id)] });
        for (const cursor of ["", "garbage", "%%%", "eyJ4IjoxfQ", "0", "-1"]) {
          await rejectsRequest(s.listEntries(c.id, { cursor }));
          await rejectsRequest(s.listTasks({ cursor }));
          await rejectsRequest(s.listConversations({ cursor }));
          await rejectsRequest(s.listDocuments({ cursor }));
          await rejectsRequest(s.listSubmissions(c.id, { cursor }));
        }
        for (const limit of [0, -1, 1.5, Number.NaN, 1_000_000]) {
          await rejectsRequest(s.listEntries(c.id, { limit }));
        }
        await rejectsRequest(s.listEntries(c.id, { order: "sideways" as never }));
        await rejectsRequest(s.listTasks({ statuses: ["bogus" as never] }));
        // A cursor is bound to its order.
        const page = await s.listEntries(c.id, { limit: 1, order: "asc" });
        assert.ok(page.nextCursor);
        await rejectsRequest(s.listEntries(c.id, { order: "desc", cursor: page.nextCursor }));
      });

      it("reports validation failures as non-retryable", async () => {
        const err = await commit({}).then(
          () => undefined,
          (e: unknown) => e,
        );
        assert.ok(err instanceof StorageRequestError);
        assert.equal(err.retryable, false);
      });
    });
  });
}

function must<T>(value: T | null | undefined): T {
  assert.ok(value !== null && value !== undefined, "expected a value");
  return value;
}

async function rejectsRequest(p: Promise<unknown>): Promise<void> {
  await assert.rejects(p, (err: unknown) => {
    assert.ok(
      err instanceof StorageRequestError,
      `expected StorageRequestError, got ${String(err)}`,
    );
    return true;
  });
}

/** Read every page of a scan, asserting that each non-final page is full. */
async function drain<T>(
  scan: (o: PageOptions) => Promise<Page<T>>,
  options: PageOptions,
): Promise<T[]> {
  const out: T[] = [];
  let cursor = options.cursor ?? null;
  for (let guard = 0; guard < 10_000; guard++) {
    const page = await scan({ ...options, cursor });
    assert.ok(page.items.length <= (options.limit ?? Number.POSITIVE_INFINITY));
    out.push(...page.items);
    if (page.nextCursor === null) return out;
    assert.equal(page.items.length, options.limit, "a page with a next cursor is full");
    cursor = page.nextCursor;
  }
  throw new Error("scan did not terminate");
}

function conversation(id: string, o: Partial<ConversationRecord> = {}): ConversationRecord {
  return {
    id,
    tenantId: "tenant-1",
    projectId: "project-1",
    ownerTaskId: null,
    createdAt: 1,
    ...o,
  };
}

function entry(id: string, conversationId: string, o: Partial<EntryRecord> = {}): EntryRecord {
  return {
    id,
    conversationId,
    taskId: null,
    kind: "message",
    payload: { role: "user", text: "hi" },
    createdAt: 1,
    ...o,
  };
}

function task(id: string, conversationId: string, o: Partial<TaskRecord> = {}): TaskRecord {
  return {
    id,
    kind: "generation",
    conversationId,
    parentTaskId: null,
    status: "pending",
    phase: "start",
    checkpoint: null,
    input: { prompt: "x" },
    waiting: null,
    signals: {},
    outcome: null,
    abandonOnRestart: false,
    createdAt: 1,
    updatedAt: 1,
    ...o,
  };
}

function waitSpec(): TaskRecord["waiting"] {
  return { on: [], policy: "allSettled", signal: "approval", until: 3_600_000 };
}

function submission(
  id: string,
  conversationId: string,
  requestId: string,
  o: Partial<SubmissionRecord> = {},
): SubmissionRecord {
  return {
    id,
    conversationId,
    requestId,
    input: { text: "hello", whenBusy: "followUp" },
    status: "pending",
    result: null,
    taskId: null,
    createdAt: 1,
    updatedAt: 1,
    ...o,
  };
}

function doc(kind: string, scopeId: string, data: DocumentRecord["data"], updatedAt = 1) {
  return { kind, scopeId, data, updatedAt } satisfies DocumentRecord;
}
