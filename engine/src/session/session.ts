/**
 * A durable session: one serialized commit line over one session's storage
 * (R3, R4, R20).
 *
 * `session.commit(async (tx) => …)` waits its turn on the line, runs the
 * callback against a fresh {@link Transaction}, turns the buffered writes and
 * changed document drafts into one batch (redacted, validated and
 * size-checked), stores it, adopts it, and only then notifies observers. Any
 * throw before storage accepts the batch rolls the whole commit back.
 *
 * Storage errors: a `StorageRequestError` rejects only the failing call. Any
 * other storage throw (or a throwing fence provider) fails the session: that
 * call rejects with the original error, queued and later calls reject with
 * `SessionFailed`, `signal` aborts, and `closed` resolves `{ reason: "failed" }`.
 * Recovery is opening a new session over a fresh storage handle.
 *
 * Commits must not be nested: calling `commit` from inside a commit callback
 * deadlocks the line.
 */
import { isDeepStrictEqual } from "node:util";
import { type Clock, realClock } from "../core/clock.js";
import { SessionFailed, StorageRequestError } from "../core/errors.js";
import { assertJson, type JsonValue, jsonByteLength } from "../core/json.js";
import { type EngineLimits, enforceLimit, resolveLimits } from "../core/limits.js";
import type { CommitBatch, DocumentRecord, Fence, SessionStorage } from "../storage/types.js";
import { type DocDefinition, docKey, parseDoc } from "./documents.js";
import {
  type CommitEvent,
  type CommitObserver,
  type DocumentChange,
  Publisher,
} from "./publish.js";
import {
  type Redact,
  type SessionReader,
  Transaction,
  type TransactionWrites,
  wrapReader,
} from "./transaction.js";

export type { Redact, RedactTarget } from "./transaction.js";

/**
 * Supplies the fence sent with every storage commit (the lease token, U4).
 * Called once per commit, right before the batch is stored. A throw is fatal.
 */
export type FenceProvider = () => Fence | null | Promise<Fence | null>;

export interface SessionOptions {
  /** Injected clock for record timestamps. Defaults to the real clock. */
  clock?: Clock;
  /** Overrides for the engine limits; entry and document sizes are enforced here. */
  limits?: Partial<EngineLimits>;
  /** Host redaction hook, applied to every stored payload. */
  redact?: Redact;
  /** Fence for storage commits. Defaults to `null` (no lease). */
  fence?: FenceProvider;
  /** Receives errors thrown by commit observers. Defaults to ignoring them. */
  onObserverError?: (error: unknown) => void;
}

export type SessionState = "open" | "closing" | "closed" | "failed";

export type SessionEnd = { reason: "closed" } | { reason: "failed"; error: unknown };

interface PreparedCommit {
  batch: CommitBatch;
  documents: DocumentChange[];
  empty: boolean;
}

export class Session {
  readonly sessionId: string;
  /** Resolves once the session has ended, saying why. Never rejects. */
  readonly closed: Promise<SessionEnd>;
  /** Committed-state reads. Reject with `SessionFailed` once the session has ended. */
  readonly reader: SessionReader;

  readonly #storage: SessionStorage;
  readonly #clock: Clock;
  readonly #limits: Readonly<EngineLimits>;
  readonly #redact: Redact | null;
  readonly #fence: FenceProvider | null;
  readonly #publisher: Publisher;
  readonly #abort = new AbortController();
  readonly #resolveClosed: (end: SessionEnd) => void;
  /** Committed document values by `docKey`; `null` means "never stored". */
  readonly #docs = new Map<string, JsonValue | null>();
  #state: SessionState = "open";
  #failure: unknown = null;
  #lastSeq: number;
  #tail: Promise<void> = Promise.resolve();
  #closing: Promise<void> | null = null;

