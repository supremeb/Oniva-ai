/**
 * Conversations and submissions.
 *
 * A conversation is bound to its tenant and project for life and carries its
 * agent configuration in the `agent.config` document. `submit` stores the
 * input (a submission plus a `user` entry) and starts a run in one commit;
 * `submission.wait()` resolves once the submission settles `done` or
 * `unanswered`.
 *
 * `submit` is idempotent by `requestId` and handles a busy conversation
 * through the inbox (`whenBusy: "followUp" | "steer" | "reject"`); see
 * `submission.ts` and `inbox.ts`. `abort()` withdraws queued inputs and
 * aborts the current run.
 */
import { ConversationBusy } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import type { Session } from "../session/session.js";
import {
  type ConversationRecord,
  type EntryRecord,
  MAX_PAGE_LIMIT,
  type SubmissionRecord,
} from "../storage/types.js";
import type { TaskDefinition } from "../tasks/define.js";
import type { TaskRuntime } from "../tasks/runtime.js";
import type { UserPayload } from "./context-builder.js";
import {
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
} from "./generation.js";
import { appendUserEntry, DISPLAY_ENTRY_KIND, Inbox, startRun } from "./inbox.js";
import { type LiveState, liveDoc } from "./progress.js";
import {
  type AgentConfig,
  type AgentConfigInput,
  agentConfigDoc,
  applyAgentConfig,
} from "./prompt.js";
import {
  normalizeSubmit,
  Submission,
  type SubmissionSettled,
  type SubmissionWritten,
  type SubmitInput,
  settleSubmission,
  toSubmissionInput,
  toSubmissionResult,
} from "./submission.js";
import { markTainted, rootConversationId } from "./taint.js";
import { listAllEntries } from "./tool-task.js";
import { type UsageTotals, usageDoc } from "./usage.js";

export { startRun } from "./inbox.js";
export {
  inputContent,
  Submission,
  type SubmissionSettled,
  type SubmitInput,
} from "./submission.js";

/** What conversations and submissions need from the harness. */
export interface AgentCore {
  readonly session: Session;
  readonly runtime: TaskRuntime;
  readonly generation: TaskDefinition<GenerationInput, GenerationCheckpoint>;
  waitForSubmission(id: string): Promise<SubmissionSettled>;
}

/** Whether a run is active in the conversation (committed state). */
export function isBusy(runtime: Pick<TaskRuntime, "tasks">, conversationId: string): boolean {
  return runtime
    .tasks({ conversationId })
    .some(
      (t) => t.kind === GENERATION_TASK_KIND && t.status !== "terminal" && t.parentTaskId === null,
    );
}

export class Conversation {
  constructor(
    readonly core: AgentCore,
    readonly id: string,
  ) {}

  /** The stored conversation (tenant and project binding). Throws for an unknown id. */
  async info(): Promise<ConversationRecord> {
    const rec = await this.core.session.reader.getConversation(this.id);
    if (!rec) throw new Error(`unknown conversation ${this.id}`);
    return rec;
  }

  agent(): Promise<AgentConfig> {
    return this.core.session.document(agentConfigDoc, this.id);
  }

  /** Change the agent configuration; takes effect from the next model request. */
  async configure(patch: AgentConfigInput): Promise<AgentConfig> {
    await this.info();
    return this.core.runtime.commit(async (tx) => {
      const draft = await tx.doc(agentConfigDoc, this.id);
      draft.set(applyAgentConfig(draft.value, patch));
      return structuredClone(draft.value);
    });
  }

  busy(): boolean {
    return isBusy(this.core.runtime, this.id);
  }

