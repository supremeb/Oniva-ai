/**
 * The transaction handed to a `session.commit` callback.
 *
 * Writes are buffered and become one atomic storage batch when the callback
 * returns. Table reads see committed state only, so they are allowed only
 * before the transaction's first table write (a read after a write would not
 * see that write). Document drafts are separate: `tx.doc` always reflects the
 * draft's own changes. Everything is revoked once the callback settles.
 */
import type { IdKind } from "../core/ids.js";
import { assertJson, type JsonValue } from "../core/json.js";
import type {
  ConversationRecord,
  EntryRecord,
  SessionStorage,
  SubmissionRecord,
  TaskOutcome,
  TaskRecord,
} from "../storage/types.js";
import {
  type DocDefinition,
  type DocDraft,
  Draft,
  docKey,
  TransactionSettled,
} from "./documents.js";

/** The read side of session storage. */
export type SessionReader = Pick<
  SessionStorage,
  | "getConversation"
  | "getEntry"
  | "getTask"
  | "getSubmission"
  | "submissionByRequestId"
  | "getDocument"
  | "listConversations"
  | "listEntries"
  | "listTasks"
  | "listSubmissions"
  | "listDocuments"
>;

const READ_METHODS = [
  "getConversation",
  "getEntry",
  "getTask",
  "getSubmission",
  "submissionByRequestId",
  "getDocument",
  "listConversations",
  "listEntries",
  "listTasks",
  "listSubmissions",
  "listDocuments",
] as const satisfies ReadonlyArray<keyof SessionReader>;

/** Wrap every read method of `source` with `wrap`. */
export function wrapReader(
  source: SessionReader,
  wrap: <R>(call: () => Promise<R>) => Promise<R>,
): SessionReader {
  const out: Record<string, unknown> = {};
  for (const name of READ_METHODS) {
    const fn = source[name] as (...args: unknown[]) => Promise<unknown>;
    out[name] = (...args: unknown[]) => wrap(() => fn.apply(source, args));
  }
  return out as unknown as SessionReader;
}

/** Which stored field a {@link Redact} call is rewriting. */
export type RedactTarget =
  | { record: "entry"; kind: string; field: "payload" }
  | { record: "task"; kind: string; field: "input" | "checkpoint" | "signals" | "outcome" }
  | { record: "submission"; field: "input" | "result" };

/**
 * Host redaction hook (R20). Called on every JSON payload a transaction
 * writes — entry payloads (tool results, messages), task inputs, checkpoints,
 * signals and outcomes (tool arguments, error text), and submission inputs and
 * results — before it is stored or published. It receives a private copy and
 * returns the value to store. It must be deterministic and idempotent: stored
 * values may pass through it again when a record is replaced. A throw rolls
 * the commit back.
 */
export type Redact = (value: JsonValue, target: RedactTarget) => JsonValue;

export type NewConversation = Pick<ConversationRecord, "tenantId" | "projectId"> & {
  id?: string;
  ownerTaskId?: string | null;
};

export type NewEntry = Pick<EntryRecord, "conversationId" | "kind" | "payload"> & {
  id?: string;
  taskId?: string | null;
};

export type NewTask = Omit<TaskRecord, "id" | "createdAt" | "updatedAt"> & { id?: string };

export type NewSubmission = Omit<SubmissionRecord, "id" | "createdAt" | "updatedAt"> & {
  id?: string;
};

/** What the session supplies to a transaction. */
export interface TransactionHost {
  readonly reader: SessionReader;
  /** Commit timestamp (injected clock), shared by every record in the commit. */
  readonly now: number;
  readonly redact: Redact | null;
  mintId(kind: IdKind): string;
  /** Committed value of a document, or null when it was never stored. */
  committedDoc<T extends JsonValue>(def: DocDefinition<T>, scopeId: string): Promise<T | null>;
}

/** Buffered writes, in the shape the session turns into a storage batch. */
export interface TransactionWrites {
  conversations: ConversationRecord[];
  entries: EntryRecord[];
  createdTasks: TaskRecord[];
  replacedTasks: TaskRecord[];
  createdSubmissions: SubmissionRecord[];
  replacedSubmissions: SubmissionRecord[];
  drafts: Draft<JsonValue>[];
}

