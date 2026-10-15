/**
 * The tool task (R6, R8, R9, R20): one durable task per tool call, owned by
 * the generation task that received the call.
 *
 * Phases:
 * - `start`: wait for earlier calls this one is ordered after (`sequential`
 *   tools), check the tool is registered and enabled, validate the
 *   arguments, run the `beforeTool` hooks (which may rewrite them),
 *   re-validate, then ask `HostPolicy.authorizeTool` about the final
 *   arguments. `block` (or a throwing policy) commits a `blocked` result;
 *   `allow` commits the intent: a transition to `execute` whose checkpoint
 *   holds the final arguments. `requireApproval` commits an approval request
 *   together with `waiting({ signal: "approval", until: expiresAt })`, to
 *   resume in `approved` with the final arguments as the checkpoint.
 * - `approved` (U9, R10): woken by `decideApproval`'s signal or by the
 *   deadline. `approve` commits the intent exactly as `allow` does; `deny`
 *   commits a `denied` result; the deadline commits `approval_expired` and
 *   closes the request. The wait holds no running task.
 * - `execute`: run the tool, stream its partial output (throttled) to the
 *   task's `agent.tool_output` document, run the `afterTool` hooks and commit
 *   the `tool_result` entry together with the task's `done`.
 *
 * Recovery: entering `execute` without having committed the intent in this
 * process means a previous process died between intent and result. A
 * replay-safe tool runs again (same idempotency key, next attempt number).
 * Otherwise the tool's `reconcile` may supply the stored outcome; failing
 * that the model gets an `interrupted` result that warns the effect may have
 * happened and carries the output committed so far. The tool is not run
 * again.
 *
 * A subagent tool (`subagents.ts`) leaves `start` / `approved` for the
 * `subagent` phase instead of `execute`: it runs a child conversation the
 * task owns and waits for its answer without a running invocation.
 *
 * Result statuses: a failed result whose `details.error` is `timeout` or
 * `lease_lost` (the tool gateway's failure codes) gets that status; any other
 * failure is `error`.
 *
 * A task aborted while awaiting approval closes its request as `cancelled`.
 */
import type { Clock } from "../core/clock.js";
import type { Context } from "../core/context.js";
import { errorText } from "../core/errors.js";
import { type JsonObject, jsonByteLength } from "../core/json.js";
import type { EngineLimits } from "../core/limits.js";
import type { ModelRegistry } from "../models/stream.js";
import type { Session } from "../session/session.js";
import type { Transaction } from "../session/transaction.js";
import { type EntryRecord, MAX_PAGE_LIMIT } from "../storage/types.js";
import {
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  type Transition,
  waiting,
} from "../tasks/define.js";
import type {
  ToolApi,
  ToolConversation,
  ToolDefinition,
  ToolReconcileApi,
  ToolRegistry,
  ToolResult,
} from "../tools/registry.js";
import {
  APPROVAL_SIGNAL,
  approvalOf,
  approvalsDoc,
  closeApproval,
  decisionFromSignal,
  requestApproval,
  resultApproval,
} from "./approvals.js";
import {
  ENTRY_KINDS,
  type ToolResultPayload,
  type ToolResultStatus,
  toEntryPayload,
} from "./context-builder.js";
import { type AgentErrorReporter, type HookRunner, isToolResult } from "./hooks.js";
import { authorize, type HostPolicy } from "./policy.js";
import {
  appendOutput,
  ProgressWriter,
  type ToolOutputState,
  toolOutputDoc,
  toolOutputIntervalMs,
} from "./progress.js";
import { type AgentConfig, agentConfigDoc } from "./prompt.js";
import { isSubagentTool, SUBAGENT_PHASE, type SubagentLink, subagentPhase } from "./subagents.js";

export const TOOL_TASK_KIND = "agent.tool";

/** Everything the agent's task handlers need from the harness. */
export interface AgentDeps {
  session: Session;
  models: ModelRegistry;
  tools: ToolRegistry;
  policy: HostPolicy;
  hooks: HookRunner;
  clock: Clock;
  limits: Readonly<EngineLimits>;
  progressIntervalMs: number;
  report: AgentErrorReporter;
  /**
   * Tool tasks whose intent this process committed and whose `execute` phase
   * has not started yet. Process memory on purpose: after a restart it is
   * empty, which is how `execute` recognises an interrupted call.
   */
  freshIntents: Set<string>;
}

export type ToolCallInput = {
  id: string;
  name: string;
  arguments: JsonObject;
  /** Raw argument text when the model's arguments were not a JSON object. */
  invalidArguments: string | null;
};