  /**
   * Store an input. An existing submission with the same `requestId` is
   * returned as is. Otherwise, in one commit: a display-only write is
   * appended and settled; an input starts a run when the conversation is
   * idle, and when it is busy is queued as a follow-up or steer, or rejected
   * with `ConversationBusy` (storing nothing).
   */
  async submit(input: SubmitInput): Promise<Submission> {
    const { record, requestId } = normalizeSubmit(input);
    const reader = this.core.session.reader;
    if (requestId !== null) {
      const existing = await reader.submissionByRequestId(this.id, requestId);
      if (existing) return new Submission(this.core, existing.id);
    }
    const id = await this.core.runtime.commit(async (tx, ops) => {
      // Reads first: a commit may not read tables after its first write.
      const conv = await tx.getConversation(this.id);
      if (!conv) throw new Error(`unknown conversation ${this.id}`);
      if (requestId !== null) {
        const existing = await tx.submissionByRequestId(this.id, requestId);
        if (existing) return existing.id;
      }
      const inbox = await Inbox.load(tx, this.id);
      // An untrusted input taints the root conversation in the same commit
      // that stores it (reads first; a mark is a document write).
      const taintRoot = record.untrusted ? await rootConversationId(tx, conv) : null;

      const submissionId = tx.mintId("submission");
      const base = {
        id: submissionId,
        conversationId: this.id,
        requestId: requestId ?? submissionId,
        input: toSubmissionInput(record),
      };
      if (record.type === "write") {
        const payload: UserPayload = {
          submissionId,
          content: record.content,
          requestId: base.requestId,
        };
        const entry = tx.appendEntry({
          conversationId: this.id,
          taskId: null,
          kind: DISPLAY_ENTRY_KIND,
          payload: payload as unknown as JsonValue,
        });
        const written: SubmissionWritten = { entryId: entry.id, displayOnly: true };
        tx.createSubmission({
          ...base,
          status: "done",
          result: toSubmissionResult(written),
          taskId: null,
        });
        return submissionId;
      }

      let busy = isBusy(this.core.runtime, this.id);
      if (busy && record.whenBusy === "reject") throw new ConversationBusy(this.id);
      // An idle conversation with a backlog (its last run ended without
      // reaching the inbox, e.g. it faulted) serves the backlog first.
      if (!busy && inbox.queued > 0) {
        if (record.whenBusy === "reject") throw new ConversationBusy(this.id);
        busy = inbox.startNext(ops, this.core.generation) !== null;
      }
      if (busy) {
        tx.createSubmission({ ...base, status: "pending", result: null, taskId: null });
        inbox.enqueue(
          submissionId,
          record.whenBusy === "steer" ? "steer" : "followUp",
          record.content,
          base.requestId,
        );
        if (taintRoot !== null) {
          await markTainted(tx, taintRoot, { kind: "input", name: "input", ref: submissionId });
        }
        return submissionId;
      }
      const runId = tx.mintId("task");
      tx.createSubmission({ ...base, status: "active", result: null, taskId: runId });
      appendUserEntry(tx, this.id, submissionId, record.content, base.requestId);
      if (taintRoot !== null) {
        await markTainted(tx, taintRoot, { kind: "input", name: "input", ref: submissionId });
      }
      startRun(tx, ops, this.core.generation, this.id, submissionId, runId, inbox.lane);
      return submissionId;
    });
    return new Submission(this.core, id);
  }

  /**
   * Cancel one still-queued input, settling it `unanswered(withdrawn)` in one
   * commit. `"cancelled"` when it was queued; `"not_queued"` when it already
   * started, was placed into a run or settled (nothing changes);
   * `"unknown"` when no such submission exists in this conversation.
   */
  async cancelSubmission(submissionId: string): Promise<"cancelled" | "not_queued" | "unknown"> {
    await this.info();
    return this.core.runtime.commit(async (tx) => {
      const sub = await tx.getSubmission(submissionId);
      if (!sub || sub.conversationId !== this.id) return "unknown";
      const inbox = await Inbox.load(tx, this.id);
      return inbox.cancel(submissionId) ? "cancelled" : "not_queued";
    });
  }

  /**
   * Withdraw queued inputs (`unanswered(withdrawn)`), settle the current
   * run's inputs `unanswered(aborted)` and abort every live task of the
   * conversation. Resolves once those tasks are terminal, leaving the
   * conversation idle.
   */
  async abort(reason = "aborted"): Promise<void> {
    await this.info();
    const runtime = this.core.runtime;
    await runtime.commit(async (tx, ops) => {
      const active: SubmissionRecord[] = [];
      for (let cursor: string | null = null; ; ) {
        const page = await tx.listSubmissions(this.id, {
          statuses: ["active"],
          limit: MAX_PAGE_LIMIT,
          cursor,
        });
        active.push(...page.items);
        cursor = page.nextCursor;
        if (cursor === null) break;
      }
      const inbox = await Inbox.load(tx, this.id);
      inbox.withdrawAll();
      for (const sub of active) {
        settleSubmission(tx, sub, "unanswered", {
          reason: "aborted",
          message: reason,
          error: null,
        });
      }
      (await tx.doc(liveDoc, this.id)).set(liveDoc.initial);
      ops.abortConversation(this.id, reason);
    });
    for (;;) {
      const live = runtime
        .tasks({ conversationId: this.id })
        .filter((t) => t.status !== "terminal");
      if (live.length === 0) break;
      await Promise.all(live.map((t) => runtime.waitForTask(t.id)));
    }
    // Inputs queued while the abort was finishing start now.
    await runtime.commit(async (tx, ops) => {
      if (isBusy(runtime, this.id)) return;
      const inbox = await Inbox.load(tx, this.id);
      inbox.startNext(ops, this.core.generation);
    });
  }

  /** Every entry, in order. */
  entries(): Promise<EntryRecord[]> {
    return listAllEntries(this.core.session, this.id);
  }

  live(): Promise<LiveState> {
    return this.core.session.document(liveDoc, this.id);
  }

  usage(): Promise<UsageTotals> {
    return this.core.session.document(usageDoc, this.id);
  }
}
