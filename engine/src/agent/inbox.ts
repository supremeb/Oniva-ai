/**
 * The inbox (R12): inputs waiting for a busy conversation.
 *
 * The `agent.inbox` document holds, per conversation:
 * - `queue`: pending submissions in arrival order, each a `followUp` or a
 *   `steer`;
 * - `placed`: steers already placed into a run, which settle with that run.
 *
 * Every change happens inside the commit that causes it, so queued inputs
 * survive a restart and are picked up when the run that is now resumed
 * reaches the matching point:
 * - at a tool-round boundary (after the round's results, before the next
 *   model request, same run) every queued steer is placed: its `user` entry
 *   is appended and it becomes `active` with the run's next generation;
 * - when a run ends, its submission and placed steers settle together and
 *   the head of the queue (a follow-up, or a steer that missed the run's last
 *   boundary) starts the next run in the same commit.
 */
import { z } from "zod";
import type { TextContent } from "../models/types.js";
import { type DocDraft, defineDoc } from "../session/documents.js";
import type { Transaction } from "../session/transaction.js";
import type { SubmissionRecord, TaskRecord } from "../storage/types.js";
import type { Lane, TaskDefinition, TaskOps } from "../tasks/define.js";
import { ENTRY_KINDS, toEntryPayload, type UserPayload } from "./context-builder.js";
import type { GenerationCheckpoint, GenerationInput } from "./generation.js";
import { agentConfigDoc } from "./prompt.js";
import {
  type SubmissionAnswer,
  type SubmissionUnanswered,
  settleSubmission,
  storedContent,
  updateSubmission,
} from "./submission.js";

/** The first {@link INBOX_PREVIEW_CHARS} characters of an input's text. */
export function previewOf(content: string | TextContent[]): string {
  const text = typeof content === "string" ? content : content.map((c) => c.text).join("");
  return Array.from(text).slice(0, INBOX_PREVIEW_CHARS).join("");
}

/** Entry kind of a display-only write; ignored by the context builder. */
export const DISPLAY_ENTRY_KIND = "display";

export type InboxMode = "followUp" | "steer";

/** Longest text preview kept on a queued item. */
export const INBOX_PREVIEW_CHARS = 200;

/**
 * A queued input. `preview` (the first {@link INBOX_PREVIEW_CHARS} characters
 * of its text) and `requestId` are absent on items queued before they existed.
 */
export type InboxQueueItem = {
  submissionId: string;
  mode: InboxMode;
  preview?: string;
  requestId?: string;
};

export type InboxState = {
  queue: InboxQueueItem[];
  placed: Array<{ submissionId: string; runId: string }>;
};

const inboxSchema = z.object({
  queue: z.array(
    z.object({
      submissionId: z.string(),
      mode: z.enum(["followUp", "steer"]),
      preview: z.exactOptional(z.string()),
      requestId: z.exactOptional(z.string()),
    }),
  ),
  placed: z.array(z.object({ submissionId: z.string(), runId: z.string() })),
});

export const inboxDoc = defineDoc<InboxState>({
  kind: "agent.inbox",
  scope: "conversation",
  initial: { queue: [], placed: [] },
  schema: inboxSchema,
});

type Generation = TaskDefinition<GenerationInput, GenerationCheckpoint>;

/** Start a run for `submissionId`: spawn its first generation task. */
export function startRun(
  tx: Transaction,
  ops: TaskOps,
  generation: Generation,
  conversationId: string,
  submissionId: string,
  runId: string = tx.mintId("task"),
  lane: Lane = "interactive",
): TaskRecord {
  return ops.spawn(generation, {
    id: runId,
    conversationId,
    parentTaskId: null,
    input: { runId, submissionId, round: 0, ...(lane === "background" ? { lane } : {}) },
  });
}

/** Append the `user` entry of an input. */
export function appendUserEntry(
  tx: Transaction,
  conversationId: string,
  submissionId: string,
  content: UserPayload["content"],
  requestId?: string,
): string {
  const payload: UserPayload =
    requestId === undefined ? { submissionId, content } : { submissionId, content, requestId };
  return tx.appendEntry({
    conversationId,
    taskId: null,
    kind: ENTRY_KINDS.user,
    payload: toEntryPayload(ENTRY_KINDS.user, payload),
  }).id;
}

/**
 * The inbox inside one commit. Table reads are only allowed before a commit's
 * first table write, so {@link Inbox.load} reads the document and every
 * submission it references up front; the methods afterwards only write.
 */
export class Inbox {
  private constructor(
    readonly tx: Transaction,
    readonly conversationId: string,
    readonly draft: DocDraft<InboxState>,
    readonly subs: Map<string, SubmissionRecord>,
    /** The conversation's scheduling lane, for the runs this inbox starts. */
    readonly lane: Lane = "interactive",
  ) {}

  static async load(tx: Transaction, conversationId: string): Promise<Inbox> {
    const draft = await tx.doc(inboxDoc, conversationId);
    const lane = (await tx.doc(agentConfigDoc, conversationId)).value.lane;
    const subs = new Map<string, SubmissionRecord>();
    const ids = [
      ...draft.value.queue.map((q) => q.submissionId),
      ...draft.value.placed.map((p) => p.submissionId),
    ];
    for (const id of new Set(ids)) {
      const sub = await tx.getSubmission(id);
      if (sub) subs.set(id, sub);
    }
    return new Inbox(tx, conversationId, draft, subs, lane);
  }