export type ToolTaskInput = {
  runId: string;
  call: ToolCallInput;
  /** Tool tasks of the same round that must settle before this call starts. */
  after: string[];
};

/**
 * The intent: the final, authorized arguments. A subagent call also records
 * its child conversation and submission once it has created them.
 */
export type ToolTaskCheckpoint = { arguments: JsonObject; subagent?: SubagentLink } | null;

/** A tool task's `done` value. */
export type ToolTaskValue = {
  entryId: string;
  status: ToolResultStatus;
  terminate: boolean;
};

export const INTERRUPTED_WARNING =
  "The tool was interrupted by a restart before it reported a result. Its effect may have happened; check before retrying.";

/**
 * The result for a call whose effect may have happened but whose outcome is
 * unknown: the standard warning plus the output committed so far.
 */
export function interruptedResult(stored: Pick<ToolOutputState, "text" | "truncated">): ToolResult {
  const partial = stored.text;
  const content =
    partial.length > 0
      ? `${INTERRUPTED_WARNING}\n\nOutput before the interruption${stored.truncated ? " (truncated)" : ""}:\n${partial}`
      : INTERRUPTED_WARNING;
  return {
    content,
    isError: true,
    details: { warning: INTERRUPTED_WARNING, partialOutput: partial, truncated: stored.truncated },
  };
}

export async function loadConversation(
  deps: Pick<AgentDeps, "session">,
  conversationId: string,
): Promise<{ conversation: ToolConversation; agent: AgentConfig }> {
  const rec = await deps.session.reader.getConversation(conversationId);
  if (!rec) throw new Error(`unknown conversation ${conversationId}`);
  const agent = await deps.session.document(agentConfigDoc, conversationId);
  const rootId = await rootConversationId(deps.session, rec);
  return {
    conversation: {
      id: rec.id,
      tenantId: rec.tenantId,
      projectId: rec.projectId,
      ...(rootId !== rec.id ? { rootId } : {}),
    },
    agent,
  };
}

/**
 * The top-level conversation above `rec`: follow each owning task to its
 * conversation until a conversation has no owner. A broken or cyclic chain
 * stops at the last conversation reached.
 */
async function rootConversationId(
  session: AgentDeps["session"],
  rec: { id: string; ownerTaskId: string | null },
): Promise<string> {
  let id = rec.id;
  let owner = rec.ownerTaskId;
  const seen = new Set([id]);
  while (owner !== null) {
    const task = await session.reader.getTask(owner);
    if (!task || seen.has(task.conversationId)) break;
    id = task.conversationId;
    seen.add(id);
    const parent = await session.reader.getConversation(id);
    owner = parent ? parent.ownerTaskId : null;
  }
  return id;
}

export async function listAllEntries(
  session: Session,
  conversationId: string,
): Promise<EntryRecord[]> {
  const out: EntryRecord[] = [];
  for (let cursor: string | null = null; ; ) {
    const page = await session.reader.listEntries(conversationId, {
      limit: MAX_PAGE_LIMIT,
      cursor,
    });
    out.push(...page.items);
    cursor = page.nextCursor;
    if (cursor === null) return out;
  }
}

/** Append a `tool_result` entry; returns its id. */
export function appendToolResult(
  tx: Transaction,
  conversationId: string,
  taskId: string | null,
  payload: ToolResultPayload,
): string {
  return tx.appendEntry({
    conversationId,
    taskId,
    kind: ENTRY_KINDS.toolResult,
    payload: toEntryPayload(ENTRY_KINDS.toolResult, payload),
  }).id;
}

export function resultPayload(
  runId: string,
  call: Pick<ToolCallInput, "id" | "name">,
  status: ToolResultStatus,
  result: ToolResult,
  attempts: number,
): ToolResultPayload {
  return {
    runId,
    toolCallId: call.id,
    toolName: call.name,
    status,
    content: structuredClone(result.content),
    isError: result.isError ?? status !== "ok",
    details: result.details === undefined ? null : structuredClone(result.details),
    terminate: result.terminate === true,
    attempts,
  };
}

/** Gateway failure codes (`details.error`) with their own result status. */
const FAILURE_STATUSES: ReadonlySet<string> = new Set<ToolResultStatus>(["timeout", "lease_lost"]);

