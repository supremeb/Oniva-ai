/**
 * Conversations and submissions.
 *
 * A conversation is bound to its tenant and project for life and carries its
 * agent configuration in the `agent.config` document. `submit` stores the
 * input (a submission plus a `user` entry) and starts a run in one commit;
 * `submission.wait()` resolves once the submission settles `done` or
 * `unanswered`.
 *
 * U8 extends `submit` with `requestId` idempotency and busy handling
 * (`whenBusy: "followUp" | "steer" | "reject"`); today a busy conversation
 * rejects with `ConversationBusy`. Runs start through {@link startRun}, which
 * follow-ups reuse.
 */
import { ConversationBusy } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import type { TextContent } from "../models/types.js";
import type { Session } from "../session/session.js";
import type { Transaction } from "../session/transaction.js";
import type {
  ConversationRecord,
  EntryRecord,
  SubmissionRecord,
  TaskRecord,
} from "../storage/types.js";
import type { TaskDefinition, TaskOps } from "../tasks/define.js";
import type { TaskRuntime } from "../tasks/runtime.js";
import { ENTRY_KINDS, type UserPayload } from "./context-builder.js";
import {
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
} from "./generation.js";
import { type LiveState, liveDoc } from "./progress.js";
import {
  type AgentConfig,
  type AgentConfigInput,
  agentConfigDoc,
  applyAgentConfig,
} from "./prompt.js";
import { listAllEntries } from "./tool-task.js";
import { type UsageTotals, usageDoc } from "./usage.js";

/** What conversations and submissions need from the harness. */
export interface AgentCore {
  readonly session: Session;
  readonly runtime: TaskRuntime;
  readonly generation: TaskDefinition<GenerationInput, GenerationCheckpoint>;
  waitForSubmission(id: string): Promise<SubmissionSettled>;
}

export interface SubmitInput {
  /** Plain-text input (shorthand for `content`). */
  text?: string;
  content?: string | TextContent[];
  /** Caller's id for the input; unique per conversation. Defaults to the submission id. */
  requestId?: string;
}

export interface SubmissionSettled {
  status: "done" | "unanswered";
  /** `SubmissionAnswer` when done, `SubmissionUnanswered` otherwise. */
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

/** Whether a run is active in the conversation (committed state). */
export function isBusy(runtime: TaskRuntime, conversationId: string): boolean {
  return runtime
    .tasks({ conversationId })
    .some(
      (t) => t.kind === GENERATION_TASK_KIND && t.status !== "terminal" && t.parentTaskId === null,
    );
}

/** Start a run for `submissionId`: spawn its first generation task. */
export function startRun(
  tx: Transaction,
  ops: TaskOps,
  generation: TaskDefinition<GenerationInput, GenerationCheckpoint>,
  conversationId: string,
  submissionId: string,
  runId: string = tx.mintId("task"),
): TaskRecord {
  return ops.spawn(generation, {
    id: runId,
    conversationId,
    parentTaskId: null,
    input: { runId, submissionId, round: 0 },
  });
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

  /** Store an input and start answering it. Rejects with `ConversationBusy` during a run. */
  async submit(input: SubmitInput): Promise<Submission> {
    const content = inputContent(input);
    const id = await this.core.runtime.commit(async (tx, ops) => {
      const conv = await tx.getConversation(this.id);
      if (!conv) throw new Error(`unknown conversation ${this.id}`);
      if (isBusy(this.core.runtime, this.id)) throw new ConversationBusy(this.id);
      const submissionId = tx.mintId("submission");
      const runId = tx.mintId("task");
      tx.createSubmission({
        id: submissionId,
        conversationId: this.id,
        requestId: input.requestId ?? submissionId,
        input: { content } as JsonValue,
        status: "active",
        result: null,
        taskId: runId,
      });
      const payload: UserPayload = { submissionId, content };
      tx.appendEntry({
        conversationId: this.id,
        taskId: null,
        kind: ENTRY_KINDS.user,
        payload: payload as unknown as JsonValue,
      });
      startRun(tx, ops, this.core.generation, this.id, submissionId, runId);
      return submissionId;
    });
    return new Submission(this.core, id);
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
