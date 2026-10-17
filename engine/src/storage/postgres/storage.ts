/**
 * Postgres storage backend (R15, R16, KTD2, KTD3, KTD5).
 *
 * Records are stored whole as `json` next to the columns needed for lookups
 * and scans (see `migrations/001_init.sql`). Each session has a counter row in
 * `engine_sessions` holding the last commit seq and the next creation position.
 *
 * A commit is one transaction:
 * 1. lock the session's lease and counter rows in one `SELECT ... FOR UPDATE`
 *    and compare the fence token (mismatch, expiry or no lease: `LeaseLost`,
 *    nothing written);
 * 2. check the batch against stored state;
 * 3. write every record, then bump the counters and insert the
 *    `engine_commits` row in one statement.
 *
 * A document-only commit is five round trips: BEGIN, lock, upsert, bump,
 * COMMIT.
 *
 * A rejected batch rolls back, so it consumes no seq and leaves no rows. Unique
 * violations from a cross-session race map to `StorageRequestError`; every
 * other database or connection error is rethrown as is (fatal to the session).
 *
 * The provider takes a `pg.Pool`; set the pool's `search_path` to the schema
 * the engine's migrations were applied to.
 */
import { isDeepStrictEqual } from "node:util";
import pg, { type Pool, type PoolClient } from "pg";
import { LeaseLost, StorageRequestError } from "../../core/errors.js";
import { type IdKind, newId } from "../../core/ids.js";
import type { JsonValue } from "../../core/json.js";
import {
  type CommitBatch,
  type CommitResult,
  type ConversationRecord,
  type DocumentRecord,
  type DocumentScanOptions,
  type EntryRecord,
  type Fence,
  type Page,
  type PageOptions,
  type SessionStorage,
  type StorageProvider,
  type Stored,
  SUBMISSION_STATUSES,
  type SubmissionRecord,
  type SubmissionScanOptions,
  TASK_STATUSES,
  type TaskRecord,
  type TaskScanOptions,
  type UsageOutboxRecord,
} from "../types.js";
import {
  normalizePage,
  rejectRequest,
  validateBatch,
  validateFence,
  validateScopeIds,
  validateStatuses,
} from "../validate.js";

export class PostgresStorageProvider implements StorageProvider {
  constructor(readonly pool: Pool) {}

  async open(sessionId: string): Promise<SessionStorage> {
    if (typeof sessionId !== "string" || sessionId.length === 0) {
      rejectRequest("session id must be a non-empty string");
    }
    await this.pool.query(
      "INSERT INTO engine_sessions (session_id) VALUES ($1) ON CONFLICT DO NOTHING",
      [sessionId],
    );
    return new PostgresSessionStorage(this.pool, sessionId);
  }

  async sessionOfConversation(conversationId: string): Promise<string | null> {
    const { rows } = await this.pool.query<{ session_id: string }>(
      "SELECT session_id FROM engine_conversations WHERE id = $1",
      [conversationId],
    );
    return rows[0]?.session_id ?? null;
  }

  async sessionOfTask(taskId: string): Promise<string | null> {
    const { rows } = await this.pool.query<{ session_id: string }>(
      "SELECT session_id FROM engine_tasks WHERE id = $1",
      [taskId],
    );
    return rows[0]?.session_id ?? null;
  }

  /**
   * Park a usage report (KTD6). One unfenced row, outside the session commit
   * line; a repeated `requestId` is a no-op. `created_at`/`claimed_at` are
   * epoch ms, so the memory and Postgres backends agree exactly.
   */
  async enqueue(record: UsageOutboxRecord): Promise<void> {
    await this.pool.query(
      `INSERT INTO engine_usage_outbox (request_id, body, created_at, claimed_at)
       VALUES ($1, $2::json, $3, $4)
       ON CONFLICT (request_id) DO NOTHING`,
      [record.requestId, JSON.stringify(record.body), record.createdAt, record.claimedAt],
    );
  }

