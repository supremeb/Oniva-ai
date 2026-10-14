/**
 * The generation task (R2, R8, KTD11): one model request and its tool round.
 *
 * A run is a chain of generation tasks, each a root task of the conversation
 * with the run's `runId`:
 * - `request`: build the context (newest compaction head, newest `system`
 *   entry, appending a new `system` entry only when the prompt or tool set
 *   changed), run `beforeRequest` hooks, ask `resolveModelRequest` and stream
 *   the reply, writing throttled partial output to the `agent.live`
 *   document. One commit then stores the `assistant` entry, the usage and one
 *   tool task per call (up to the per-round limit; calls over it get a
 *   `limit_exceeded` result) and waits `allSettled` on the tool tasks. A
 *   reply without tool calls answers the run's submission and finishes.
 * - `after_tools`: synthesize results for tool tasks that ended without one
 *   (aborted, faulted), then either finish the run (every result asked to
 *   `terminate`) or hand off to the next generation, which the same commit
 *   spawns.
 *
 * A model error (including a policy denial) fails the run: a `run_error`
 * entry is written and the submission settles `unanswered(model_error)`.
 */
import type { Context } from "../core/context.js";
import type { JsonValue } from "../core/json.js";
import { MessageBuilder } from "../models/stream.js";
import type { AssistantMessage, ModelInput, ToolCallBlock, Usage } from "../models/types.js";
import type { Transaction } from "../session/transaction.js";
import type { SubmissionRecord } from "../storage/types.js";
import {
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  waiting,
} from "../tasks/define.js";
import {
  type AssistantPayload,
  assistantText,
  buildContext,
  ENTRY_KINDS,
  type RunErrorPayload,
} from "./context-builder.js";
import { liveDoc, ProgressWriter } from "./progress.js";
import { systemPayload } from "./prompt.js";
import {
  type AgentDeps,
  appendToolResult,
  listAllEntries,
  loadConversation,
  resultPayload,
  type ToolTaskCheckpoint,
  type ToolTaskInput,
  type ToolTaskValue,
} from "./tool-task.js";
import { recordUsage, usageDoc } from "./usage.js";

export const GENERATION_TASK_KIND = "agent.generation";

export type GenerationInput = {
  runId: string;
  submissionId: string;
  /** 0 for the run's first request. */
  round: number;
};

type RoundCall = { id: string; name: string; taskId: string | null };

export type GenerationCheckpoint = {
  assistantEntryId: string;
  calls: RoundCall[];
} | null;

/** A generation task's `done` value. */
export type GenerationValue =
  | { outcome: "answered"; entryId: string }
  | { outcome: "terminated" }
  | { outcome: "continued"; next: string }
  | { outcome: "failed"; reason: "model_error" };

/** `done` result of a submission. */
export type SubmissionAnswer = { entryId: string | null; text: string; terminated: boolean };

/** `unanswered` result of a submission. */
export type SubmissionUnanswered = {
  reason: string;
  message: string;
  error: JsonValue;
};

/** Settle a submission still open (pending or active); a settled one is left alone. */
export function settleSubmission(
  tx: Transaction,
  sub: SubmissionRecord | null,
  status: "done" | "unanswered",
  result: SubmissionAnswer | SubmissionUnanswered,
): boolean {
  if (!sub || (sub.status !== "pending" && sub.status !== "active")) return false;
  const { seq: _seq, ...rec } = sub as SubmissionRecord & { seq?: number };
  tx.replaceSubmission({ ...rec, status, result: result as unknown as JsonValue });
  return true;
}

/** Clear the live document if it belongs to `taskId`. */
export async function clearLive(
  tx: Transaction,
  conversationId: string,
  taskId: string,
): Promise<void> {
  const live = await tx.doc(liveDoc, conversationId);
  if (live.value.taskId === taskId) live.set({ runId: null, taskId: null, message: null });
}

type Scope = TaskScope<GenerationCheckpoint>;
type State = TaskState<GenerationInput, GenerationCheckpoint>;