export class Transaction implements SessionReader {
  readonly #host: TransactionHost;
  #settled = false;
  #wrote = false;
  readonly #conversations: ConversationRecord[] = [];
  readonly #entries: EntryRecord[] = [];
  readonly #createdTasks = new Map<string, TaskRecord>();
  readonly #replacedTasks = new Map<string, TaskRecord>();
  readonly #createdSubmissions = new Map<string, SubmissionRecord>();
  readonly #replacedSubmissions = new Map<string, SubmissionRecord>();
  readonly #drafts = new Map<string, Draft<JsonValue>>();
  /** Draft loads in flight, so concurrent `doc` calls share one draft. */
  readonly #loading = new Map<string, Promise<Draft<JsonValue>>>();

  constructor(host: TransactionHost) {
    this.#host = host;
  }

  /** The commit timestamp (injected clock). */
  get now(): number {
    this.#check();
    return this.#host.now;
  }

  mintId(kind: IdKind): string {
    this.#check();
    return this.#host.mintId(kind);
  }

  // ---- writes -------------------------------------------------------------

  createConversation(input: NewConversation): ConversationRecord {
    this.#write();
    const rec: ConversationRecord = {
      id: input.id ?? this.#host.mintId("conversation"),
      tenantId: input.tenantId,
      projectId: input.projectId,
      ownerTaskId: input.ownerTaskId ?? null,
      createdAt: this.#host.now,
    };
    this.#conversations.push(rec);
    return structuredClone(rec);
  }