/** The status of a tool's own result: `ok`, a gateway failure status, or `error`. */
export function resultStatus(result: ToolResult): ToolResultStatus {
  if (!result.isError) return "ok";
  const d = result.details;
  const code =
    typeof d === "object" && d !== null && !Array.isArray(d) ? (d as JsonObject).error : undefined;
  return typeof code === "string" && FAILURE_STATUSES.has(code)
    ? (code as ToolResultStatus)
    : "error";
}

/**
 * Inside a tool task's commit: append its `tool_result` entry (carrying the
 * approval when the call needed one) and return the task's `done`.
 */
export async function writeToolResult(
  tx: Transaction,
  task: TaskState<ToolTaskInput, ToolTaskCheckpoint>,
  status: ToolResultStatus,
  result: ToolResult,
  attempts: number,
): Promise<Transition<ToolTaskCheckpoint>> {
  const payload = resultPayload(task.input.runId, task.input.call, status, result, attempts);
  if (task.phase === "approved" || Object.hasOwn(task.signals, APPROVAL_SIGNAL)) {
    const item = await approvalOf(tx, task.conversationId, task.id);
    if (item && item.status !== "pending" && item.status !== "cancelled") {
      payload.approval = resultApproval(item, tx.now);
    }
  }
  const entryId = appendToolResult(tx, task.conversationId, task.id, payload);
  const value: ToolTaskValue = { entryId, status, terminate: payload.terminate };
  return done(value);
}

/**
 * Bytes kept free for the entry record around the payload (ids, timestamps,
 * the approval summary, the truncation marker).
 */
const ENTRY_ENVELOPE_BYTES = 2048;

export const TRUNCATED_RESULT_MARKER = "[Tool result truncated to fit the entry size limit";

/**
 * Fit a tool result into one `tool_result` entry of at most `maxEntryBytes`:
 * oversized `details` are replaced by a note and oversized content is cut
 * (head kept) with a {@link TRUNCATED_RESULT_MARKER} line. A result that
 * already fits is returned unchanged. Without this an oversized result would
 * fault the commit after the tool's effect had happened.
 */
export function capToolResult(
  call: Pick<ToolCallInput, "id" | "name">,
  result: ToolResult,
  maxEntryBytes: number,
): ToolResult {
  const measure = (r: ToolResult) => jsonByteLength(resultPayload("", call, "ok", r, 0));
  const budget = Math.max(0, maxEntryBytes - ENTRY_ENVELOPE_BYTES);
  if (measure(result) <= budget) return result;

  let capped: ToolResult = { ...result };
  if (measure({ ...capped, content: "" }) > budget / 2) {
    capped.details = { truncated: true, note: "details dropped: too large for one entry" };
  }
  const text =
    typeof capped.content === "string"
      ? capped.content
      : capped.content.map((c) => c.text).join("\n");
  const marker = (kept: number) =>
    `\n\n${TRUNCATED_RESULT_MARKER}: kept ${kept} of ${text.length} characters.]`;
  // Shrink until it fits; JSON escaping makes bytes per character vary.
  let keep = text.length;
  for (let i = 0; i < 32 && keep > 0; i++) {
    const candidate = { ...capped, content: text.slice(0, keep) + marker(keep) };
    const size = measure(candidate);
    if (size <= budget) return candidate;
    keep = Math.max(0, Math.floor(keep * (budget / size) * 0.95) - 16);
  }
  capped = { ...capped, content: marker(0).trimStart() };
  return capped;
}

function issuesText(issues: ReadonlyArray<{ path: PropertyKey[]; message: string }>): string {
  return issues.map((i) => `${i.path.map(String).join(".") || "(root)"}: ${i.message}`).join("; ");
}

type Scope = TaskScope<ToolTaskCheckpoint>;
type State = TaskState<ToolTaskInput, ToolTaskCheckpoint>;

