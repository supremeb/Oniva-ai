/**
 * The storage contract (R3, R4, R15).
 *
 * Storage is scoped per session: a {@link StorageProvider} opens a
 * {@link SessionStorage} handle for one session id, and every read and write
 * on that handle sees only that session's data. Each session has one
 * serialized commit line; {@link SessionStorage.commit} writes a whole
 * {@link CommitBatch} atomically and returns the session's next gap-free
 * `seq`.
 *
 * Records never use optional properties: absent values are `null`, so a
 * record round-trips identically through any backend.
 *
 * Errors:
 * - A duplicate or invalid write, an unknown conversation, a malformed cursor
 *   or bad page options throw `StorageRequestError` (with `retryable: false`)
 *   and leave the session open. Nothing from a rejected batch is stored.
 * - Any other throw is fatal to the open session; recovery is a reopen.
 */
import type { IdKind } from "../core/ids.js";
import type { JsonValue } from "../core/json.js";

/** Lifecycle status of a durable task. */
export type TaskStatus = "pending" | "running" | "waiting" | "completing" | "terminal";

export const TASK_STATUSES: readonly TaskStatus[] = Object.freeze([
  "pending",
  "running",
  "waiting",
  "completing",
  "terminal",
]);

/** How a task waiting on other tasks resumes. */
export type WaitPolicy = "allSettled" | "failFast";

/** What a `waiting` task is waiting for. It resumes when any condition is met. */
export interface WaitSpec {
  /** Task ids to wait on (empty when not waiting on tasks). */
  on: string[];
  /** Policy for `on`. */
  policy: WaitPolicy;
  /** Signal key to wait for, or null. */
  signal: string | null;
  /** Absolute deadline in injected-clock epoch ms, or null. */
  until: number | null;
}

/** Final result of a terminal task. */
export type TaskOutcome =
  | { type: "done"; value: JsonValue }
  | { type: "aborted"; reason: string }
  | { type: "faulted"; message: string; detail: JsonValue };

/** A conversation, bound for its lifetime to one tenant and project. */
export interface ConversationRecord {
  id: string;
  tenantId: string;
  projectId: string;
  /** The tool task that owns this conversation (a subagent), or null. */
  ownerTaskId: string | null;
  createdAt: number;
}

/** An immutable conversation entry (message, tool result, display write, ...). */
export interface EntryRecord {
  id: string;
  conversationId: string;
  /** The task that produced the entry, or null. */
  taskId: string | null;
  kind: string;
  payload: JsonValue;
  createdAt: number;
}

/** A durable task. Writes replace the whole record. */
export interface TaskRecord {
  id: string;
  kind: string;
  /** The conversation this task belongs to. */
  conversationId: string;
  /** The owning task, or null when the conversation owns it directly. */
  parentTaskId: string | null;
  status: TaskStatus;
  /** Current phase name. */
  phase: string;
  /** Checkpoint data for the current phase. */
  checkpoint: JsonValue;
  /** Task input, fixed at creation by convention (not enforced). */
  input: JsonValue;
  /** Non-null exactly when `status` is `waiting`. */
  waiting: WaitSpec | null;
  /** Delivered signal payloads, keyed by signal key. */
  signals: { [key: string]: JsonValue };
  /** Non-null exactly when `status` is `terminal`. */
  outcome: TaskOutcome | null;
  /** Abort this task instead of resuming it after a restart. */
  abandonOnRestart: boolean;
  createdAt: number;
  updatedAt: number;
}

export type SubmissionStatus = "pending" | "active" | "done" | "unanswered";

export const SUBMISSION_STATUSES: readonly SubmissionStatus[] = Object.freeze([
  "pending",
  "active",
  "done",
  "unanswered",
]);

/** An idempotent input to a conversation; `requestId` is unique per conversation. */
export interface SubmissionRecord {
  id: string;
  conversationId: string;
  requestId: string;
  input: JsonValue;
  status: SubmissionStatus;
  /** Result or `unanswered` reason, or null while open. */
  result: JsonValue;
  /** The task handling the submission, or null. */
  taskId: string | null;
  createdAt: number;
  updatedAt: number;
}

/** A typed JSON document snapshot, identified by `(kind, scopeId)`. */
export interface DocumentRecord {
  kind: string;
  scopeId: string;
  data: JsonValue;
  updatedAt: number;
}

/** A record as read back: stamped with the `seq` of the commit that last wrote it. */
export type Stored<T> = T & { readonly seq: number };

/**
 * One atomic write. Every list is optional; at least one item must be present.
 * An id may appear at most once per kind of record in a batch.
 */
export interface CommitBatch {
  /** New conversations (ids are unique across all sessions). */
  createConversations?: ConversationRecord[];
  /** New immutable entries (ids are unique within the session). */
  appendEntries?: EntryRecord[];
  /** New tasks (ids are unique across all sessions). */
  createTasks?: TaskRecord[];
  /**
   * Full replacements of existing tasks. `kind`, `conversationId`,
   * `parentTaskId` and `createdAt` are immutable.
   */
  replaceTasks?: TaskRecord[];
  /** New submissions (`requestId` unique per conversation). */
  createSubmissions?: SubmissionRecord[];
  /**
   * Full replacements of existing submissions. `conversationId`, `requestId`,
   * `input` and `createdAt` are immutable.
   */
  replaceSubmissions?: SubmissionRecord[];
  /** Document snapshots; each replaces any previous snapshot with the same key. */
  putDocuments?: DocumentRecord[];
}

