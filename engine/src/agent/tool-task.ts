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
 *   holds the final arguments.
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
 * Approvals (U9) slot in between authorization and intent: `requireApproval`
 * will commit an approval record and `waiting({ signal: "approval" })`, and
 * an approval resumes into the same intent commit. Until then the call gets
 * an `approval_unsupported` result.
 */
import type { Clock } from "../core/clock.js";
import type { Context } from "../core/context.js";
import type { JsonObject, JsonValue } from "../core/json.js";
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
import { ENTRY_KINDS, type ToolResultPayload, type ToolResultStatus } from "./context-builder.js";
import { type AgentErrorReporter, type HookRunner, isToolResult } from "./hooks.js";
import { authorize, type HostPolicy } from "./policy.js";
import { appendOutput, ProgressWriter, type ToolOutputState, toolOutputDoc } from "./progress.js";
import { type AgentConfig, agentConfigDoc } from "./prompt.js";

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

/** The intent: the final, authorized arguments. */
export type ToolTaskCheckpoint = { arguments: JsonObject } | null;

/** A tool task's `done` value. */
export type ToolTaskValue = {
  entryId: string;
  status: ToolResultStatus;
  terminate: boolean;
};

export const INTERRUPTED_WARNING =
  "The tool was interrupted by a restart before it reported a result. Its effect may have happened; check before retrying.";

export async function loadConversation(
  deps: Pick<AgentDeps, "session">,
  conversationId: string,
): Promise<{ conversation: ToolConversation; agent: AgentConfig }> {
  const rec = await deps.session.reader.getConversation(conversationId);
  if (!rec) throw new Error(`unknown conversation ${conversationId}`);
  const agent = await deps.session.document(agentConfigDoc, conversationId);
  return {
    conversation: { id: rec.id, tenantId: rec.tenantId, projectId: rec.projectId },
    agent,
  };
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
    payload: payload as unknown as JsonValue,
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

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err));

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
  ): Promise<Committed> =>
    rt.commit(async (tx) => {
      if (output) (await tx.doc(toolOutputDoc, task.id)).set(output);
      const payload = resultPayload(task.input.runId, task.input.call, status, result, attempts);
      const entryId = appendToolResult(tx, task.conversationId, task.id, payload);
      const value: ToolTaskValue = { entryId, status, terminate: payload.terminate };
      return done(value);
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
          case "requireApproval":
            // Replaced by a durable approval wait in U9.
            return finish(
              task,
              rt,
              "approval_unsupported",
              {
                content: `This call requires approval${verdict.reason ? ` (${verdict.reason})` : ""}, which is not available here.`,
                isError: true,
                details: { reason: verdict.reason ?? null, expiresAt: verdict.expiresAt ?? null },
              },
              0,
            );
          case "allow": {
            const intent = await rt.commit(() => next("execute", { arguments: args }));
            deps.freshIntents.add(task.id);
            return intent;
          }
        }
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
            (await tx.doc(toolOutputDoc, task.id)).set({ text: "", truncated: false, attempt });
          });
        }
        return run(task, rt, ctx, tool, parsed.data, args, conversation, agent, attempt);
      },
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
          return finish(task, rt, outcome.isError ? "error" : "ok", outcome, stored.attempt);
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
    const partial = stored.text;
    const content =
      partial.length > 0
        ? `${INTERRUPTED_WARNING}\n\nOutput before the interruption${stored.truncated ? " (truncated)" : ""}:\n${partial}`
        : INTERRUPTED_WARNING;
    return finish(
      task,
      rt,
      "interrupted",
      {
        content,
        isError: true,
        details: {
          warning: INTERRUPTED_WARNING,
          partialOutput: partial,
          truncated: stored.truncated,
        },
      },
      stored.attempt,
    );
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
    let output: ToolOutputState = { text: "", truncated: false, attempt };
    let touched = false;
    const writer = new ProgressWriter(
      deps.clock,
      deps.progressIntervalMs,
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
    let status: ToolResultStatus;
    try {
      const value = await tool.execute(parsedArgs, api);
      if (isToolResult(value)) {
        result = value;
        status = value.isError ? "error" : "ok";
      } else {
        result = { content: `Tool ${call.name} returned an invalid result.`, isError: true };
        status = "error";
      }
    } catch (err) {
      ctx.throwIfCancelled();
      result = { content: `Tool ${call.name} failed: ${errorText(err)}`, isError: true };
      status = "error";
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
    if (status === "ok" && result.isError) status = "error";
    return finish(task, rt, status, result, attempt, touched ? output : undefined);
  }
}
