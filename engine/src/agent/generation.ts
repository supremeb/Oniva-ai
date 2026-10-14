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
 *
 * Compaction (`compaction.ts`) hooks into `request`: at its start (a turn
 * boundary) a finished background summary is placed as the new context head;
 * past the background threshold a compaction task is started alongside; at
 * the blocking threshold the request waits on one (resuming `request` with a
 * {@link RequestCheckpoint}) and a failed compaction settles the input
 * `unanswered(compaction_failed)`; one `context_overflow` error waits on a
 * compaction and retries once, a second fails the run with `model_error`.
 *
 * The inbox (`inbox.ts`) is served at two points: steers queued during a tool
 * round are placed in the `after_tools` commit that spawns the next
 * generation, and the commit that ends a run settles its placed steers and
 * starts the next queued input.
 */
import type { Context } from "../core/context.js";
import type { JsonValue } from "../core/json.js";
import { MessageBuilder } from "../models/stream.js";
import type {
  AssistantMessage,
  ModelError,
  ModelInput,
  ToolCallBlock,
  Usage,
} from "../models/types.js";
import type { Transaction } from "../session/transaction.js";
import {
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskOps,
  type TaskScope,
  type TaskState,
  waiting,
} from "../tasks/define.js";
import {
  bindCompactionTask,
  type CompactionTask,
  compactionDefaults,
  compactionDoc,
  compactionOutcome,
  compactionTaskDefinition,
  compactionThresholds,
  ensureCompaction,
  estimateContextTokens,
  placeReadyCompaction,
  resolveCompactionSettings,
} from "./compaction.js";
import {
  type AssistantPayload,
  assistantText,
  buildContext,
  ENTRY_KINDS,
  type RunErrorPayload,
} from "./context-builder.js";
import { Inbox } from "./inbox.js";
import { liveDoc, ProgressWriter } from "./progress.js";
import { systemPayload } from "./prompt.js";
import { settleSubmission } from "./submission.js";
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

/** `after_tools`: the round's assistant entry and calls. */
export type RoundCheckpoint = {
  assistantEntryId: string;
  calls: RoundCall[];
};

/** `request` resumed after waiting on a compaction (or re-run after placing one). */
export type RequestCheckpoint = {
  /** The compaction task waited on, or null when a ready summary was placed instead. */
  compactionTaskId: string | null;
  /** True once a context overflow has been retried in this request. */
  overflowRetried: boolean;
};

export type GenerationCheckpoint = RoundCheckpoint | RequestCheckpoint | null;

/** A generation task's `done` value. */
export type GenerationValue =
  | { outcome: "answered"; entryId: string }
  | { outcome: "terminated" }
  | { outcome: "continued"; next: string }
  | { outcome: "failed"; reason: "model_error" | "compaction_failed" };

const requestCheckpoint = (cp: GenerationCheckpoint): RequestCheckpoint | null =>
  cp !== null && "overflowRetried" in cp ? cp : null;

export {
  type SubmissionAnswer,
  type SubmissionUnanswered,
  settleSubmission,
} from "./submission.js";

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

/**
 * The generation task kind. `compaction` is the compaction kind it spawns
 * (made with default settings when omitted); register it with the runtime
 * too (`compactionTaskOf(generation)`).
 */