/**
 * Proof of session ownership: the lease fencing token. A backend that
 * enforces leases rejects a commit whose token is not the current one with
 * `LeaseLost` (fatal). Backends without leases may accept `null`.
 */
export interface Fence {
  readonly token: bigint | number;
}

export interface CommitResult {
  /** The commit's per-session sequence number: 1, 2, 3, ... with no gaps. */
  seq: number;
}

export type ScanOrder = "asc" | "desc";

/**
 * Cursor paging. Scans return records in creation order (commit seq, then
 * position in the batch); `desc` reverses it. Replacing a record does not move it.
 */
export interface PageOptions {
  order?: ScanOrder;
  /** 1..{@link MAX_PAGE_LIMIT}; defaults to {@link DEFAULT_PAGE_LIMIT}. */
  limit?: number;
  /** An opaque `nextCursor` from a previous page of the same scan and order. */
  cursor?: string | null;
}

export const DEFAULT_PAGE_LIMIT = 100;
export const MAX_PAGE_LIMIT = 1000;

export interface Page<T> {
  items: T[];
  /** Cursor for the next page, or null when this page is the last. */
  nextCursor: string | null;
}

export interface TaskScanOptions extends PageOptions {
  conversationId?: string;
  statuses?: TaskStatus[];
}

export interface SubmissionScanOptions extends PageOptions {
  statuses?: SubmissionStatus[];
}

export interface DocumentScanOptions extends PageOptions {
  kind?: string;
}

/**
 * Storage for one session. All returned values are copies: mutating them never
 * changes stored state, and mutating a committed batch afterwards has no effect.
 */
export interface SessionStorage {
  readonly sessionId: string;

  /** Atomically write `batch`. Returns the new per-session seq. */
  commit(batch: CommitBatch, fence: Fence | null): Promise<CommitResult>;
  /** Seq of the last commit, or 0 for a session with no commits. */
  lastSeq(): Promise<number>;
  /** Mint a fresh id of the given kind. */
  mintId(kind: IdKind): string;

  getConversation(id: string): Promise<Stored<ConversationRecord> | null>;
  getEntry(id: string): Promise<Stored<EntryRecord> | null>;
  getTask(id: string): Promise<Stored<TaskRecord> | null>;
  getSubmission(id: string): Promise<Stored<SubmissionRecord> | null>;
  /** Throws `StorageRequestError` for an unknown conversation. */
  submissionByRequestId(
    conversationId: string,
    requestId: string,
  ): Promise<Stored<SubmissionRecord> | null>;
  getDocument(kind: string, scopeId: string): Promise<Stored<DocumentRecord> | null>;
  /**
   * The documents of one kind for many scopes, read as one snapshot: element
   * `i` is the document for `scopeIds[i]`, or null. At most
   * {@link MAX_PAGE_LIMIT} ids; more throw `StorageRequestError`.
   */
  getDocuments(
    kind: string,
    scopeIds: readonly string[],
  ): Promise<Array<Stored<DocumentRecord> | null>>;

  listConversations(options?: PageOptions): Promise<Page<Stored<ConversationRecord>>>;
  /** Throws `StorageRequestError` for an unknown conversation. */
  listEntries(conversationId: string, options?: PageOptions): Promise<Page<Stored<EntryRecord>>>;
  listTasks(options?: TaskScanOptions): Promise<Page<Stored<TaskRecord>>>;
  /** Throws `StorageRequestError` for an unknown conversation. */
  listSubmissions(
    conversationId: string,
    options?: SubmissionScanOptions,
  ): Promise<Page<Stored<SubmissionRecord>>>;
  listDocuments(options?: DocumentScanOptions): Promise<Page<Stored<DocumentRecord>>>;

  /** Release the handle. Later calls on it reject. Idempotent. */
  close(): Promise<void>;
}

/** Opens session handles over one shared store. */
export interface StorageProvider extends UsageOutbox {
  /** Open (creating if needed) storage for `sessionId`. */
  open(sessionId: string): Promise<SessionStorage>;
  /** The session holding a conversation, or null. */
  sessionOfConversation(conversationId: string): Promise<string | null>;
  /** The session holding a task, or null. */
  sessionOfTask(taskId: string): Promise<string | null>;
}

/**
 * A usage report parked until the backend ledger acknowledges it (KTD6/U6).
 * The body is the exact JSON POSTed to `/internal/models/usage`.
 */
export interface UsageOutboxRecord {
  /** Unique report id; re-enqueuing the same id changes nothing. */
  requestId: string;
  body: JsonValue;
  /** Epoch ms the row was first enqueued. */
  createdAt: number;
  /** Epoch ms of the current claim, or null when unclaimed. */
  claimedAt: number | null;
}

/**
 * The worker's durable usage outbox (KTD6/U6). It is provider-level, not
 * session-level: writes are unfenced single-row operations outside the session
 * commit line, so they never touch the lease.
 */
export interface UsageOutbox {
  /** Park a report for delivery; idempotent per `requestId`. */
  enqueue(record: UsageOutboxRecord): Promise<void>;
  /**
   * Claim reports that are unclaimed or were claimed longer than
   * `claimTimeoutMs` ago, oldest first, and stamp them claimed now.
   */
  claimPending(claimTimeoutMs: number): Promise<UsageOutboxRecord[]>;
  /** Delete a delivered report. A no-op when the row is absent. */
  ack(requestId: string): Promise<void>;
}
