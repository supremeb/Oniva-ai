/**
 * Submissions (R11, R12).
 *
 * A submission is one input to a conversation, idempotent by `requestId`
 * (unique per conversation; it defaults to the submission id). Its record
 * moves `pending` (queued in the inbox) → `active` (being answered by a run)
 * → `done` or `unanswered`. `submission.wait()` resolves once it settles;
 * `harness.submission(id)` reacquires one after a restart.
 *
 * Input kinds:
 * - `type: "input"` (default) is answered by a run. While the conversation
 *   is busy, `whenBusy` decides: `followUp` (default) queues it to start the
 *   next run, `steer` places it into the current run at the next tool-round
 *   boundary, `reject` throws `ConversationBusy` and stores nothing.
 * - `type: "write"` is display-only: it is appended at once as a `display`
 *   entry (never sent to the model), settles `done`, and starts no run.
 */
import type { JsonValue } from "../core/json.js";
import type { TextContent } from "../models/types.js";
import type { Transaction } from "../session/transaction.js";
import type { SubmissionRecord } from "../storage/types.js";
import type { AgentCore } from "./conversation.js";

export type WhenBusy = "followUp" | "steer" | "reject";
export type SubmissionType = "input" | "write";

export const WHEN_BUSY: readonly WhenBusy[] = Object.freeze(["followUp", "steer", "reject"]);
export const SUBMISSION_TYPES: readonly SubmissionType[] = Object.freeze(["input", "write"]);

export interface SubmitInput {
  /** Plain-text input (shorthand for `content`). */
  text?: string;
  content?: string | TextContent[];
  /** Caller's id for the input; unique per conversation. Defaults to the submission id. */
  requestId?: string;
  /** What to do while a run is active (inputs only). Default `followUp`. */
  whenBusy?: WhenBusy;
  /** `input` (default) is answered; `write` is display-only. */
  type?: SubmissionType;
  /**
   * The host's assertion that this input is untrusted content (e.g. an
   * uploaded image or a forwarded document): it taints the conversation when
   * the input reaches the model.
   */
  untrusted?: boolean;
}

/** The stored `input` of a submission. */
export type SubmissionInputRecord = {
  content: string | TextContent[];
  type: SubmissionType;
  whenBusy: WhenBusy;
  untrusted: boolean;
};

/** `done` result of an answered submission. */
export type SubmissionAnswer = { entryId: string | null; text: string; terminated: boolean };

/** `done` result of a display-only write. */
export type SubmissionWritten = { entryId: string; displayOnly: true };

/** `unanswered` result of a submission. */
export type SubmissionUnanswered = {
  /** `model_error`, `aborted`, `faulted`, `withdrawn`, ... */
  reason: string;
  message: string;
  error: JsonValue;
};

/** What a settled submission stores as its `result`. */
export type SubmissionResult = SubmissionAnswer | SubmissionWritten | SubmissionUnanswered;

/** A submission result as stored JSON. */
export function toSubmissionResult(result: SubmissionResult): JsonValue {
  return result as unknown as JsonValue;
}

/** The result a submission was settled with (null while it is open). */
export function submissionResult(sub: SubmissionRecord): SubmissionResult | null {
  return sub.result as unknown as SubmissionResult | null;
}

/** A submission input as stored JSON. */
export function toSubmissionInput(input: SubmissionInputRecord): JsonValue {
  return input as unknown as JsonValue;
}

export interface SubmissionSettled {
  status: "done" | "unanswered";
  /** `SubmissionAnswer` / `SubmissionWritten` when done, `SubmissionUnanswered` otherwise. */
  result: JsonValue;
  record: SubmissionRecord;
}

export class Submission {
  constructor(
    readonly core: AgentCore,
    readonly id: string,
  ) {}

  /** The committed record, or null for an unknown id. */
  async record(): Promise<SubmissionRecord | null> {
    return this.core.session.reader.getSubmission(this.id);
  }

  /** Resolves once the submission is `done` or `unanswered`. */
  wait(): Promise<SubmissionSettled> {
    return this.core.waitForSubmission(this.id);
  }
}

/** Settle a submission still open (pending or active); a settled one is left alone. */
export function settleSubmission(
  tx: Transaction,
  sub: SubmissionRecord | null,
  status: "done" | "unanswered",
  result: SubmissionResult,
): boolean {
  if (!sub || (sub.status !== "pending" && sub.status !== "active")) return false;
  const { seq: _seq, ...rec } = sub as SubmissionRecord & { seq?: number };
  tx.replaceSubmission({ ...rec, status, result: toSubmissionResult(result) });
  return true;
}

/** Replace an open submission's status and task (a settled one is left alone). */
export function updateSubmission(
  tx: Transaction,
  sub: SubmissionRecord | null,
  patch: { status?: "pending" | "active"; taskId?: string | null },
): boolean {
  if (!sub || (sub.status !== "pending" && sub.status !== "active")) return false;
  const { seq: _seq, ...rec } = sub as SubmissionRecord & { seq?: number };
  tx.replaceSubmission({ ...rec, ...patch });
  return true;
}

export function inputContent(input: SubmitInput): string | TextContent[] {
  const content = input.content ?? input.text;
  if (typeof content === "string") {
    if (content.length === 0) throw new TypeError("input text must not be empty");
    return content;
  }
  if (
    Array.isArray(content) &&
    content.length > 0 &&
    content.every((p) => p?.type === "text" && typeof p.text === "string")
  ) {
    return structuredClone(content);
  }
  throw new TypeError("submit needs text or text content");
}

/** Validate and normalize a submit request. */
export function normalizeSubmit(input: SubmitInput): {
  record: SubmissionInputRecord;
  requestId: string | null;
} {
  const content = inputContent(input);
  const type = input.type ?? "input";
  if (!SUBMISSION_TYPES.includes(type)) throw new TypeError(`unknown submission type ${type}`);
  const whenBusy = input.whenBusy ?? "followUp";
  if (!WHEN_BUSY.includes(whenBusy)) throw new TypeError(`unknown whenBusy ${whenBusy}`);
  const requestId = input.requestId ?? null;
  if (requestId !== null && (typeof requestId !== "string" || requestId.length === 0)) {
    throw new TypeError("requestId must be a non-empty string");
  }
  if (input.untrusted !== undefined && typeof input.untrusted !== "boolean") {
    throw new TypeError("untrusted must be a boolean");
  }
  return { record: { content, type, whenBusy, untrusted: input.untrusted ?? false }, requestId };
}

/** The content of a stored submission input. */
export function storedContent(sub: SubmissionRecord): string | TextContent[] {
  return (sub.input as unknown as SubmissionInputRecord).content;
}

/** Whether a stored submission input was marked untrusted by the host. */
export function storedUntrusted(sub: SubmissionRecord): boolean {
  return (sub.input as unknown as SubmissionInputRecord).untrusted === true;
}