  /** Claim rows unclaimed or claimed at most `claimTimeoutMs` ago, oldest first. */
  async claimPending(claimTimeoutMs: number): Promise<UsageOutboxRecord[]> {
    const now = Date.now();
    const { rows } = await this.pool.query<{
      request_id: string;
      body: JsonValue;
      created_at: string;
      claimed_at: string | null;
    }>(
      `WITH claimed AS (
         UPDATE engine_usage_outbox SET claimed_at = $1
          WHERE request_id IN (
            SELECT request_id FROM engine_usage_outbox
             WHERE claimed_at IS NULL OR claimed_at <= $2
             ORDER BY created_at, request_id
          )
         RETURNING request_id, body, created_at, claimed_at
       )
       SELECT request_id, body, created_at, claimed_at FROM claimed
        ORDER BY created_at, request_id`,
      [now, now - claimTimeoutMs],
    );
    return rows.map((r) => ({
      requestId: r.request_id,
      body: r.body,
      createdAt: Number(r.created_at),
      claimedAt: r.claimed_at === null ? null : Number(r.claimed_at),
    }));
  }

  /** Delete a delivered report. */
  async ack(requestId: string): Promise<void> {
    await this.pool.query("DELETE FROM engine_usage_outbox WHERE request_id = $1", [requestId]);
  }
}

type Table =
  | "engine_conversations"
  | "engine_entries"
  | "engine_tasks"
  | "engine_submissions"
  | "engine_documents";

interface RecordRow {
  record: unknown;
  seq: string;
  pos: string;
}

const CURSOR_RE = /^p1\.(asc|desc)\.([1-9]\d{0,15})$/;
const UNIQUE_VIOLATION = "23505";
const ignoreClientError = (): void => {};

class PostgresSessionStorage implements SessionStorage {
  readonly sessionId: string;
  readonly #pool: Pool;
  #closed = false;

  constructor(pool: Pool, sessionId: string) {
    this.#pool = pool;
    this.sessionId = sessionId;
  }

  async commit(batch: CommitBatch, fence: Fence | null): Promise<CommitResult> {
    this.#assertOpen();
    validateFence(fence);
    validateBatch(batch);
    // Snapshot before the first await: later mutation of `batch` has no effect.
    const b = structuredClone(batch);
    if (fence === null) throw new LeaseLost(this.sessionId, 0);
    const token = Number(fence.token);

    const client = await this.#pool.connect();
    client.on("error", ignoreClientError); // surfaced through the failing query instead
    let broken: Error | undefined;
    try {
      await client.query("BEGIN");
      try {
        const seq = await this.#commitIn(client, b, token);
        await client.query("COMMIT");
        return { seq };
      } catch (err) {
        await client.query("ROLLBACK").catch((e: unknown) => {
          broken = e instanceof Error ? e : new Error(String(e));
        });
        if (err instanceof pg.DatabaseError && err.code === UNIQUE_VIOLATION) {
          throw new StorageRequestError(`conflicting write: ${err.detail ?? err.message}`, {
            retryable: false,
            cause: err,
          });
        }
        throw err;
      }
    } finally {
      client.off("error", ignoreClientError);
      client.release(broken);
    }
  }

  async #commitIn(client: PoolClient, b: CommitBatch, token: number): Promise<number> {
    const sid = this.sessionId;
    const row = await this.#lockLeaseAndCounters(client, token);
    await this.#checkAgainstState(client, b);

    const seq = Number(row.last_seq) + 1;
    let pos = Number(row.next_pos);
    const json = (v: unknown) => JSON.stringify(v);