  get queued(): number {
    return this.draft.value.queue.length;
  }

  /** Queue a pending submission, keeping a short preview of its text. */
  enqueue(
    submissionId: string,
    mode: InboxMode,
    content?: string | TextContent[],
    requestId?: string,
  ): void {
    this.draft.value.queue.push({
      submissionId,
      mode,
      ...(content === undefined ? {} : { preview: previewOf(content) }),
      ...(requestId === undefined ? {} : { requestId }),
    });
  }

  /**
   * Withdraw one still-queued input, settling it `unanswered(withdrawn)`.
   * False when it is not queued (already placed, started or settled).
   */
  cancel(submissionId: string): boolean {
    const { queue } = this.draft.value;
    if (!queue.some((q) => q.submissionId === submissionId)) return false;
    this.draft.value.queue = queue.filter((q) => q.submissionId !== submissionId);
    const sub = this.subs.get(submissionId) ?? null;
    if (
      settleSubmission(this.tx, sub, "unanswered", {
        reason: "withdrawn",
        message: "the input was cancelled before it was answered",
        error: null,
      })
    ) {
      this.subs.delete(submissionId);
    }
    return true;
  }

  /**
   * Start the next queued input, if any. The caller guarantees no run is
   * active (or that the current one ends in this commit). Returns the started
   * submission's id, or null.
   */
  startNext(ops: TaskOps, generation: Generation): string | null {
    const { queue } = this.draft.value;
    while (queue.length > 0) {
      const head = queue.shift() as InboxState["queue"][number];
      const sub = this.#pending(head.submissionId);
      if (!sub) continue;
      const runId = this.tx.mintId("task");
      appendUserEntry(this.tx, this.conversationId, sub.id, storedContent(sub), sub.requestId);
      this.#update(sub, { status: "active", taskId: runId });
      startRun(this.tx, ops, generation, this.conversationId, sub.id, runId, this.lane);
      return sub.id;
    }
    return null;
  }

  /**
   * Tool-round boundary: place every queued steer into run `runId`, whose
   * next generation is `nextTaskId`. Appends their `user` entries (after the
   * round's results) and makes them active with that generation. Returns the
   * placed submission ids.
   */
  placeSteers(runId: string, nextTaskId: string): string[] {
    const value = this.draft.value;
    const steers = value.queue.filter((q) => q.mode === "steer");
    if (steers.length === 0) return [];
    value.queue = value.queue.filter((q) => q.mode !== "steer");
    const placed: string[] = [];
    for (const s of steers) {
      const sub = this.#pending(s.submissionId);
      if (!sub) continue;
      appendUserEntry(this.tx, this.conversationId, sub.id, storedContent(sub), sub.requestId);
      this.#update(sub, { status: "active", taskId: nextTaskId });
      value.placed.push({ submissionId: sub.id, runId });
      placed.push(sub.id);
    }
    return placed;
  }

  /** Run `runId` moved on to generation `nextTaskId`: point its placed steers at it. */
  advance(runId: string, nextTaskId: string): void {
    for (const p of this.draft.value.placed) {
      if (p.runId !== runId) continue;
      const sub = this.subs.get(p.submissionId);
      if (sub?.status === "active") this.#update(sub, { taskId: nextTaskId });
    }
  }

  /**
   * Run `runId` ended: settle the steers placed into it with the run's
   * result, then (unless `startNext` is false) start the next queued input.
   * The caller settles the run's own submission.
   */
  endRun(
    ops: TaskOps,
    generation: Generation,
    runId: string,
    status: "done" | "unanswered",
    result: SubmissionAnswer | SubmissionUnanswered,
    startNext = true,
  ): void {
    const value = this.draft.value;
    const mine = value.placed.filter((p) => p.runId === runId);
    if (mine.length > 0) {
      value.placed = value.placed.filter((p) => p.runId !== runId);
      for (const p of mine) {
        const sub = this.subs.get(p.submissionId) ?? null;
        if (settleSubmission(this.tx, sub, status, result)) this.subs.delete(p.submissionId);
      }
    }
    if (startNext) this.startNext(ops, generation);
  }

  /**
   * Withdraw every queued input (settling it `unanswered(withdrawn)`) and
   * forget placed steers (their run is being aborted; the caller settles
   * them). Returns the withdrawn ids.
   */
  withdrawAll(): string[] {
    const { queue } = this.draft.value;
    this.draft.set({ queue: [], placed: [] });
    const withdrawn: string[] = [];
    for (const q of queue) {
      const sub = this.subs.get(q.submissionId) ?? null;
      const ok = settleSubmission(this.tx, sub, "unanswered", {
        reason: "withdrawn",
        message: "the conversation was aborted before this input was answered",
        error: null,
      });
      if (ok) {
        withdrawn.push(q.submissionId);
        this.subs.delete(q.submissionId);
      }
    }
    return withdrawn;
  }

  #pending(id: string): SubmissionRecord | null {
    const sub = this.subs.get(id);
    return sub?.status === "pending" ? sub : null;
  }

  #update(sub: SubmissionRecord, patch: { status?: "active"; taskId?: string | null }): void {
    updateSubmission(this.tx, sub, patch);
    this.subs.set(sub.id, { ...sub, ...patch });
  }
}
