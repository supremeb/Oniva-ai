/**
 * Backend-independent validation of commit batches and page options. Every
 * backend runs these before touching its store; failures throw
 * `StorageRequestError` (non-retryable). Checks that need stored state
 * (uniqueness against existing rows, references, immutable fields) are the
 * backend's job.
 */
import { z } from "zod";
import { StorageRequestError } from "../core/errors.js";
import { isJson, type JsonValue } from "../core/json.js";
import {
  type CommitBatch,
  DEFAULT_PAGE_LIMIT,
  MAX_PAGE_LIMIT,
  type PageOptions,
  SUBMISSION_STATUSES,
  type SubmissionStatus,
  TASK_STATUSES,
  type TaskStatus,
} from "./types.js";

/** Longest id, kind, phase or key string accepted. */
export const MAX_KEY_LENGTH = 256;

const key = z.string().min(1).max(MAX_KEY_LENGTH);
const nullableKey = key.nullable();
const json = z.custom<JsonValue>((v) => isJson(v), { message: "not strict JSON" });
const time = z.number();

const waitSpec = z.strictObject({
  on: z.array(key),
  policy: z.enum(["allSettled", "failFast"]),
  signal: nullableKey,
  until: time.nullable(),
});

const outcome = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("done"), value: json }),
  z.strictObject({ type: z.literal("aborted"), reason: z.string() }),
  z.strictObject({ type: z.literal("faulted"), message: z.string(), detail: json }),
]);

const conversation = z.strictObject({
  id: key,
  tenantId: key,
  projectId: key,
  ownerTaskId: nullableKey,
  createdAt: time,
});

const entry = z.strictObject({
  id: key,
  conversationId: key,
  taskId: nullableKey,
  kind: key,
  payload: json,
  createdAt: time,
});

const task = z
  .strictObject({
    id: key,
    kind: key,
    conversationId: key,
    parentTaskId: nullableKey,
    status: z.enum(TASK_STATUSES as [TaskStatus, ...TaskStatus[]]),
    phase: key,
    checkpoint: json,
    input: json,
    waiting: waitSpec.nullable(),
    signals: z.record(z.string(), json),
    outcome: outcome.nullable(),
    abandonOnRestart: z.boolean(),
    createdAt: time,
    updatedAt: time,
  })
  .superRefine((t, ctx) => {
    if ((t.status === "terminal") !== (t.outcome !== null)) {
      ctx.addIssue({ code: "custom", message: "outcome must be set exactly when terminal" });
    }
    if ((t.status === "waiting") !== (t.waiting !== null)) {
      ctx.addIssue({ code: "custom", message: "waiting must be set exactly when waiting" });
    }
    if (t.parentTaskId === t.id) {
      ctx.addIssue({ code: "custom", message: "a task cannot own itself" });
    }
  });

const submission = z.strictObject({
  id: key,
  conversationId: key,
  requestId: key,
  input: json,
  status: z.enum(SUBMISSION_STATUSES as [SubmissionStatus, ...SubmissionStatus[]]),
  result: json,
  taskId: nullableKey,
  createdAt: time,
  updatedAt: time,
});

const documentRecord = z.strictObject({
  kind: key,
  scopeId: key,
  data: json,
  updatedAt: time,
});

const batchSchema = z.strictObject({
  createConversations: z.array(conversation).optional(),
  appendEntries: z.array(entry).optional(),
  createTasks: z.array(task).optional(),
  replaceTasks: z.array(task).optional(),
  createSubmissions: z.array(submission).optional(),
  replaceSubmissions: z.array(submission).optional(),
  putDocuments: z.array(documentRecord).optional(),
});

function reject(message: string): never {
  throw new StorageRequestError(message, { retryable: false });
}

/**
 * Validate a batch's shape and its internal consistency: strict record shapes
 * and strict JSON values, at least one write, and no id (or document key, or
 * `(conversationId, requestId)`) used twice within the batch.
 */
export function validateBatch(batch: CommitBatch): void {
  const parsed = batchSchema.safeParse(batch);
  if (!parsed.success) reject(`invalid batch: ${z.prettifyError(parsed.error)}`);

  const b = batch;
  const total =
    (b.createConversations?.length ?? 0) +
    (b.appendEntries?.length ?? 0) +
    (b.createTasks?.length ?? 0) +
    (b.replaceTasks?.length ?? 0) +
    (b.createSubmissions?.length ?? 0) +
    (b.replaceSubmissions?.length ?? 0) +
    (b.putDocuments?.length ?? 0);
  if (total === 0) reject("empty batch");

  unique(
    "conversation id",
    (b.createConversations ?? []).map((c) => c.id),
  );
  unique(
    "entry id",
    (b.appendEntries ?? []).map((e) => e.id),
  );
  unique(
    "task id",
    [...(b.createTasks ?? []), ...(b.replaceTasks ?? [])].map((t) => t.id),
  );
  unique(
    "submission id",
    [...(b.createSubmissions ?? []), ...(b.replaceSubmissions ?? [])].map((s) => s.id),
  );
  unique(
    "requestId",
    (b.createSubmissions ?? []).map((s) => JSON.stringify([s.conversationId, s.requestId])),
  );
  unique(
    "document key",
    (b.putDocuments ?? []).map((d) => JSON.stringify([d.kind, d.scopeId])),
  );
}

function unique(what: string, values: string[]): void {
  const seen = new Set<string>();
  for (const v of values) {
    if (seen.has(v)) reject(`duplicate ${what} ${v} in batch`);
    seen.add(v);
  }
}

/** Page options with defaults applied. */
export interface NormalizedPage {
  order: "asc" | "desc";
  limit: number;
  cursor: string | null;
}

export function normalizePage(options: PageOptions = {}): NormalizedPage {
  const order = options.order ?? "asc";
  if (order !== "asc" && order !== "desc") reject(`invalid order ${String(order)}`);
  const limit = options.limit ?? DEFAULT_PAGE_LIMIT;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
    reject(`limit must be an integer in 1..${MAX_PAGE_LIMIT}, got ${limit}`);
  }
  const cursor = options.cursor ?? null;
  if (cursor !== null && typeof cursor !== "string") reject("cursor must be a string");
  return { order, limit, cursor };
}

/** Validate a status filter against the allowed values. */
export function validateStatuses<T extends string>(
  statuses: readonly T[] | undefined,
  allowed: readonly T[],
): void {
  if (statuses === undefined) return;
  if (!Array.isArray(statuses)) reject("statuses must be an array");
  for (const st of statuses) {
    if (!allowed.includes(st)) reject(`unknown status ${String(st)}`);
  }
}

/** Validate the scope ids of a batched document read (at most {@link MAX_PAGE_LIMIT}). */
export function validateScopeIds(scopeIds: readonly string[]): void {
  if (!Array.isArray(scopeIds)) reject("scope ids must be an array");
  if (scopeIds.length > MAX_PAGE_LIMIT) {
    reject(`at most ${MAX_PAGE_LIMIT} scope ids per read, got ${scopeIds.length}`);
  }
  for (const id of scopeIds) {
    if (typeof id !== "string") reject("scope ids must be strings");
  }
}

/** Validate a fence's shape (backends that enforce leases also compare it). */
export function validateFence(fence: { token: bigint | number } | null): void {
  if (fence === null) return;
  const t = fence?.token;
  const ok =
    (typeof t === "bigint" && t >= 0n) ||
    (typeof t === "number" && Number.isSafeInteger(t) && t >= 0);
  if (!ok) reject("fence token must be a non-negative integer");
}

export { reject as rejectRequest };