  private constructor(storage: SessionStorage, lastSeq: number, options: SessionOptions) {
    this.sessionId = storage.sessionId;
    this.#storage = storage;
    this.#lastSeq = lastSeq;
    this.#clock = options.clock ?? realClock;
    this.#limits = resolveLimits(options.limits);
    this.#redact = options.redact ?? null;
    this.#fence = options.fence ?? null;
    this.#publisher = new Publisher(options.onObserverError ?? (() => {}));
    let resolve!: (end: SessionEnd) => void;
    this.closed = new Promise<SessionEnd>((r) => {
      resolve = r;
    });
    this.#resolveClosed = resolve;
    this.reader = wrapReader(storage, (call) => this.#guard(call));
  }

  /** Open a session over a storage handle. The session owns the handle and closes it. */
  static async open(storage: SessionStorage, options: SessionOptions = {}): Promise<Session> {
    return new Session(storage, await storage.lastSeq(), options);
  }

  get state(): SessionState {
    return this.#state;
  }

  /** Seq of the last adopted commit (0 for a session with no commits). */
  get lastSeq(): number {
    return this.#lastSeq;
  }

  /** Aborts when the session ends (closed or failed). Tie in-flight work to it. */
  get signal(): AbortSignal {
    return this.#abort.signal;
  }

  /** Run `fn` on the commit line and store its writes atomically. Resolves to `fn`'s value. */
  commit<T>(fn: (tx: Transaction) => T | Promise<T>): Promise<T> {
    if (this.#state !== "open") return Promise.reject(this.#unavailable());
    const run = this.#tail.then(() => this.#run(fn));
    this.#tail = run.then(
      () => {},
      () => {},
    );
    return run;
  }

  /** Observe adopted commits, in order. Returns an unsubscribe function. */
  subscribeCommits(observer: CommitObserver): () => void {
    return this.#publisher.subscribe(observer);
  }

  /** The committed value of a document (or its initial value). A private copy. */
  async document<T extends JsonValue>(def: DocDefinition<T>, scopeId: string): Promise<T> {
    if (this.#state === "failed" || this.#state === "closed") throw this.#unavailable();
    return (await this.#committedDoc(def, scopeId)) ?? structuredClone(def.initial);
  }

  /** Fail the session from outside (for example when the lease is lost). Idempotent. */
  fail(error: unknown): void {
    if (this.#state === "failed" || this.#state === "closed") return;
    this.#state = "failed";
    this.#failure = error;
    this.#end({ reason: "failed", error });
  }

  /** Stop accepting commits, let queued ones finish, then release storage. Idempotent. */
  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    if (this.#state !== "open") return this.closed.then(() => {});
    this.#state = "closing";
    this.#closing = this.#tail.then(() => {
      if (this.#state !== "closing") return; // failed while draining
      this.#state = "closed";
      this.#end({ reason: "closed" });
    });
    this.#closing = this.#closing.then(() => this.closed).then(() => {});
    return this.#closing;
  }

  // ---- commit pipeline ----------------------------------------------------

  async #run<T>(fn: (tx: Transaction) => T | Promise<T>): Promise<T> {
    if (this.#state === "failed" || this.#state === "closed") throw this.#unavailable();
    const now = this.#clock.now();
    const tx = new Transaction({
      reader: this.reader,
      now,
      redact: this.#redact,
      mintId: (kind) => this.#storage.mintId(kind),
      committedDoc: (def, scopeId) => this.#committedDoc(def, scopeId),
    });
    let value: T;
    let writes: TransactionWrites;
    try {
      value = await fn(tx);
    } finally {
      writes = tx.settle();
    }
    const prepared = this.#prepare(writes, now);
    if (prepared.empty) return value;
    // The session may have been failed from outside while the callback ran.
    if ((this.#state as SessionState) === "failed") throw this.#unavailable();

    let seq: number;
    try {
      const fence = this.#fence ? await this.#fence() : null;
      ({ seq } = await this.#storage.commit(prepared.batch, fence));
    } catch (err) {
      if (!(err instanceof StorageRequestError)) this.fail(err);
      throw err;
    }

    // Adopt, then publish.
    this.#lastSeq = seq;
    for (const change of prepared.documents) {
      this.#docs.set(docKey(change.kind, change.scopeId), change.after);
    }
    const event: CommitEvent = {
      sessionId: this.sessionId,
      seq,
      batch: prepared.batch,
      documents: prepared.documents,
    };
    this.#publisher.publish(event);
    return value;
  }

  /** Build the storage batch: snapshot drafts, enforce limits. Throws to roll back. */
  #prepare(w: TransactionWrites, now: number): PreparedCommit {
    for (const entry of w.entries) {
      enforceLimit(this.#limits, "maxEntryBytes", jsonByteLength(entry));
    }

    const puts: DocumentRecord[] = [];
    const documents: DocumentChange[] = [];
    for (const draft of w.drafts) {
      const parsed = parseDoc(draft.def, draft.scopeId, draft.draftValue());
      // Host redaction (R20) applies to documents too: re-validate the result
      // so a redactor cannot store a value the definition would reject. The
      // committed `before` was redacted the same way, so the no-op check
      // compares like with like.
      const after = this.#redactDoc(draft.def, draft.scopeId, parsed);
      if (isDeepStrictEqual(after, draft.before ?? draft.def.initial)) continue;
      enforceLimit(this.#limits, "maxDocumentBytes", jsonByteLength(after));
      const data = structuredClone(after);
      puts.push({ kind: draft.def.kind, scopeId: draft.scopeId, data, updatedAt: now });
      documents.push({
        kind: draft.def.kind,
        scopeId: draft.scopeId,
        before: draft.before === null ? null : structuredClone(draft.before),
        after: structuredClone(data),
      });
    }

    const batch: CommitBatch = {};
    if (w.conversations.length) batch.createConversations = w.conversations;
    if (w.entries.length) batch.appendEntries = w.entries;
    if (w.createdTasks.length) batch.createTasks = w.createdTasks;
    if (w.replacedTasks.length) batch.replaceTasks = w.replacedTasks;
    if (w.createdSubmissions.length) batch.createSubmissions = w.createdSubmissions;
    if (w.replacedSubmissions.length) batch.replaceSubmissions = w.replacedSubmissions;
    if (puts.length) batch.putDocuments = puts;
    return { batch, documents, empty: Object.keys(batch).length === 0 };
  }

  // ---- documents ----------------------------------------------------------

  #redactDoc(def: DocDefinition<JsonValue>, scopeId: string, value: JsonValue): JsonValue {
    if (this.#redact === null) return value;
    const out = this.#redact(structuredClone(value), { record: "document", kind: def.kind });
    assertJson(out);
    return parseDoc(def, scopeId, structuredClone(out));
  }

  async #committedDoc<T extends JsonValue>(
    def: DocDefinition<T>,
    scopeId: string,
  ): Promise<T | null> {
    const key = docKey(def.kind, scopeId);
    if (!this.#docs.has(key)) {
      const stored = await this.#guard(() => this.#storage.getDocument(def.kind, scopeId));
      const value = stored === null ? null : parseDoc(def, scopeId, stored.data);
      // A commit adopted while we were reading wins: it is newer.
      if (!this.#docs.has(key)) this.#docs.set(key, value);
    }
    const value = this.#docs.get(key) ?? null;
    return value === null ? null : (structuredClone(value) as T);
  }

  // ---- lifecycle ----------------------------------------------------------

  /** Run a storage call; a non-request storage error fails the session. */
  async #guard<R>(call: () => Promise<R>): Promise<R> {
    if (this.#state === "failed" || this.#state === "closed") throw this.#unavailable();
    try {
      return await call();
    } catch (err) {
      if (!(err instanceof StorageRequestError)) this.fail(err);
      throw err;
    }
  }

  #unavailable(): SessionFailed {
    if (this.#state === "failed") {
      const why = this.#failure instanceof Error ? this.#failure.message : String(this.#failure);
      return new SessionFailed(this.sessionId, why, { cause: this.#failure });
    }
    return new SessionFailed(this.sessionId, "session is closed");
  }

  #end(end: SessionEnd): void {
    this.#publisher.clear();
    this.#abort.abort(
      end.reason === "failed" ? this.#unavailable() : new SessionFailed(this.sessionId, "closed"),
    );
    this.#storage.close().then(
      () => this.#resolveClosed(end),
      () => this.#resolveClosed(end),
    );
  }
}