  appendEntry(input: NewEntry): EntryRecord {
    this.#write();
    const rec: EntryRecord = {
      id: input.id ?? this.#host.mintId("entry"),
      conversationId: input.conversationId,
      taskId: input.taskId ?? null,
      kind: input.kind,
      payload: this.#redact(input.payload, {
        record: "entry",
        kind: input.kind,
        field: "payload",
      }),
      createdAt: this.#host.now,
    };
    this.#entries.push(rec);
    return structuredClone(rec);
  }

  createTask(input: NewTask): TaskRecord {
    this.#write();
    const { id, ...rest } = input;
    const rec = this.#redactTask({
      ...rest,
      id: id ?? this.#host.mintId("task"),
      createdAt: this.#host.now,
      updatedAt: this.#host.now,
    });
    this.#createdTasks.set(rec.id, rec);
    return structuredClone(rec);
  }

  /**
   * Replace a task (whole record; `updatedAt` is stamped). Replacing a task
   * created or replaced earlier in this transaction updates that write.
   */
  replaceTask(record: TaskRecord): TaskRecord {
    this.#write();
    const rec = this.#redactTask({ ...record, updatedAt: this.#host.now });
    const created = this.#createdTasks.get(rec.id);
    if (created) this.#createdTasks.set(rec.id, { ...rec, createdAt: created.createdAt });
    else this.#replacedTasks.set(rec.id, rec);
    return structuredClone(this.#createdTasks.get(rec.id) ?? rec);
  }

  createSubmission(input: NewSubmission): SubmissionRecord {
    this.#write();
    const { id, ...rest } = input;
    const rec = this.#redactSubmission({
      ...rest,
      id: id ?? this.#host.mintId("submission"),
      createdAt: this.#host.now,
      updatedAt: this.#host.now,
    });
    this.#createdSubmissions.set(rec.id, rec);
    return structuredClone(rec);
  }

  /** Replace a submission (whole record; `updatedAt` is stamped). Merges like `replaceTask`. */
  replaceSubmission(record: SubmissionRecord): SubmissionRecord {
    this.#write();
    const rec = this.#redactSubmission({ ...record, updatedAt: this.#host.now });
    const created = this.#createdSubmissions.get(rec.id);
    if (created) this.#createdSubmissions.set(rec.id, { ...rec, createdAt: created.createdAt });
    else this.#replacedSubmissions.set(rec.id, rec);
    return structuredClone(this.#createdSubmissions.get(rec.id) ?? rec);
  }

  /**
   * A draft of document `(def.kind, scopeId)`, starting from its committed
   * value (or `def.initial`). Validated and snapshotted when the commit is
   * stored; unchanged drafts write nothing. Not a table read.
   */
  async doc<T extends JsonValue>(def: DocDefinition<T>, scopeId: string): Promise<DocDraft<T>> {
    this.#check();
    const key = docKey(def.kind, scopeId);
    const existing = this.#drafts.get(key);
    if (existing) return this.#sameDef(existing, def);
    let loading = this.#loading.get(key);
    if (!loading) {
      loading = this.#host.committedDoc(def, scopeId).then((before) => {
        const draft = new Draft(def, scopeId, before) as unknown as Draft<JsonValue>;
        this.#drafts.set(key, draft);
        return draft;
      });
      this.#loading.set(key, loading);
    }
    const draft = await loading;
    this.#check();
    return this.#sameDef(draft, def);
  }

  // ---- reads (committed state; only before the first table write) ---------

  getConversation: SessionReader["getConversation"] = (id) =>
    this.#read((r) => r.getConversation(id));
  getEntry: SessionReader["getEntry"] = (id) => this.#read((r) => r.getEntry(id));
  getTask: SessionReader["getTask"] = (id) => this.#read((r) => r.getTask(id));
  getSubmission: SessionReader["getSubmission"] = (id) => this.#read((r) => r.getSubmission(id));
  submissionByRequestId: SessionReader["submissionByRequestId"] = (conversationId, requestId) =>
    this.#read((r) => r.submissionByRequestId(conversationId, requestId));
  getDocument: SessionReader["getDocument"] = (kind, scopeId) =>
    this.#read((r) => r.getDocument(kind, scopeId));
  listConversations: SessionReader["listConversations"] = (options) =>
    this.#read((r) => r.listConversations(options));
  listEntries: SessionReader["listEntries"] = (conversationId, options) =>
    this.#read((r) => r.listEntries(conversationId, options));
  listTasks: SessionReader["listTasks"] = (options) => this.#read((r) => r.listTasks(options));
  listSubmissions: SessionReader["listSubmissions"] = (conversationId, options) =>
    this.#read((r) => r.listSubmissions(conversationId, options));
  listDocuments: SessionReader["listDocuments"] = (options) =>
    this.#read((r) => r.listDocuments(options));

  // ---- session-facing -----------------------------------------------------

  /** Revoke the transaction and its drafts and return the buffered writes. */
  settle(): TransactionWrites {
    this.#settled = true;
    for (const d of this.#drafts.values()) d.revoke();
    return {
      conversations: this.#conversations,
      entries: this.#entries,
      createdTasks: [...this.#createdTasks.values()],
      replacedTasks: [...this.#replacedTasks.values()],
      createdSubmissions: [...this.#createdSubmissions.values()],
      replacedSubmissions: [...this.#replacedSubmissions.values()],
      drafts: [...this.#drafts.values()],
    };
  }

  // ---- internals ----------------------------------------------------------

  #check(): void {
    if (this.#settled) throw new TransactionSettled("transaction");
  }

  #write(): void {
    this.#check();
    this.#wrote = true;
  }

  #read<R>(call: (reader: SessionReader) => Promise<R>): Promise<R> {
    try {
      this.#check();
      if (this.#wrote) {
        throw new Error(
          "table read after a table write in the same commit (reads see committed state only)",
        );
      }
    } catch (err) {
      return Promise.reject(err);
    }
    return call(this.#host.reader);
  }

  #sameDef<T extends JsonValue>(draft: Draft<JsonValue>, def: DocDefinition<T>): DocDraft<T> {
    if (draft.def !== (def as unknown as DocDefinition<JsonValue>)) {
      throw new Error(
        `document ${def.kind} ${draft.scopeId} opened with two different definitions`,
      );
    }
    return draft as unknown as DocDraft<T>;
  }

  #redact<V extends JsonValue>(value: V, target: RedactTarget): V {
    assertJson(value);
    const copy = structuredClone(value);
    if (this.#host.redact === null) return copy;
    const out = this.#host.redact(copy, target);
    assertJson(out);
    return structuredClone(out) as V;
  }

  #redactTask(rec: TaskRecord): TaskRecord {
    const t = (field: "input" | "checkpoint" | "signals" | "outcome") =>
      ({ record: "task", kind: rec.kind, field }) as const;
    return {
      ...rec,
      waiting: rec.waiting === null ? null : structuredClone(rec.waiting),
      input: this.#redact(rec.input, t("input")),
      checkpoint: this.#redact(rec.checkpoint, t("checkpoint")),
      signals: this.#redact(rec.signals, t("signals")) as TaskRecord["signals"],
      outcome:
        rec.outcome === null
          ? null
          : (this.#redact(
              rec.outcome as unknown as JsonValue,
              t("outcome"),
            ) as unknown as TaskOutcome | null),
    };
  }

  #redactSubmission(rec: SubmissionRecord): SubmissionRecord {
    return {
      ...rec,
      input: this.#redact(rec.input, { record: "submission", field: "input" }),
      result: this.#redact(rec.result, { record: "submission", field: "result" }),
    };
  }
}