    const convs = b.createConversations ?? [];
    if (convs.length > 0) {
      await client.query(
        `INSERT INTO engine_conversations (id, session_id, pos, seq, record)
         SELECT x.id, $1, x.pos, $2, x.rec::json
           FROM unnest($3::text[], $4::bigint[], $5::text[]) AS x(id, pos, rec)`,
        [sid, seq, convs.map((c) => c.id), convs.map(() => ++pos), convs.map(json)],
      );
    }
    const tasks = b.createTasks ?? [];
    if (tasks.length > 0) {
      await client.query(
        `INSERT INTO engine_tasks (id, session_id, conversation_id, status, pos, seq, record)
         SELECT x.id, $1, x.cid, x.status, x.pos, $2, x.rec::json
           FROM unnest($3::text[], $4::text[], $5::text[], $6::bigint[], $7::text[])
             AS x(id, cid, status, pos, rec)`,
        [
          sid,
          seq,
          tasks.map((t) => t.id),
          tasks.map((t) => t.conversationId),
          tasks.map((t) => t.status),
          tasks.map(() => ++pos),
          tasks.map(json),
        ],
      );
    }
    const replacedTasks = b.replaceTasks ?? [];
    if (replacedTasks.length > 0) {
      await client.query(
        `UPDATE engine_tasks t SET status = x.status, seq = $2, record = x.rec::json
           FROM unnest($3::text[], $4::text[], $5::text[]) AS x(id, status, rec)
          WHERE t.session_id = $1 AND t.id = x.id`,
        [
          sid,
          seq,
          replacedTasks.map((t) => t.id),
          replacedTasks.map((t) => t.status),
          replacedTasks.map(json),
        ],
      );
    }
    const entries = b.appendEntries ?? [];
    if (entries.length > 0) {
      await client.query(
        `INSERT INTO engine_entries (session_id, id, conversation_id, pos, seq, record)
         SELECT $1, x.id, x.cid, x.pos, $2, x.rec::json
           FROM unnest($3::text[], $4::text[], $5::bigint[], $6::text[]) AS x(id, cid, pos, rec)`,
        [
          sid,
          seq,
          entries.map((e) => e.id),
          entries.map((e) => e.conversationId),
          entries.map(() => ++pos),
          entries.map(json),
        ],
      );
    }
    const subs = b.createSubmissions ?? [];
    if (subs.length > 0) {
      await client.query(
        `INSERT INTO engine_submissions
           (session_id, id, conversation_id, request_id, status, pos, seq, record)
         SELECT $1, x.id, x.cid, x.rid, x.status, x.pos, $2, x.rec::json
           FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::bigint[], $8::text[])
             AS x(id, cid, rid, status, pos, rec)`,
        [
          sid,
          seq,
          subs.map((s) => s.id),
          subs.map((s) => s.conversationId),
          subs.map((s) => s.requestId),
          subs.map((s) => s.status),
          subs.map(() => ++pos),
          subs.map(json),
        ],
      );
    }
    const replacedSubs = b.replaceSubmissions ?? [];
    if (replacedSubs.length > 0) {
      await client.query(
        `UPDATE engine_submissions s SET status = x.status, seq = $2, record = x.rec::json
           FROM unnest($3::text[], $4::text[], $5::text[]) AS x(id, status, rec)
          WHERE s.session_id = $1 AND s.id = x.id`,
        [
          sid,
          seq,
          replacedSubs.map((s) => s.id),
          replacedSubs.map((s) => s.status),
          replacedSubs.map(json),
        ],
      );
    }
    const docs = b.putDocuments ?? [];
    if (docs.length > 0) {
      // A replaced document keeps its original position.
      await client.query(
        `INSERT INTO engine_documents (session_id, kind, scope_id, pos, seq, record)
         SELECT $1, x.kind, x.scope, x.pos, $2, x.rec::json
           FROM unnest($3::text[], $4::text[], $5::bigint[], $6::text[]) AS x(kind, scope, pos, rec)
         ON CONFLICT (session_id, kind, scope_id)
           DO UPDATE SET seq = EXCLUDED.seq, record = EXCLUDED.record`,
        [
          sid,
          seq,
          docs.map((d) => d.kind),
          docs.map((d) => d.scopeId),
          docs.map(() => ++pos),
          docs.map(json),
        ],
      );
    }