export function generationTaskDefinition(
  deps: AgentDeps,
  toolTask: TaskDefinition<ToolTaskInput, ToolTaskCheckpoint>,
): TaskDefinition<GenerationInput, GenerationCheckpoint> {
  const def: TaskDefinition<GenerationInput, GenerationCheckpoint> = defineTask<
    GenerationInput,
    GenerationCheckpoint
  >({
    kind: GENERATION_TASK_KIND,
    phases: {
      request: (task, rt, ctx) => request(task, rt, ctx),
      after_tools: (task, rt) => afterTools(task, rt),
    },
  });
  return def;

  async function request(task: State, rt: Scope, ctx: Context): Promise<Committed> {
    const { runId, round, submissionId } = task.input;
    const conversationId = task.conversationId;
    const { conversation, agent } = await loadConversation(deps, conversationId);
    const specs = deps.tools.specs(agent.tools);
    const system = systemPayload(agent.systemPrompt, specs);

    let built = buildContext(await listAllEntries(deps.session, conversationId));
    if (built.system?.fingerprint !== system.fingerprint) {
      // Replay-tolerant: a rerun after a crash finds the entry and skips this.
      await rt.write((tx) => {
        tx.appendEntry({
          conversationId,
          taskId: task.id,
          kind: ENTRY_KINDS.system,
          payload: system as unknown as JsonValue,
        });
      });
      built = buildContext(await listAllEntries(deps.session, conversationId));
    }
    // U11 seam: compaction before the request (blocking threshold) goes here.

    let input: ModelInput = { messages: built.messages };
    if (specs.length > 0) input.tools = specs;
    if (agent.thinking !== null) input.thinking = agent.thinking;
    input = await deps.hooks.beforeRequest({ conversation, agent, runId, round, input }, task.id);

    const stream = deps.models.resolveAndStream(
      ctx,
      (c) =>
        deps.policy.resolveModelRequest({
          ctx: c,
          purpose: "generation",
          conversation,
          agent,
          runId,
          round,
        }),
      input,
    );

    let builder: MessageBuilder | null = null;
    const writer = new ProgressWriter(
      deps.clock,
      deps.progressIntervalMs,
      async () => {
        const message = builder?.snapshot() ?? null;
        await rt.write(async (tx) => {
          (await tx.doc(liveDoc, conversationId)).set({
            runId,
            taskId: task.id,
            message: message as unknown as JsonValue,
          });
        });
      },
      (err) => {
        if (!ctx.cancelled)
          deps.report(err, { during: "progress", taskId: task.id, conversationId });
      },
    );
    try {
      for await (const event of stream) {
        if (event.type === "start") builder = new MessageBuilder(event.provider, event.model);
        else if (event.type === "done" || event.type === "error") break;
        else builder?.apply(event);
        writer.mark();
      }
    } finally {
      await writer.close();
    }
    const terminal = await stream.result();

    if (terminal.type === "error") {
      ctx.throwIfCancelled();
      // U11 seam: a `context_overflow` error triggers one compaction and one retry.
      const error = terminal.error;
      const partial = terminal.partial;
      return rt.commit(async (tx) => {
        const sub = await tx.getSubmission(submissionId);
        const payload: RunErrorPayload = { runId, error };
        tx.appendEntry({
          conversationId,
          taskId: task.id,
          kind: ENTRY_KINDS.runError,
          payload: payload as unknown as JsonValue,
        });
        if (partial.provider !== "") {
          recordUsage(
            await tx.doc(usageDoc, conversationId),
            partial.provider,
            partial.model,
            terminal.usage,
          );
        }
        await clearLive(tx, conversationId, task.id);
        settleSubmission(tx, sub, "unanswered", {
          reason: "model_error",
          message: error.message,
          error: error as unknown as JsonValue,
        });
        return done({ outcome: "failed", reason: "model_error" } satisfies GenerationValue);
      });
    }

    const message = terminal.message;
    await deps.hooks.afterResponse(
      { conversation, agent, runId, round, message, usage: terminal.usage },
      task.id,
    );
    const calls = message.blocks.filter((b): b is ToolCallBlock => b.type === "tool_call");
    return rt.commit(async (tx, ops) => {
      const sub = await tx.getSubmission(submissionId);
      const entryId = appendAssistant(tx, task, message, terminal.usage);
      recordUsage(
        await tx.doc(usageDoc, conversationId),
        message.provider,
        message.model,
        terminal.usage,
      );
      await clearLive(tx, conversationId, task.id);

      if (calls.length === 0) {
        settleSubmission(tx, sub, "done", {
          entryId,
          text: assistantText(message),
          terminated: false,
        });
        return done({ outcome: "answered", entryId } satisfies GenerationValue);
      }

      // One tool task per call up to the limit. Sequential tools are ordered
      // after everything before them, and everything after waits for them.
      const cap = deps.limits.maxToolCallsPerRound;
      const roundCalls: RoundCall[] = [];
      const spawned: string[] = [];
      let gate: string[] = [];
      let group: string[] = [];
      for (const [i, call] of calls.entries()) {
        if (i >= cap) {
          const payload = resultPayload(
            runId,
            call,
            "limit_exceeded",
            {
              content: `Too many tool calls in one round: at most ${cap} run, this was call ${i + 1} of ${calls.length}.`,
              isError: true,
            },
            0,
          );
          appendToolResult(tx, conversationId, task.id, payload);
          roundCalls.push({ id: call.id, name: call.name, taskId: null });
          continue;
        }
        const sequential = deps.tools.get(call.name)?.sequential === true;
        const after = sequential ? [...new Set([...gate, ...group])] : [...gate];
        const child = ops.spawn(toolTask, {
          input: {
            runId,
            call: {
              id: call.id,
              name: call.name,
              arguments: call.arguments,
              invalidArguments: call.invalidArguments ?? null,
            },
            after,
          },
        });
        spawned.push(child.id);
        roundCalls.push({ id: call.id, name: call.name, taskId: child.id });
        if (sequential) {
          gate = [child.id];
          group = [];
        } else {
          group.push(child.id);
        }
      }
      const checkpoint: GenerationCheckpoint = { assistantEntryId: entryId, calls: roundCalls };
      if (spawned.length === 0) return next("after_tools", checkpoint);
      return waiting({ on: spawned, policy: "allSettled", phase: "after_tools", checkpoint });
    });
  }

  async function afterTools(task: State, rt: Scope): Promise<Committed> {
    const { runId, round, submissionId } = task.input;
    const conversationId = task.conversationId;
    const cp = task.checkpoint;
    if (cp === null) throw new Error("after_tools without a checkpoint");

    const missing: RoundCall[] = [];
    const terminates: boolean[] = [];
    for (const call of cp.calls) {
      if (call.taskId === null) {
        terminates.push(false); // limit_exceeded, written with the assistant entry
        continue;
      }
      const t = rt.task(call.taskId);
      if (t?.outcome?.type === "done") {
        terminates.push((t.outcome.value as ToolTaskValue).terminate === true);
      } else {
        missing.push(call);
        terminates.push(false);
      }
    }
    const terminated = terminates.length > 0 && terminates.every(Boolean);

    return rt.commit(async (tx, ops) => {
      const sub = await tx.getSubmission(submissionId);
      for (const call of missing) {
        const t = call.taskId === null ? null : rt.task(call.taskId);
        const why =
          t?.outcome?.type === "aborted"
            ? `The tool call was aborted (${t.outcome.reason}).`
            : t?.outcome?.type === "faulted"
              ? `The tool call failed: ${t.outcome.message}`
              : "The tool call ended without a result.";
        const status = t?.outcome?.type === "aborted" ? "aborted" : "error";
        appendToolResult(
          tx,
          conversationId,
          call.taskId,
          resultPayload(runId, call, status, { content: why, isError: true }, 0),
        );
      }
      if (terminated) {
        settleSubmission(tx, sub, "done", { entryId: null, text: "", terminated: true });
        return done({ outcome: "terminated" } satisfies GenerationValue);
      }
      // U8 seam: steers queued during this round are appended here, after the
      // tool results and before the next request.
      const nextTask = ops.spawn(def, {
        input: { runId, submissionId, round: round + 1 },
        parentTaskId: null,
      });
      if (sub && (sub.status === "active" || sub.status === "pending")) {
        const { seq: _seq, ...rec } = sub;
        tx.replaceSubmission({ ...rec, taskId: nextTask.id });
      }
      return done({ outcome: "continued", next: nextTask.id } satisfies GenerationValue);
    });
  }
}

function appendAssistant(
  tx: Transaction,
  task: State,
  message: AssistantMessage,
  usage: Usage,
): string {
  const payload: AssistantPayload = {
    runId: task.input.runId,
    round: task.input.round,
    message,
    usage,
  };
  return tx.appendEntry({
    conversationId: task.conversationId,
    taskId: task.id,
    kind: ENTRY_KINDS.assistant,
    payload: payload as unknown as JsonValue,
  }).id;
}