export function toolTaskDefinition(
  deps: AgentDeps,
): TaskDefinition<ToolTaskInput, ToolTaskCheckpoint> {
  /** Commit the result entry and finish. */
  const finish = (
    task: State,
    rt: Scope,
    status: ToolResultStatus,
    result: ToolResult,
    attempts: number,
    output?: ToolOutputState,
    prepare?: (tx: Transaction) => Promise<unknown>,
  ): Promise<Committed> =>
    rt.commit(async (tx) => {
      if (prepare) await prepare(tx);
      if (output) (await tx.doc(toolOutputDoc, task.id)).set(output);
      const capped = capToolResult(task.input.call, result, deps.limits.maxEntryBytes);
      return writeToolResult(tx, task, status, capped, attempts);
    });

  const fail = (task: State, rt: Scope, status: ToolResultStatus, message: string) =>
    finish(task, rt, status, { content: message, isError: true }, 0);

  return defineTask<ToolTaskInput, ToolTaskCheckpoint>({
    kind: TOOL_TASK_KIND,
    phases: {
      start: async (task, rt) => {
        const pending = task.input.after.filter((id) => rt.task(id)?.status !== "terminal");
        if (pending.length > 0) return rt.commit(() => waiting({ on: pending, phase: "start" }));

        const { call, runId } = task.input;
        const { conversation, agent } = await loadConversation(deps, task.conversationId);
        const tool = deps.tools.get(call.name);
        if (!tool || !agent.tools.includes(call.name)) {
          return fail(task, rt, "unknown_tool", `Unknown tool "${call.name}".`);
        }
        if (call.invalidArguments !== null) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: not a JSON object.`,
          );
        }
        const first = tool.parameters.safeParse(call.arguments);
        if (!first.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: ${issuesText(first.error.issues)}`,
          );
        }
        const args = await deps.hooks.beforeTool(
          {
            conversation,
            agent,
            runId,
            toolCallId: call.id,
            toolName: call.name,
            arguments: call.arguments,
          },
          task.id,
        );
        const final = tool.parameters.safeParse(args);
        if (!final.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name} after hooks: ${issuesText(final.error.issues)}`,
          );
        }
        // Host policy: last, on the final arguments, not overridable.
        const verdict = await authorize(deps.policy, {
          conversation,
          agent,
          runId,
          toolCallId: call.id,
          toolName: call.name,
          arguments: args,
          replay: tool.replay,
        });
        switch (verdict.decision) {
          case "block":
            return fail(task, rt, "blocked", `Blocked by policy: ${verdict.reason}`);
          case "requireApproval": {
            const expiresAt = verdict.expiresAt ?? null;
            return rt.commit(async (tx) => {
              await requestApproval(tx, {
                conversationId: task.conversationId,
                toolTaskId: task.id,
                runId,
                toolCallId: call.id,
                toolName: call.name,
                arguments: args,
                reason: verdict.reason ?? null,
                expiresAt,
              });
              return waiting({
                signal: APPROVAL_SIGNAL,
                until: expiresAt,
                phase: "approved",
                checkpoint: { arguments: args },
              });
            });
          }
          case "allow": {
            if (isSubagentTool(tool)) {
              return rt.commit(() => next(SUBAGENT_PHASE, { arguments: args }));
            }
            const intent = await rt.commit(() => next("execute", { arguments: args }));
            deps.freshIntents.add(task.id);
            return intent;
          }
        }
      },

      approved: async (task, rt) => {
        const decision = decisionFromSignal(task.signals[APPROVAL_SIGNAL]);
        if (decision === null) {
          if (task.wake?.reason !== "deadline") {
            // Woken without a decision or deadline: keep waiting.
            const doc = await deps.session.document(approvalsDoc, task.conversationId);
            const own = doc.items.find((i) => i.toolTaskId === task.id);
            return rt.commit(() =>
              waiting({
                signal: APPROVAL_SIGNAL,
                until: own?.expiresAt ?? null,
                phase: "approved",
              }),
            );
          }
          return finish(
            task,
            rt,
            "approval_expired",
            {
              content:
                "The approval this call required expired before a decision. The tool did not run.",
              isError: true,
            },
            0,
            undefined,
            (tx) => closeApproval(tx, task.conversationId, task.id, "expired"),
          );
        }
        if (decision.decision === "deny") {
          return finish(
            task,
            rt,
            "denied",
            {
              content: `The call was denied by a human${decision.note ? `: ${decision.note}` : "."} The tool did not run.`,
              isError: true,
            },
            0,
          );
        }
        const approvedArgs = task.checkpoint?.arguments ?? {};
        if (isSubagentTool(deps.tools.get(task.input.call.name))) {
          return rt.commit(() => next(SUBAGENT_PHASE, { arguments: approvedArgs }));
        }
        const intent = await rt.commit(() => next("execute", { arguments: approvedArgs }));
        deps.freshIntents.add(task.id);
        return intent;
      },

      execute: async (task, rt, ctx) => {
        const fresh = deps.freshIntents.delete(task.id);
        const { call } = task.input;
        const args = task.checkpoint?.arguments ?? {};
        const tool = deps.tools.get(call.name);
        if (!tool) return fail(task, rt, "error", `Tool "${call.name}" is no longer registered.`);
        const parsed = tool.parameters.safeParse(args);
        if (!parsed.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: ${issuesText(parsed.error.issues)}`,
          );
        }
        const { conversation, agent } = await loadConversation(deps, task.conversationId);
        const stored = await deps.session.document(toolOutputDoc, task.id);
        let attempt = 1;
        if (!fresh) {
          if (tool.replay !== "safe")
            return recover(task, rt, ctx, tool, parsed.data, conversation, stored);
          attempt = stored.attempt + 1;
          await rt.write(async (tx) => {
            (await tx.doc(toolOutputDoc, task.id)).set({
              text: "",
              truncated: false,
              attempt,
              toolCallId: call.id,
              toolName: call.name,
            });
          });
        }
        return run(task, rt, ctx, tool, parsed.data, args, conversation, agent, attempt);
      },

      [SUBAGENT_PHASE]: (task, rt) => subagentPhase(deps, task, rt, writeToolResult),
    },
    onAbort: async (task, scope) => {
      if (task.phase !== "approved") return;
      await scope.commit(async (tx) => {
        await closeApproval(tx, task.conversationId, task.id, "cancelled");
      });
    },
  });

  async function recover(
    task: State,
    rt: Scope,
    ctx: Context,
    tool: ToolDefinition,
    args: unknown,
    conversation: ToolConversation,
    stored: ToolOutputState,
  ): Promise<Committed> {
    const { call, runId } = task.input;
    if (tool.reconcile) {
      const api: ToolReconcileApi = {
        ctx,
        idempotencyKey: `${runId}:${call.id}`,
        runId,
        toolCallId: call.id,
        taskId: task.id,
        conversation,
        partialOutput: stored.text,
      };
      try {
        const outcome = await tool.reconcile(args, api);
        if (outcome !== null && isToolResult(outcome)) {
          return finish(task, rt, resultStatus(outcome), outcome, stored.attempt);
        }
      } catch (err) {
        ctx.throwIfCancelled();
        deps.report(err, {
          during: "reconcile",
          taskId: task.id,
          conversationId: task.conversationId,
        });
      }
    }
    ctx.throwIfCancelled();
    return finish(task, rt, "interrupted", interruptedResult(stored), stored.attempt);
  }

  async function run(
    task: State,
    rt: Scope,
    ctx: Context,
    tool: ToolDefinition,
    parsedArgs: unknown,
    args: JsonObject,
    conversation: ToolConversation,
    agent: AgentConfig,
    attempt: number,
  ): Promise<Committed> {
    const { call, runId } = task.input;
    let output: ToolOutputState = {
      text: "",
      truncated: false,
      attempt,
      toolCallId: call.id,
      toolName: call.name,
    };
    let touched = false;
    const writer = new ProgressWriter(
      deps.clock,
      // Large snapshots are rewritten whole on every write: write them less often.
      () => toolOutputIntervalMs(deps.progressIntervalMs, output.text.length),
      async () => {
        const snapshot = output;
        await rt.write(async (tx) => {
          (await tx.doc(toolOutputDoc, task.id)).set(snapshot);
        });
      },
      (err) => {
        if (!ctx.cancelled) {
          deps.report(err, {
            during: "progress",
            taskId: task.id,
            conversationId: task.conversationId,
          });
        }
      },
    );
    const api: ToolApi = {
      ctx,
      signal: ctx.signal,
      idempotencyKey: `${runId}:${call.id}`,
      runId,
      toolCallId: call.id,
      taskId: task.id,
      attempt,
      conversation,
      output(text: string) {
        if (typeof text !== "string" || text.length === 0) return;
        output = appendOutput(output, text);
        touched = true;
        writer.mark();
      },
    };

    let result: ToolResult;
    let failed = false;
    try {
      const value = await tool.execute(parsedArgs, api);
      if (isToolResult(value)) {
        result = value;
      } else {
        result = { content: `Tool ${call.name} returned an invalid result.`, isError: true };
        failed = true;
      }
    } catch (err) {
      ctx.throwIfCancelled();
      result = { content: `Tool ${call.name} failed: ${errorText(err)}`, isError: true };
      failed = true;
    } finally {
      await writer.close();
    }
    ctx.throwIfCancelled();
    result = await deps.hooks.afterTool(
      {
        conversation,
        agent,
        runId,
        toolCallId: call.id,
        toolName: call.name,
        arguments: args,
        result,
      },
      task.id,
    );
    const status: ToolResultStatus = failed ? "error" : resultStatus(result);
    return finish(task, rt, status, result, attempt, touched ? output : undefined);
  }
}