    const count =
      convs.length +
      tasks.length +
      replacedTasks.length +
      entries.length +
      subs.length +
      replacedSubs.length +
      docs.length;
    // Bump the counters and record the commit in one statement.
    const bumped = await client.query(
      `WITH bump AS (
         UPDATE engine_sessions SET last_seq = $2, next_pos = $3
          WHERE session_id = $1 RETURNING session_id
       )
       INSERT INTO engine_commits (session_id, seq, fence_token, record_count)
       SELECT session_id, $2, $4, $5 FROM bump`,
      [sid, seq, pos, token, count],
    );
    if (bumped.rowCount !== 1) throw new Error(`session row for ${sid} vanished`);
    return seq;
  }

  /**
   * Lock the session's lease and counter rows in one statement and check the
   * fence. Mismatch, expiry or no lease: `LeaseLost`, nothing written.
   */
  async #lockLeaseAndCounters(
    client: PoolClient,
    token: number,
  ): Promise<{ last_seq: string; next_pos: string }> {
    const sid = this.sessionId;
    type Row = { token: string; live: boolean; last_seq: string; next_pos: string };
    const lock = () =>
      client.query<Row>(
        `SELECT l.token, l.expires_at > clock_timestamp() AS live, s.last_seq, s.next_pos
           FROM engine_session_leases l
           JOIN engine_sessions s ON s.session_id = l.session_id
          WHERE l.session_id = $1
            FOR UPDATE`,
        [sid],
      );
    let held = (await lock()).rows[0];
    if (!held) {
      // No lease, or (never on a handle from `open`) no counter row: create
      // the counter row if missing and look again.
      const created = await client.query(
        "INSERT INTO engine_sessions (session_id) VALUES ($1) ON CONFLICT DO NOTHING",
        [sid],
      );
      if (created.rowCount) held = (await lock()).rows[0];
    }
    if (!held || Number(held.token) !== token || !held.live) throw new LeaseLost(sid, token);
    return held;
  }

  /** Uniqueness, references and immutable fields against stored state (inside the commit). */
  async #checkAgainstState(client: PoolClient, b: CommitBatch): Promise<void> {
    const sid = this.sessionId;
    const need = (ok: boolean, message: string) => {
      if (!ok) rejectRequest(message);
    };
    const newConvs = new Set((b.createConversations ?? []).map((c) => c.id));
    const newTasks = new Set((b.createTasks ?? []).map((t) => t.id));

    // Conversations: global ids for creates, this session's for references.
    const convIdsGlobal = await ids(
      client,
      "SELECT id FROM engine_conversations WHERE id = ANY($1::text[])",
      [...newConvs],
    );
    const referencedConvs = new Set<string>();
    for (const r of [
      ...(b.appendEntries ?? []),
      ...(b.createTasks ?? []),
      ...(b.createSubmissions ?? []),
    ]) {
      if (!newConvs.has(r.conversationId)) referencedConvs.add(r.conversationId);
    }
    const convIdsHere = await ids(
      client,
      "SELECT id FROM engine_conversations WHERE session_id = $2 AND id = ANY($1::text[])",
      [...referencedConvs],
      [sid],
    );
    const conversationKnown = (id: string) => newConvs.has(id) || convIdsHere.has(id);

    // Tasks: global ids for creates; this session's for references and replacements.
    const taskIdsGlobal = await ids(
      client,
      "SELECT id FROM engine_tasks WHERE id = ANY($1::text[])",
      [...newTasks],
    );
    const replaceTaskIds = (b.replaceTasks ?? []).map((t) => t.id);
    const referencedTasks = new Set<string>(replaceTaskIds);
    const addTaskRef = (id: string | null) => {
      if (id !== null && !newTasks.has(id)) referencedTasks.add(id);
    };
    for (const c of b.createConversations ?? []) addTaskRef(c.ownerTaskId);
    for (const e of b.appendEntries ?? []) addTaskRef(e.taskId);
    for (const t of b.createTasks ?? []) addTaskRef(t.parentTaskId);
    for (const s of [...(b.createSubmissions ?? []), ...(b.replaceSubmissions ?? [])]) {
      addTaskRef(s.taskId);
    }
    const tasksHere = await records<TaskRecord>(
      client,
      "engine_tasks",
      sid,
      [...referencedTasks],
      replaceTaskIds,
    );
    const taskKnown = (id: string | null) => id === null || newTasks.has(id) || tasksHere.has(id);

    for (const c of b.createConversations ?? []) {
      need(!convIdsGlobal.has(c.id), `conversation ${c.id} already exists`);
      need(taskKnown(c.ownerTaskId), `unknown owner task ${c.ownerTaskId}`);
    }

    const entriesHere = await ids(
      client,
      "SELECT id FROM engine_entries WHERE session_id = $2 AND id = ANY($1::text[])",
      (b.appendEntries ?? []).map((e) => e.id),
      [sid],
    );
    for (const e of b.appendEntries ?? []) {
      need(!entriesHere.has(e.id), `entry ${e.id} already exists`);
      need(conversationKnown(e.conversationId), `unknown conversation ${e.conversationId}`);
      need(taskKnown(e.taskId), `unknown task ${e.taskId}`);
    }

    for (const t of b.createTasks ?? []) {
      need(!taskIdsGlobal.has(t.id), `task ${t.id} already exists`);
      need(conversationKnown(t.conversationId), `unknown conversation ${t.conversationId}`);
      need(taskKnown(t.parentTaskId), `unknown parent task ${t.parentTaskId}`);
    }
    for (const t of b.replaceTasks ?? []) {
      const prev = tasksHere.get(t.id);
      if (!prev) rejectRequest(`task ${t.id} does not exist`);
      for (const field of ["kind", "conversationId", "parentTaskId", "createdAt"] as const) {
        need(prev[field] === t[field], `task ${t.id}: ${field} is immutable`);
      }
    }

    const createSubs = b.createSubmissions ?? [];
    const replaceSubs = b.replaceSubmissions ?? [];
    const subsHere = await records<SubmissionRecord>(
      client,
      "engine_submissions",
      sid,
      [...createSubs, ...replaceSubs].map((s) => s.id),
      replaceSubs.map((s) => s.id),
    );
    const takenRequests = new Set<string>();
    if (createSubs.length > 0) {
      const { rows } = await client.query<{ conversation_id: string; request_id: string }>(
        `SELECT conversation_id, request_id FROM engine_submissions
          WHERE session_id = $1
            AND (conversation_id, request_id) IN
                (SELECT * FROM unnest($2::text[], $3::text[]))`,
        [sid, createSubs.map((s) => s.conversationId), createSubs.map((s) => s.requestId)],
      );
      for (const r of rows) takenRequests.add(JSON.stringify([r.conversation_id, r.request_id]));
    }
    for (const s of createSubs) {
      need(!subsHere.has(s.id), `submission ${s.id} already exists`);
      need(conversationKnown(s.conversationId), `unknown conversation ${s.conversationId}`);
      need(
        !takenRequests.has(JSON.stringify([s.conversationId, s.requestId])),
        `duplicate requestId ${s.requestId} in conversation ${s.conversationId}`,
      );
      need(taskKnown(s.taskId), `unknown task ${s.taskId}`);
    }
    for (const s of replaceSubs) {
      const prev = subsHere.get(s.id);
      if (!prev) rejectRequest(`submission ${s.id} does not exist`);
      for (const field of ["conversationId", "requestId", "input", "createdAt"] as const) {
        need(isDeepStrictEqual(prev[field], s[field]), `submission ${s.id}: ${field} is immutable`);
      }
      need(taskKnown(s.taskId), `unknown task ${s.taskId}`);
    }
  }

  async lastSeq(): Promise<number> {
    this.#assertOpen();
    const { rows } = await this.#pool.query<{ last_seq: string }>(
      "SELECT last_seq FROM engine_sessions WHERE session_id = $1",
      [this.sessionId],
    );
    return rows[0] ? Number(rows[0].last_seq) : 0;
  }

  mintId(kind: IdKind): string {
    this.#assertOpen();
    return newId(kind);
  }

  getConversation(id: string) {
    return this.#getOne<ConversationRecord>("engine_conversations", "id = $2", [id]);
  }

  getEntry(id: string) {
    return this.#getOne<EntryRecord>("engine_entries", "id = $2", [id]);
  }

  getTask(id: string) {
    return this.#getOne<TaskRecord>("engine_tasks", "id = $2", [id]);
  }

  getSubmission(id: string) {
    return this.#getOne<SubmissionRecord>("engine_submissions", "id = $2", [id]);
  }

  async submissionByRequestId(conversationId: string, requestId: string) {
    this.#assertOpen();
    await this.#assertConversation(conversationId);
    return this.#getOne<SubmissionRecord>(
      "engine_submissions",
      "conversation_id = $2 AND request_id = $3",
      [conversationId, requestId],
    );
  }

  getDocument(kind: string, scopeId: string) {
    return this.#getOne<DocumentRecord>("engine_documents", "kind = $2 AND scope_id = $3", [
      kind,
      scopeId,
    ]);
  }

  async getDocuments(kind: string, scopeIds: readonly string[]) {
    this.#assertOpen();
    validateScopeIds(scopeIds);
    if (scopeIds.length === 0) return [];
    const { rows } = await this.#pool.query<RecordRow & { scope_id: string }>(
      `SELECT scope_id, record, seq FROM engine_documents
        WHERE session_id = $1 AND kind = $2 AND scope_id = ANY($3::text[])`,
      [this.sessionId, kind, [...new Set(scopeIds)]],
    );
    const found = new Map(rows.map((r) => [r.scope_id, r]));
    return scopeIds.map((scopeId) => {
      const row = found.get(scopeId);
      return row ? stored<DocumentRecord>(row) : null;
    });
  }

  listConversations(options?: PageOptions) {
    return this.#scan<ConversationRecord>("engine_conversations", options, [], []);
  }

  async listEntries(conversationId: string, options?: PageOptions) {
    this.#assertOpen();
    normalizePage(options);
    await this.#assertConversation(conversationId);
    return this.#scan<EntryRecord>(
      "engine_entries",
      options,
      ["conversation_id = $?"],
      [conversationId],
    );
  }

  async listTasks(options: TaskScanOptions = {}) {
    this.#assertOpen();
    validateStatuses(options.statuses, TASK_STATUSES);
    const where: string[] = [];
    const params: unknown[] = [];
    if (options.conversationId !== undefined) {
      where.push("conversation_id = $?");
      params.push(options.conversationId);
    }
    if (options.statuses !== undefined) {
      where.push("status = ANY($?::text[])");
      params.push(options.statuses);
    }
    return this.#scan<TaskRecord>("engine_tasks", options, where, params);
  }

  async listSubmissions(conversationId: string, options: SubmissionScanOptions = {}) {
    this.#assertOpen();
    validateStatuses(options.statuses, SUBMISSION_STATUSES);
    normalizePage(options);
    await this.#assertConversation(conversationId);
    const where = ["conversation_id = $?"];
    const params: unknown[] = [conversationId];
    if (options.statuses !== undefined) {
      where.push("status = ANY($?::text[])");
      params.push(options.statuses);
    }
    return this.#scan<SubmissionRecord>("engine_submissions", options, where, params);
  }

  listDocuments(options: DocumentScanOptions = {}) {
    const where: string[] = [];
    const params: unknown[] = [];
    if (options.kind !== undefined) {
      where.push("kind = $?");
      params.push(options.kind);
    }
    return this.#scan<DocumentRecord>("engine_documents", options, where, params);
  }

  async close(): Promise<void> {
    this.#closed = true;
  }

  #assertOpen(): void {
    if (this.#closed) throw new Error(`storage handle for session ${this.sessionId} is closed`);
  }

  async #assertConversation(conversationId: string): Promise<void> {
    const { rowCount } = await this.#pool.query(
      "SELECT 1 FROM engine_conversations WHERE session_id = $1 AND id = $2",
      [this.sessionId, conversationId],
    );
    if (!rowCount) rejectRequest(`unknown conversation ${conversationId}`);
  }

  async #getOne<T>(table: Table, where: string, params: unknown[]): Promise<Stored<T> | null> {
    this.#assertOpen();
    const { rows } = await this.#pool.query<RecordRow>(
      `SELECT record, seq FROM ${table} WHERE session_id = $1 AND ${where}`,
      [this.sessionId, ...params],
    );
    return rows[0] ? stored<T>(rows[0]) : null;
  }

  /**
   * Page through `table` in creation order. `where` clauses use `$?`
   * placeholders, numbered here in order after the session id.
   */
  async #scan<T>(
    table: Table,
    options: PageOptions | undefined,
    where: string[],
    params: unknown[],
  ): Promise<Page<Stored<T>>> {
    this.#assertOpen();
    const { order, limit, cursor } = normalizePage(options);
    const all: unknown[] = [this.sessionId, ...params];
    let n = 1;
    const clauses = ["session_id = $1", ...where.map((w) => w.replace("$?", () => `$${++n}`))];
    if (cursor !== null) {
      const m = CURSOR_RE.exec(cursor);
      if (!m || m[1] !== order) rejectRequest("malformed cursor");
      all.push(Number(m[2]));
      clauses.push(`pos ${order === "asc" ? ">" : "<"} $${++n}`);
    }
    all.push(limit + 1);
    const { rows } = await this.#pool.query<RecordRow>(
      `SELECT record, seq, pos FROM ${table}
        WHERE ${clauses.join(" AND ")}
        ORDER BY pos ${order === "asc" ? "ASC" : "DESC"}
        LIMIT $${++n}`,
      all,
    );
    const more = rows.length > limit;
    const items = rows.slice(0, limit);
    const last = items.at(-1);
    return {
      items: items.map((r) => stored<T>(r)),
      nextCursor: more && last ? `p1.${order}.${last.pos}` : null,
    };
  }
}

