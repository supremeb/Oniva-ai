/**
 * In-memory storage backend, for unit tests and single-process use.
 *
 * Data lives in the provider, so closing a handle and reopening the same
 * session id reads back everything committed. Values are deep-copied on write
 * and on read. Fences are shape-checked but not enforced: there is no lease.
 */
import { isDeepStrictEqual } from "node:util";
import { type IdKind, newId } from "../core/ids.js";
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
} from "./types.js";
import {
  normalizePage,
  rejectRequest,
  validateBatch,
  validateFence,
  validateScopeIds,
  validateStatuses,
} from "./validate.js";

interface Row<T> {
  rec: T;
  /** Seq of the commit that last wrote the row. */
  seq: number;
  /** Creation position within the session; scans order by it. */
  pos: number;
}

class SessionState {
  seq = 0;
  pos = 0;
  readonly conversations = new Map<string, Row<ConversationRecord>>();
  readonly entries = new Map<string, Row<EntryRecord>>();
  readonly tasks = new Map<string, Row<TaskRecord>>();
  readonly submissions = new Map<string, Row<SubmissionRecord>>();
  /** `[conversationId, requestId]` → submission id. */
  readonly requests = new Map<string, string>();
  /** `[kind, scopeId]` → document row. */
  readonly documents = new Map<string, Row<DocumentRecord>>();
}

const requestKey = (conversationId: string, requestId: string) =>
  JSON.stringify([conversationId, requestId]);
const documentKey = (kind: string, scopeId: string) => JSON.stringify([kind, scopeId]);

export class MemoryStorageProvider implements StorageProvider {
  readonly #sessions = new Map<string, SessionState>();
  /** Global id → session id indexes (conversation and task ids are unique across sessions). */
  readonly #conversationSessions = new Map<string, string>();
  readonly #taskSessions = new Map<string, string>();
  /** Usage reports awaiting delivery, keyed by request id (KTD6). */
  readonly #usageOutbox = new Map<string, UsageOutboxRecord>();

  async open(sessionId: string): Promise<SessionStorage> {
    if (typeof sessionId !== "string" || sessionId.length === 0) {
      rejectRequest("session id must be a non-empty string");
    }
    let state = this.#sessions.get(sessionId);
    if (!state) {
      state = new SessionState();
      this.#sessions.set(sessionId, state);
    }
    return new MemorySessionStorage(sessionId, state, {
      conversations: this.#conversationSessions,
      tasks: this.#taskSessions,
    });
  }

  async sessionOfConversation(conversationId: string): Promise<string | null> {
    return this.#conversationSessions.get(conversationId) ?? null;
  }

  async sessionOfTask(taskId: string): Promise<string | null> {
    return this.#taskSessions.get(taskId) ?? null;
  }

  async enqueue(record: UsageOutboxRecord): Promise<void> {
    if (!this.#usageOutbox.has(record.requestId)) {
      this.#usageOutbox.set(record.requestId, structuredClone(record));
    }
  }

  async claimPending(claimTimeoutMs: number): Promise<UsageOutboxRecord[]> {
    const now = Date.now();
    const due = [...this.#usageOutbox.values()]
      .filter((r) => r.claimedAt === null || r.claimedAt <= now - claimTimeoutMs)
      .sort((a, b) => a.createdAt - b.createdAt || (a.requestId < b.requestId ? -1 : 1));
    return due.map((r) => {
      const claimed: UsageOutboxRecord = { ...r, claimedAt: now };
      this.#usageOutbox.set(r.requestId, claimed);
      return structuredClone(claimed);
    });
  }

  async ack(requestId: string): Promise<void> {
    this.#usageOutbox.delete(requestId);
  }
}

interface GlobalIndexes {
  conversations: Map<string, string>;
  tasks: Map<string, string>;
}

class MemorySessionStorage implements SessionStorage {
  readonly sessionId: string;
  readonly #state: SessionState;
  readonly #global: GlobalIndexes;
  #closed = false;

  constructor(sessionId: string, state: SessionState, global: GlobalIndexes) {
    this.sessionId = sessionId;
    this.#state = state;
    this.#global = global;
  }