export function generationTaskDefinition(
  deps: AgentDeps,
  toolTask: TaskDefinition<ToolTaskInput, ToolTaskCheckpoint>,
  compaction: CompactionTask = compactionTaskDefinition(deps),
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
  bindCompactionTask(def, compaction);
  return def;

  async function request(task: State, rt: Scope, ctx: Context): Promise<Committed> {
    const { runId, round, submissionId } = task.input;
    const conversationId = task.conversationId;
    const { conversation, agent } = await loadConversation(deps, conversationId);
    const specs = deps.tools.specs(agent.tools);
    const system = systemPayload(agent.systemPrompt, specs);
    const settings = resolveCompactionSettings(compactionDefaults(compaction), agent.compaction);
    // Safety net for a harness that did not register the kind (a no-op when it did).
    rt.runtime.registry.register(compaction);

    // Resumed after waiting on a compaction: a failure ends the run here.
    const resumed = requestCheckpoint(task.checkpoint);
    const overflowRetried = resumed?.overflowRetried ?? false;
    let compacted = false;
    if (resumed?.compactionTaskId) {
      const outcome = compactionOutcome(rt.task(resumed.compactionTaskId));
      if (!outcome.ok) return failRun(task, rt, outcome.error, "compaction_failed", null);
      compacted = true;
    }

    // Turn boundary: the system entry if it changed, then a waiting summary.
    // Replay-tolerant: a rerun after a crash finds both done and skips this.
    let entries = await listAllEntries(deps.session, conversationId);
    let built = buildContext(entries);
    const needSystem = built.system?.fingerprint !== system.fingerprint;
    const ready = (await deps.session.document(compactionDoc, conversationId)).ready !== null;
    if (needSystem || ready) {
      await rt.write(async (tx) => {
        if (needSystem) {
          tx.appendEntry({
            conversationId,
            taskId: task.id,
            kind: ENTRY_KINDS.system,
            payload: system as unknown as JsonValue,
          });
        }
        await placeReadyCompaction(tx, conversationId);
      });
      entries = await listAllEntries(deps.session, conversationId);
      built = buildContext(entries);
    }

    if (settings.enabled && !compacted) {
      const estimate = estimateContextTokens(entries);
      const limits = compactionThresholds(settings, estimate.model);
      const compactionInput = { runId, round, cause: "threshold" as const };
      if (estimate.tokens >= limits.blocking) {
        return rt.commit((tx, ops) =>
          waitForCompaction(tx, ops, conversationId, compactionInput, built.headEntryId, {
            compactionTaskId: null,
            overflowRetried,
          }),
        );
      }
      if (estimate.tokens > limits.background) {
        const state = await deps.session.document(compactionDoc, conversationId);
        const running = state.running !== null && rt.task(state.running)?.status !== "terminal";
        if (!running && state.ready === null) {
          await rt.write((tx, ops) =>
            ensureCompaction(
              tx,
              ops,
              compaction,
              conversationId,
              { ...compactionInput, cause: "background" },
              built.headEntryId,
            ),
          );
        }
      }
    }

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
      const error = terminal.error;
      const partial = terminal.partial;
      const spend =
        partial.provider === ""
          ? null
          : { provider: partial.provider, model: partial.model, usage: terminal.usage };
      if (error.kind === "context_overflow" && settings.enabled && !overflowRetried) {
        // One compaction, then one retry of this request.
        return rt.commit(async (tx, ops) => {
          if (spend) await recordSpend(tx, conversationId, spend);
          await clearLive(tx, conversationId, task.id);
          return waitForCompaction(
            tx,
            ops,
            conversationId,
            { runId, round, cause: "overflow" },
            built.headEntryId,
            { compactionTaskId: null, overflowRetried: true },
          );
        });
      }
      return failRun(task, rt, error, "model_error", spend);
    }

    const message = terminal.message;
    await deps.hooks.afterResponse(
      { conversation, agent, runId, round, message, usage: terminal.usage },
      task.id,
    );
    const calls = message.blocks.filter((b): b is ToolCallBlock => b.type === "tool_call");
    return rt.commit(async (tx, ops) => {
      const sub = await tx.getSubmission(submissionId);
      const inbox = await Inbox.load(tx, conversationId);
      const entryId = appendAssistant(tx, task, message, terminal.usage);
      recordUsage(
        await tx.doc(usageDoc, conversationId),
        message.provider,
        message.model,
        terminal.usage,
      );
      await clearLive(tx, conversationId, task.id);

      if (calls.length === 0) {
        const answer = { entryId, text: assistantText(message), terminated: false };
        settleSubmission(tx, sub, "done", answer);
        inbox.endRun(ops, def, runId, "done", answer);
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

  /**
   * Inside a request's commit: place a summary that is ready and re-run
   * `request` on the new head, or wait on the running compaction (starting
   * one if none runs).
   */
  async function waitForCompaction(
    tx: Transaction,
    ops: TaskOps,
    conversationId: string,
    input: { runId: string; round: number; cause: "threshold" | "overflow" },
    headEntryId: string | null,
    cp: RequestCheckpoint,
  ) {
    if ((await placeReadyCompaction(tx, conversationId)) !== null) {
      return next<GenerationCheckpoint>("request", { ...cp, compactionTaskId: null });
    }
    const id = await ensureCompaction(tx, ops, compaction, conversationId, input, headEntryId);
    if (id === null) throw new Error("a blocking compaction was not started");
    return waiting<GenerationCheckpoint>({
      on: [id],
      phase: "request",
      checkpoint: { ...cp, compactionTaskId: id },
    });
  }

  /** End the run unanswered: a `run_error` entry, the spend so far, the submission settled. */
  function failRun(
    task: State,
    rt: Scope,
    error: ModelError,
    reason: "model_error" | "compaction_failed",
    spend: Spend,
  ): Promise<Committed> {
    const { runId, submissionId } = task.input;
    const conversationId = task.conversationId;
    return rt.commit(async (tx, ops) => {
      const sub = await tx.getSubmission(submissionId);
      const inbox = await Inbox.load(tx, conversationId);
      const payload: RunErrorPayload = { runId, error };
      tx.appendEntry({
        conversationId,
        taskId: task.id,
        kind: ENTRY_KINDS.runError,
        payload: payload as unknown as JsonValue,
      });
      if (spend) await recordSpend(tx, conversationId, spend);
      await clearLive(tx, conversationId, task.id);
      const message =
        reason === "compaction_failed" ? `compaction failed: ${error.message}` : error.message;
      const unanswered = { reason, message, error: error as unknown as JsonValue };
      settleSubmission(tx, sub, "unanswered", unanswered);
      inbox.endRun(ops, def, runId, "unanswered", unanswered);
      return done({ outcome: "failed", reason } satisfies GenerationValue);
    });
  }

  async function afterTools(task: State, rt: Scope): Promise<Committed> {
    const { runId, round, submissionId } = task.input;
    const conversationId = task.conversationId;
    const cp = task.checkpoint;
    if (cp === null || !("calls" in cp)) throw new Error("after_tools without a round checkpoint");

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
      const inbox = await Inbox.load(tx, conversationId);
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
        const answer = { entryId: null, text: "", terminated: true };
        settleSubmission(tx, sub, "done", answer);
        inbox.endRun(ops, def, runId, "done", answer);
        return done({ outcome: "terminated" } satisfies GenerationValue);
      }
      const nextTask = ops.spawn(def, {
        input: { runId, submissionId, round: round + 1 },
        parentTaskId: null,
      });
      // Steers queued during this round land here: after the round's tool
      // results, before the next request, in the same run.
      inbox.advance(runId, nextTask.id);
      inbox.placeSteers(runId, nextTask.id);
      if (sub && (sub.status === "active" || sub.status === "pending")) {
        const { seq: _seq, ...rec } = sub;
        tx.replaceSubmission({ ...rec, taskId: nextTask.id });
      }
      return done({ outcome: "continued", next: nextTask.id } satisfies GenerationValue);
    });
  }
}

type Spend = { provider: string; model: string; usage: Usage } | null;

async function recordSpend(
  tx: Transaction,
  conversationId: string,
  spend: NonNullable<Spend>,
): Promise<void> {
  recordUsage(await tx.doc(usageDoc, conversationId), spend.provider, spend.model, spend.usage);
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