function stored<T>(row: { record: unknown; seq: string }): Stored<T> {
  return { ...(row.record as T), seq: Number(row.seq) };
}

/** Run an id query over a non-empty id list; returns the ids found. */
async function ids(
  client: PoolClient,
  sql: string,
  list: string[],
  extra: unknown[] = [],
): Promise<Set<string>> {
  if (list.length === 0) return new Set();
  const { rows } = await client.query<{ id: string }>(sql, [list, ...extra]);
  return new Set(rows.map((r) => r.id));
}

/**
 * Which of `list` exist in this session; the full record for those also in
 * `withRecord` (null otherwise, to avoid shipping unneeded payloads).
 */
async function records<T>(
  client: PoolClient,
  table: "engine_tasks" | "engine_submissions",
  sessionId: string,
  list: string[],
  withRecord: string[],
): Promise<Map<string, T | null>> {
  const out = new Map<string, T | null>();
  if (list.length === 0) return out;
  const { rows } = await client.query<{ id: string; record: JsonValue | null }>(
    `SELECT id, CASE WHEN id = ANY($3::text[]) THEN record END AS record
       FROM ${table} WHERE session_id = $1 AND id = ANY($2::text[])`,
    [sessionId, list, withRecord],
  );
  for (const r of rows) out.set(r.id, r.record as T | null);
  return out;
}