  async commit(batch: CommitBatch, fence: Fence | null): Promise<CommitResult> {
    this.#assertOpen();
    validateFence(fence);
    validateBatch(batch);
    this.#checkAgainstState(batch);
    return { seq: this.#apply(batch) };
  }

  async lastSeq(): Promise<number> {
    this.#assertOpen();
    return this.#state.seq;
  }

  mintId(kind: IdKind): string {
    this.#assertOpen();
    return newId(kind);
  }

  async getConversation(id: string) {
    this.#assertOpen();
    return out(this.#state.conversations.get(id));
  }

  async getEntry(id: string) {
    this.#assertOpen();
    return out(this.#state.entries.get(id));
  }

  async getTask(id: string) {
    this.#assertOpen();
    return out(this.#state.tasks.get(id));
  }

  async getSubmission(id: string) {
    this.#assertOpen();
    return out(this.#state.submissions.get(id));
  }

  async submissionByRequestId(conversationId: string, requestId: string) {
    this.#assertOpen();
    this.#assertConversation(conversationId);
    const id = this.#state.requests.get(requestKey(conversationId, requestId));
    return id === undefined ? null : out(this.#state.submissions.get(id));
  }

  async getDocument(kind: string, scopeId: string) {
    this.#assertOpen();
    return out(this.#state.documents.get(documentKey(kind, scopeId)));
  }

  async getDocuments(kind: string, scopeIds: readonly string[]) {
    this.#assertOpen();
    validateScopeIds(scopeIds);
    return scopeIds.map((scopeId) => out(this.#state.documents.get(documentKey(kind, scopeId))));
  }

  async listConversations(options?: PageOptions) {
    this.#assertOpen();
    return scan(this.#state.conversations.values(), options, () => true);
  }

  async listEntries(conversationId: string, options?: PageOptions) {
    this.#assertOpen();
    this.#assertConversation(conversationId);
    return scan(this.#state.entries.values(), options, (e) => e.conversationId === conversationId);
  }

  async listTasks(options: TaskScanOptions = {}) {
    this.#assertOpen();
    validateStatuses(options.statuses, TASK_STATUSES);
    const { conversationId, statuses } = options;
    return scan(
      this.#state.tasks.values(),
      options,
      (t) =>
        (conversationId === undefined || t.conversationId === conversationId) &&
        (statuses === undefined || statuses.includes(t.status)),
    );
  }

  async listSubmissions(conversationId: string, options: SubmissionScanOptions = {}) {
    this.#assertOpen();
    this.#assertConversation(conversationId);
    validateStatuses(options.statuses, SUBMISSION_STATUSES);
    const { statuses } = options;
    return scan(
      this.#state.submissions.values(),
      options,
      (s) =>
        s.conversationId === conversationId &&
        (statuses === undefined || statuses.includes(s.status)),
    );
  }

  async listDocuments(options: DocumentScanOptions = {}) {
    this.#assertOpen();
    const { kind } = options;
    return scan(
      this.#state.documents.values(),
      options,
      (d) => kind === undefined || d.kind === kind,
    );
  }

  async close(): Promise<void> {
    this.#closed = true;
  }

  #assertOpen(): void {
    if (this.#closed) throw new Error(`storage handle for session ${this.sessionId} is closed`);
  }

  #assertConversation(conversationId: string): void {
    if (!this.#state.conversations.has(conversationId)) {
      rejectRequest(`unknown conversation ${conversationId}`);
    }
  }

  /** Uniqueness, references and immutable fields, checked before any write. */
  #checkAgainstState(b: CommitBatch): void {
    const st = this.#state;
    const newConversations = new Set((b.createConversations ?? []).map((c) => c.id));
    const newTasks = new Set((b.createTasks ?? []).map((t) => t.id));
    const conversationKnown = (id: string) => st.conversations.has(id) || newConversations.has(id);
    const taskKnown = (id: string | null) => id === null || st.tasks.has(id) || newTasks.has(id);
    const need = (ok: boolean, message: string) => {
      if (!ok) rejectRequest(message);
    };

    for (const c of b.createConversations ?? []) {
      need(!this.#global.conversations.has(c.id), `conversation ${c.id} already exists`);
      need(taskKnown(c.ownerTaskId), `unknown owner task ${c.ownerTaskId}`);
    }
    for (const e of b.appendEntries ?? []) {
      need(!st.entries.has(e.id), `entry ${e.id} already exists`);
      need(conversationKnown(e.conversationId), `unknown conversation ${e.conversationId}`);
      need(taskKnown(e.taskId), `unknown task ${e.taskId}`);
    }
    for (const t of b.createTasks ?? []) {
      need(!this.#global.tasks.has(t.id), `task ${t.id} already exists`);
      need(conversationKnown(t.conversationId), `unknown conversation ${t.conversationId}`);
      need(taskKnown(t.parentTaskId), `unknown parent task ${t.parentTaskId}`);
    }
    for (const t of b.replaceTasks ?? []) {
      const prev = st.tasks.get(t.id)?.rec;
      if (!prev) rejectRequest(`task ${t.id} does not exist`);
      for (const field of ["kind", "conversationId", "parentTaskId", "createdAt"] as const) {
        need(prev[field] === t[field], `task ${t.id}: ${field} is immutable`);
      }
    }
    for (const s of b.createSubmissions ?? []) {
      need(!st.submissions.has(s.id), `submission ${s.id} already exists`);
      need(conversationKnown(s.conversationId), `unknown conversation ${s.conversationId}`);
      need(
        !st.requests.has(requestKey(s.conversationId, s.requestId)),
        `duplicate requestId ${s.requestId} in conversation ${s.conversationId}`,
      );
      need(taskKnown(s.taskId), `unknown task ${s.taskId}`);
    }
    for (const s of b.replaceSubmissions ?? []) {
      const prev = st.submissions.get(s.id)?.rec;
      if (!prev) rejectRequest(`submission ${s.id} does not exist`);
      for (const field of ["conversationId", "requestId", "input", "createdAt"] as const) {
        need(isDeepStrictEqual(prev[field], s[field]), `submission ${s.id}: ${field} is immutable`);
      }
      need(taskKnown(s.taskId), `unknown task ${s.taskId}`);
    }
  }

  /** Write a validated batch. Cannot fail. */
  #apply(b: CommitBatch): number {
    const st = this.#state;
    const seq = st.seq + 1;
    const fresh = <T>(rec: T): Row<T> => ({ rec: structuredClone(rec), seq, pos: ++st.pos });
    const replace = <T>(prev: Row<T>, rec: T): Row<T> => ({
      rec: structuredClone(rec),
      seq,
      pos: prev.pos,
    });

    for (const c of b.createConversations ?? []) {
      st.conversations.set(c.id, fresh(c));
      this.#global.conversations.set(c.id, this.sessionId);
    }
    for (const t of b.createTasks ?? []) {
      st.tasks.set(t.id, fresh(t));
      this.#global.tasks.set(t.id, this.sessionId);
    }
    for (const t of b.replaceTasks ?? []) {
      st.tasks.set(t.id, replace(st.tasks.get(t.id) as Row<TaskRecord>, t));
    }
    for (const e of b.appendEntries ?? []) st.entries.set(e.id, fresh(e));
    for (const s of b.createSubmissions ?? []) {
      st.submissions.set(s.id, fresh(s));
      st.requests.set(requestKey(s.conversationId, s.requestId), s.id);
    }
    for (const s of b.replaceSubmissions ?? []) {
      st.submissions.set(s.id, replace(st.submissions.get(s.id) as Row<SubmissionRecord>, s));
    }
    for (const d of b.putDocuments ?? []) {
      const k = documentKey(d.kind, d.scopeId);
      const prev = st.documents.get(k);
      st.documents.set(k, prev ? replace(prev, d) : fresh(d));
    }
    st.seq = seq;
    return seq;
  }
}

function out<T>(row: Row<T> | undefined): Stored<T> | null {
  return row ? { ...structuredClone(row.rec), seq: row.seq } : null;
}

const CURSOR_RE = /^m1\.(asc|desc)\.([1-9]\d{0,15})$/;

function scan<T>(
  rows: Iterable<Row<T>>,
  options: PageOptions | undefined,
  match: (rec: T) => boolean,
): Page<Stored<T>> {
  const { order, limit, cursor } = normalizePage(options);
  let after: number | null = null;
  if (cursor !== null) {
    const m = CURSOR_RE.exec(cursor);
    if (!m || m[1] !== order) rejectRequest("malformed cursor");
    after = Number(m[2]);
  }
  const dir = order === "asc" ? 1 : -1;
  const picked = [...rows]
    .filter(
      (r) => match(r.rec) && (after === null || (order === "asc" ? r.pos > after : r.pos < after)),
    )
    .sort((a, b) => dir * (a.pos - b.pos))
    .slice(0, limit + 1);
  const more = picked.length > limit;
  const items = picked.slice(0, limit);
  const last = items.at(-1);
  return {
    items: items.map((r) => out(r) as Stored<T>),
    nextCursor: more && last ? `m1.${order}.${last.pos}` : null,
  };
}
